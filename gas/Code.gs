// ============================================================
// Cable BOM & Costing System — Google Apps Script
// Deployed as a Web App → serves as Telegram Webhook
// NO server needed. Runs entirely on Google's infrastructure.
// ============================================================

// ── CONFIG — Edit these values ───────────────────────────────
var ANTHROPIC_API_KEY  = PropertiesService.getScriptProperties().getProperty("ANTHROPIC_API_KEY");
var TELEGRAM_BOT_TOKEN = PropertiesService.getScriptProperties().getProperty("TELEGRAM_BOT_TOKEN");
var CLAUDE_MODEL       = "claude-sonnet-4-6";
var COMPANY_NAME       = PropertiesService.getScriptProperties().getProperty("COMPANY_NAME") || "Your Company";
// ─────────────────────────────────────────────────────────────


// ── Telegram Webhook Entry Point ─────────────────────────────
function doPost(e) {
  try {
    var update = JSON.parse(e.postData.contents);
    handleUpdate(update);
  } catch (err) {
    Logger.log("doPost error: " + err);
  }
  return ContentService.createTextOutput("OK");
}

function handleUpdate(update) {
  var msg = update.message;
  if (!msg) return;

  var chatId  = msg.chat.id;
  var text    = msg.text || "";
  var doc     = msg.document;

  // Commands
  if (text === "/start") {
    sendTelegramMessage(chatId,
      "👋 *" + COMPANY_NAME + " BOM Bot*\n\n" +
      "Send me a GTP PDF and I'll calculate the BOM and pricing.\n\n" +
      "Commands:\n/prices — current RM prices\n/help — usage guide"
    );
    return;
  }

  if (text === "/prices") {
    sendPricesList(chatId);
    return;
  }

  if (text === "/help") {
    sendTelegramMessage(chatId,
      "*How to use*\n\n" +
      "1. Send any GTP PDF (e.g. GTP-2023-A.pdf)\n" +
      "   • GTP type A/B/C is read from the filename suffix\n" +
      "2. I will:\n" +
      "   • Parse all cable specs from the PDF\n" +
      "   • Calculate Costing BOM and Production BOM\n" +
      "   • Calculate floor price and selling price\n" +
      "   • Send you a pricing summary\n\n" +
      "/prices — view current RM prices\n" +
      "/help — this message"
    );
    return;
  }

  // PDF document
  if (doc && doc.file_name && doc.file_name.toLowerCase().endsWith(".pdf")) {
    sendTelegramMessage(chatId, "📄 Received: *" + doc.file_name + "*\nProcessing... please wait (1-2 min).");
    processPdf(chatId, doc);
    return;
  }

  sendTelegramMessage(chatId, "Please send a GTP PDF file to get started. Type /help for instructions.");
}


// ── Main Pipeline ─────────────────────────────────────────────
function processPdf(chatId, doc) {
  try {
    // 1. Download PDF from Telegram as base64
    var pdfBase64 = downloadTelegramFile(doc.file_id);

    // 2. Parse GTP via Claude API (sends PDF directly)
    var gtpData = parseGtpWithClaude(pdfBase64, doc.file_name);

    if (!gtpData || !gtpData.cables || gtpData.cables.length === 0) {
      sendTelegramMessage(chatId, "❌ Could not extract cable data from the PDF. Please check the file.");
      return;
    }

    // 3. Load config from Sheets
    var ss = SpreadsheetApp.openById(getSpreadsheetId());
    var config = loadConfig(ss);

    // 4. Calculate BOM for each cable (costing + production)
    var bomResults = [];
    for (var i = 0; i < gtpData.cables.length; i++) {
      var cable    = gtpData.cables[i];
      var gtpType  = gtpData.gtp_type || "A";
      var bomCost  = buildBom(cable, "costing",    gtpType, config);
      var bomProd  = buildBom(cable, "production", gtpType, config);
      bomResults.push({ cable: cable, bom_costing: bomCost, bom_production: bomProd });
    }

    // 5. Write BOM to Sheets
    writeBomToSheet(ss, gtpData, bomResults);

    // 6. Calculate costing
    var costResults = calcCosting(gtpData, bomResults, config);

    // 7. Write costing to Sheets
    writeCostingToSheet(ss, gtpData, costResults);

    // 8. Reply on Telegram
    var summary = buildTelegramSummary(gtpData, costResults);
    sendTelegramMessage(chatId, summary);

  } catch (err) {
    Logger.log("Pipeline error: " + err + "\n" + err.stack);
    sendTelegramMessage(chatId, "❌ Error processing GTP:\n" + err.toString());
  }
}


// ── Claude API: Parse GTP PDF ─────────────────────────────────
function parseGtpWithClaude(pdfBase64, fileName) {
  var gtpType = null;
  var base = fileName.replace(/\.pdf$/i, "").toUpperCase();
  if (base.endsWith("A")) gtpType = "A";
  else if (base.endsWith("B")) gtpType = "B";
  else if (base.endsWith("C")) gtpType = "C";

  var prompt = buildGtpParsePrompt();

  var payload = {
    model: CLAUDE_MODEL,
    max_tokens: 8192,
    system: "You are a cable engineering expert. Return only valid JSON with no prose.",
    messages: [{
      role: "user",
      content: [
        {
          type: "document",
          source: {
            type: "base64",
            media_type: "application/pdf",
            data: pdfBase64
          }
        },
        {
          type: "text",
          text: prompt
        }
      ]
    }]
  };

  var response = UrlFetchApp.fetch("https://api.anthropic.com/v1/messages", {
    method: "post",
    headers: {
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      "anthropic-beta": "pdfs-2024-09-25",
      "content-type": "application/json"
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  var result = JSON.parse(response.getContentText());
  if (!result.content || !result.content[0]) {
    throw new Error("Claude API error: " + response.getContentText());
  }

  var text = result.content[0].text.trim();
  if (text.startsWith("```")) {
    var lines = text.split("\n");
    text = lines.slice(1, lines.length - 1).join("\n");
  }

  var gtpData = JSON.parse(text);
  if (gtpType) gtpData.gtp_type = gtpType;
  return gtpData;
}

function buildGtpParsePrompt() {
  return 'Extract ALL cable specifications from this GTP document.\n\nReturn a JSON object:\n{\n  "gtp_ref": "document reference number",\n  "customer": "customer name",\n  "project": "project name",\n  "date": "date string",\n  "gtp_type": null,\n  "cables": [\n    {\n      "item_no": 1,\n      "designation": "e.g. A2XY-FRLSH",\n      "config": "e.g. 3.5C x 70mm2",\n      "num_cores": 3,\n      "conductor_area_mm2": 70.0,\n      "voltage_kv": "1.1",\n      "standard": "IS 7098-1",\n      "conductor_material": "aluminium",\n      "conductor_shape": "round",\n      "num_wires": 19,\n      "wire_dia_mm": null,\n      "conductor_od_mm": 10.5,\n      "dc_resistance_ohm_per_km": 0.443,\n      "layers": [\n        {\n          "layer_name": "XLPE Insulation",\n          "material_key": "xlpe_insulation",\n          "nominal_thickness_mm": 3.5,\n          "thickness_type": "Nominal",\n          "od_mm": 17.5,\n          "armour_strip_width_mm": null,\n          "armour_strip_thickness_mm": null,\n          "tape_overlap_pct": null,\n          "tape_thickness_mm": null\n        }\n      ],\n      "overall_od_mm": 32.0,\n      "delivery_length_m": 1000,\n      "drum_type": "wooden"\n    }\n  ]\n}\n\nmaterial_key must be one of: xlpe_insulation, pvc_insulation, conductor_screen, insulation_screen, copper_tape_screen, bedding, gs_flat_strip_armour, frlsh_outer_sheath, pvc_outer_sheath\nExtract EVERY cable. Use null for missing values. Return ONLY valid JSON.';
}


// ── BOM Calculation ───────────────────────────────────────────
var RHO = { copper: 1/58, aluminium: 1/35 };

function buildBom(cable, bomType, gtpType, config) {
  var rows = [];
  var typeFactor  = getGtpTypeFactor(config, cable.designation, gtpType);
  var numCores    = Math.ceil(cable.num_cores || 1);
  var numWires    = cable.num_wires || 7;

  // 1. Conductor
  var condLayFactor  = getLayFactor(config, "conductor", numWires, bomType);
  var rdc  = cable.dc_resistance_ohm_per_km || 0;
  var rho  = RHO[cable.conductor_material] || RHO.copper;
  var area = rdc > 0 ? (rho / (rdc / 1000)) * typeFactor.conductor_resistance_factor : (cable.conductor_area_mm2 || 0);
  var density = getDensity(config, cable.conductor_material === "aluminium" ? "aluminium_conductor" : "copper_conductor", bomType);
  rows.push({
    layer: "Conductor",
    material: cable.conductor_material + "_conductor",
    effective_area_mm2: round4(area),
    lay_factor: condLayFactor,
    density_g_cm3: density,
    weight_kg_per_km: round3(area * density * condLayFactor * numCores)
  });

  // 2. Subsequent layers from GTP
  var currentOd = cable.conductor_od_mm || 0;
  var layers = cable.layers || [];
  for (var i = 0; i < layers.length; i++) {
    var layer = layers[i];
    var matKey = layer.material_key;
    if (matKey === "conductor") continue;

    if (matKey === "gs_flat_strip_armour") {
      var sw = layer.armour_strip_width_mm || 0;
      var st = layer.armour_strip_thickness_mm || (typeFactor.strip_thickness || 0.8);
      if (sw > 0 && st > 0) {
        var gap = 1.0;
        var nStrips = Math.round(Math.PI * (currentOd + st) / (sw + gap));
        var armDensity = getDensity(config, "gs_flat_strip_armour", bomType);
        var armLay = getLayFactor(config, "armour", 0, bomType);
        rows.push({
          layer: layer.layer_name || "GS Flat Strip Armour",
          material: "gs_flat_strip_armour",
          num_strips: nStrips,
          strip_width_mm: sw,
          strip_thickness_mm: st,
          density_g_cm3: armDensity,
          lay_factor: armLay,
          weight_kg_per_km: round3(nStrips * sw * st * armDensity * armLay * typeFactor.armour_coverage)
        });
        currentOd += 2 * st;
      }

    } else if (matKey === "copper_tape_screen") {
      var tapeT  = layer.tape_thickness_mm || 0.1;
      var overl  = layer.tape_overlap_pct  || 15;
      var meanOd = currentOd + tapeT;
      var tapeDensity = getDensity(config, "copper_tape_screen", bomType);
      rows.push({
        layer: layer.layer_name || "Copper Tape Screen",
        material: "copper_tape_screen",
        mean_od_mm: meanOd,
        tape_thickness_mm: tapeT,
        overlap_pct: overl,
        density_g_cm3: tapeDensity,
        weight_kg_per_km: round3(Math.PI * meanOd * (1 + overl/100) * tapeT * tapeDensity * numCores)
      });
      currentOd += 2 * tapeT;

    } else {
      var t = layer.nominal_thickness_mm;
      if (t == null) continue;
      var tType    = layer.thickness_type || "Nominal";
      var tolFact  = getTolFactor(config, t, tType, bomType);
      var effT     = t * tolFact;
      var od       = currentOd + 2 * effT;
      var layDens  = getDensity(config, matKey, bomType);
      var isOuter  = matKey.indexOf("sheath") >= 0 || matKey === "bedding";
      var coreCount = (matKey === "xlpe_insulation" || matKey === "pvc_insulation" ||
                       matKey === "conductor_screen" || matKey === "insulation_screen") ? numCores : 1;
      var annArea  = (Math.PI / 4) * (od * od - currentOd * currentOd);
      var wt       = annArea * layDens * coreCount * (isOuter ? getLayFactor(config, "cabling", 0, bomType) : 1.0);
      rows.push({
        layer: layer.layer_name || matKey,
        material: matKey,
        id_mm: round3(currentOd),
        effective_thickness_mm: round3(effT),
        od_mm: round3(od),
        tolerance_factor: tolFact,
        density_g_cm3: layDens,
        num_cores: coreCount,
        weight_kg_per_km: round3(wt)
      });
      currentOd = layer.od_mm || od;
    }
  }
  return rows;
}


// ── Costing Calculation ───────────────────────────────────────
function calcCosting(gtpData, bomResults, config) {
  var results = [];
  var prices  = getRmPrices(config);
  var margins = getMargins(config);
  var drums   = getDrumCosts(config);

  for (var i = 0; i < bomResults.length; i++) {
    var entry  = bomResults[i];
    var cable  = entry.cable;
    var bom    = entry.bom_costing;
    var area   = extractArea(cable.config || cable.designation || "");
    var family = inferFamily(cable.designation || "");
    var delLen = cable.delivery_length_m || 1000;
    var drumT  = (cable.drum_type || "wooden").toLowerCase();

    var matCost = 0;
    var breakdown = [];
    for (var j = 0; j < bom.length; j++) {
      var row   = bom[j];
      var mat   = row.material;
      var wt    = row.weight_kg_per_km || 0;
      var price = prices[mat] || 0;
      var cost  = wt * price;
      matCost  += cost;
      breakdown.push({ layer: row.layer, material: mat, weight_kg_per_km: wt, price_per_kg: price, cost_per_km: round2(cost) });
    }

    var drumCost = getDrumCostPerKm(drums, family, area, drumT, delLen);
    var total    = matCost + drumCost;
    var margin   = getMargin(margins, family, area);
    var selling  = margin < 100 ? total / (1 - margin / 100) : total;

    results.push({
      item_no: cable.item_no,
      designation: cable.designation,
      config: cable.config,
      voltage_kv: cable.voltage_kv,
      delivery_length_m: delLen,
      drum_type: drumT,
      material_cost_per_km: round2(matCost),
      drum_cost_per_km: round2(drumCost),
      conversion_cost_per_km: 0,
      total_cost_per_km: round2(total),
      margin_pct: margin,
      floor_price_per_km: round2(total),
      selling_price_per_km: round2(selling),
      selling_price_per_drum_length: round2(selling * delLen / 1000),
      material_breakdown: breakdown
    });
  }
  return results;
}


// ── Google Sheets: Write Results ──────────────────────────────
function writeBomToSheet(ss, gtpData, bomResults) {
  var ws   = ss.getSheetByName("BOM_Results");
  if (!ws) return;
  var now  = new Date().toISOString();
  var gRef = gtpData.gtp_ref || "";
  var gTyp = gtpData.gtp_type || "";

  for (var i = 0; i < bomResults.length; i++) {
    var entry = bomResults[i];
    var cable = entry.cable;
    var bom   = entry.bom_costing;
    var prodB = entry.bom_production;

    for (var j = 0; j < bom.length; j++) {
      var prodRow = prodB.find(function(r) { return r.layer === bom[j].layer; }) || {};
      ws.appendRow([
        gRef, gTyp, cable.designation || "", cable.config || "", cable.voltage_kv || "",
        bom[j].layer || "", bom[j].material || "",
        bom[j].weight_kg_per_km || 0, prodRow.weight_kg_per_km || 0,
        "kg/km", now
      ]);
    }
  }
}

function writeCostingToSheet(ss, gtpData, results) {
  var ws  = ss.getSheetByName("Costing_Results");
  if (!ws) return;
  var now = new Date().toISOString();

  for (var i = 0; i < results.length; i++) {
    var r = results[i];
    ws.appendRow([
      gtpData.gtp_ref || "", gtpData.gtp_type || "",
      r.designation || "", r.config || "",
      r.material_cost_per_km, r.drum_cost_per_km, r.conversion_cost_per_km,
      r.total_cost_per_km, r.margin_pct,
      r.floor_price_per_km, r.selling_price_per_km, r.selling_price_per_drum_length,
      r.delivery_length_m, r.drum_type, now
    ]);
  }

  // Log the GTP
  var log = ss.getSheetByName("GTP_Log");
  if (log) {
    log.appendRow([gtpData.gtp_ref || "", gtpData.customer || "", (gtpData.cables || []).length, now, now, "processed"]);
  }
}


// ── Config Loaders ────────────────────────────────────────────
function loadConfig(ss) {
  return {
    materials:   sheetToObjects(ss, "Config/Materials"),
    layFactors:  sheetToObjects(ss, "Config/Lay_Factors"),
    extrusionTol:sheetToObjects(ss, "Config/Extrusion_Tolerances"),
    gtpTypes:    sheetToObjects(ss, "Config/GTP_Types"),
    drums:       sheetToObjects(ss, "Config/Drums"),
    margins:     sheetToObjects(ss, "Config/Margins"),
  };
}

function sheetToObjects(ss, name) {
  var ws = ss.getSheetByName(name);
  if (!ws) return [];
  var data = ws.getDataRange().getValues();
  if (data.length < 2) return [];
  var headers = data[0];
  return data.slice(1).map(function(row) {
    var obj = {};
    headers.forEach(function(h, i) { obj[h] = row[i]; });
    return obj;
  });
}

function getDensity(config, matKey, bomType) {
  var col = bomType === "costing" ? "density_costing" : "density_production";
  var row = config.materials.find(function(r) { return r.material_code === matKey; });
  return row ? (parseFloat(row[col]) || 1.0) : 1.0;
}

function getLayFactor(config, category, numWires, bomType) {
  var col = bomType === "costing" ? "costing_value" : "production_value";
  if (category === "conductor") {
    var rows = config.layFactors.filter(function(r) { return r.category === "conductor"; });
    for (var i = 0; i < rows.length; i++) {
      if (numWires >= parseInt(rows[i].min_wires) && numWires <= parseInt(rows[i].max_wires)) {
        return parseFloat(rows[i][col]) || 1.0;
      }
    }
    return 1.005;
  } else {
    var row = config.layFactors.find(function(r) { return r.category === category; });
    return row ? (parseFloat(row[col]) || 1.0) : 1.007;
  }
}

function getTolFactor(config, thickness, thicknessType, bomType) {
  var col = bomType === "costing" ? "costing_factor" : "production_factor";
  for (var i = 0; i < config.extrusionTol.length; i++) {
    var r = config.extrusionTol[i];
    if (r.thickness_type === thicknessType &&
        thickness >= parseFloat(r.min_mm) && thickness < parseFloat(r.max_mm)) {
      return parseFloat(r[col]) || 1.0;
    }
  }
  return 1.0;
}

function getGtpTypeFactor(config, designation, gtpType) {
  var family = inferFamily(designation);
  var row = config.gtpTypes.find(function(r) {
    return r.product_type === family && r.gtp_suffix === gtpType;
  });
  return row ? {
    conductor_resistance_factor: parseFloat(row.conductor_resistance_factor) || 1.0,
    armour_coverage: parseFloat(row.armour_coverage) || 1.0,
    strip_thickness: parseFloat(row.strip_thickness) || null
  } : { conductor_resistance_factor: 1.0, armour_coverage: 1.0, strip_thickness: null };
}

function getRmPrices(config) {
  var prices = {};
  config.materials.forEach(function(r) { prices[r.material_code] = parseFloat(r.rm_price_per_kg) || 0; });
  return prices;
}

function getMargins(config) { return config.margins; }
function getDrumCosts(config) { return config.drums; }

function getMargin(margins, family, area) {
  for (var i = 0; i < margins.length; i++) {
    var r = margins[i];
    if (r.product_family === family && area >= parseFloat(r.min_area_mm2) && area <= parseFloat(r.max_area_mm2)) {
      return parseFloat(r.margin_pct) || 15;
    }
  }
  return 15;
}

function getDrumCostPerKm(drums, family, area, drumType, deliveryM) {
  for (var i = 0; i < drums.length; i++) {
    var r = drums[i];
    if (r.product_type === family && r.drum_type === drumType &&
        area >= parseFloat(r.size_range_from_mm2) && area <= parseFloat(r.size_range_to_mm2)) {
      var dl = parseFloat(r.drum_length_m) || deliveryM;
      var cp = parseFloat(r.cost_per_drum) || 0;
      return dl > 0 ? cp / (dl / 1000) : 0;
    }
  }
  return 0;
}


// ── Telegram Helpers ──────────────────────────────────────────
function sendTelegramMessage(chatId, text) {
  UrlFetchApp.fetch("https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage", {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ chat_id: chatId, text: text, parse_mode: "Markdown" }),
    muteHttpExceptions: true
  });
}

function downloadTelegramFile(fileId) {
  var infoUrl = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/getFile?file_id=" + fileId;
  var info    = JSON.parse(UrlFetchApp.fetch(infoUrl).getContentText());
  var filePath = info.result.file_path;
  var fileUrl  = "https://api.telegram.org/file/bot" + TELEGRAM_BOT_TOKEN + "/" + filePath;
  var blob     = UrlFetchApp.fetch(fileUrl).getBlob();
  return Utilities.base64Encode(blob.getBytes());
}

function sendPricesList(chatId) {
  try {
    var ss     = SpreadsheetApp.openById(getSpreadsheetId());
    var config = loadConfig(ss);
    var prices = getRmPrices(config);
    var lines  = ["*Current RM Prices (₹/kg)*\n"];
    Object.keys(prices).forEach(function(k) {
      if (prices[k] > 0) lines.push("• " + k.replace(/_/g, " ") + ": ₹" + prices[k].toLocaleString());
    });
    sendTelegramMessage(chatId, lines.join("\n"));
  } catch (err) {
    sendTelegramMessage(chatId, "Could not load prices: " + err);
  }
}


// ── Summary Builder ───────────────────────────────────────────
function buildTelegramSummary(gtpData, results) {
  var lines = ["*Pricing — GTP: " + (gtpData.gtp_ref || "?") + " (Type " + (gtpData.gtp_type || "?") + ")*\n"];
  results.forEach(function(r) {
    lines.push(
      "• *" + (r.config || "") + " " + (r.designation || "") + "*\n" +
      "  Floor: ₹" + r.floor_price_per_km.toLocaleString() + "/km | " +
      "Selling: ₹" + r.selling_price_per_km.toLocaleString() + "/km\n" +
      "  Per " + r.delivery_length_m + "m drum: ₹" + r.selling_price_per_drum_length.toLocaleString()
    );
  });
  return lines.join("\n");
}


// ── Utility ───────────────────────────────────────────────────
function inferFamily(designation) {
  var d = designation.toUpperCase();
  if (d.indexOf("XIFY") >= 0) return "HT_11KV";
  if (d.indexOf("XFY")  >= 0) return "LT_XLPE_ARMOURED";
  if (d.indexOf("2X")   >= 0 || d.indexOf("A2X") >= 0) return "LT_XLPE_UNARM";
  return "LT_PVC";
}

function extractArea(config) {
  var m = config.match(/(\d+(?:\.\d+)?)\s*mm/);
  return m ? parseFloat(m[1]) : 0;
}

function round2(v) { return Math.round(v * 100) / 100; }
function round3(v) { return Math.round(v * 1000) / 1000; }
function round4(v) { return Math.round(v * 10000) / 10000; }

function getSpreadsheetId() {
  var id = PropertiesService.getScriptProperties().getProperty("SPREADSHEET_ID");
  if (!id) throw new Error("SPREADSHEET_ID not set in Script Properties");
  return id;
}
