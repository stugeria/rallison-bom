// ═══════════════════════════════════════════════════════════════════════════
// Code.gs  —  BOM System entry point
// Handles: Telegram webhook, Sheets custom menu, GTP pipeline orchestration
//
// SETUP (one-time):
//   1. Paste all .gs files into a new Apps Script project bound to your sheet
//   2. Fill in CONFIG below
//   3. Deploy → New deployment → Web app → Execute as: Me → Anyone can access
//   4. Register the web app URL as Telegram webhook:
//      https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WEB_APP_URL>
//   5. Run setupDriveFolders() once from the editor to create Drive folders
// ═══════════════════════════════════════════════════════════════════════════

// ── CONFIG — fill these in ────────────────────────────────────────────────
var CONFIG = {
  TELEGRAM_TOKEN:   PropertiesService.getScriptProperties().getProperty('TELEGRAM_TOKEN')   || '',
  ANTHROPIC_KEY:    PropertiesService.getScriptProperties().getProperty('ANTHROPIC_KEY')    || '',
  SPREADSHEET_ID:   PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID')   || '',
  GTP_FOLDER_ID:    PropertiesService.getScriptProperties().getProperty('GTP_FOLDER_ID')    || '',  // Drive folder for incoming GTPs
  // Restrict to these Telegram chat IDs (comma-separated string). Leave blank to allow all.
  ALLOWED_CHAT_IDS: PropertiesService.getScriptProperties().getProperty('ALLOWED_CHAT_IDS') || '',
};

// Sheet names — must match setup_sheets.py / SheetWriter.gs
var SHEETS = {
  GTP_REGISTRY:    'GTP_Registry',
  BOM_PRODUCTION:  'BOM_Production',
  BOM_COSTING:     'BOM_Costing',
  RM_MASTER:       'RM_Master',
  MATERIALS:       'Config/Materials',
  MARGINS:         'Config/Margins',
  DRUMS:           'Config/Drums',
  GTP_TYPES:       'Config/GTP_Types',
};


// ═══════════════════════════════════════════════════════════════════════════
// TELEGRAM WEBHOOK ENTRY POINT
// ═══════════════════════════════════════════════════════════════════════════

function doPost(e) {
  try {
    var update = JSON.parse(e.postData.contents);
    handleTelegramUpdate(update);
  } catch (err) {
    Logger.log('doPost error: ' + err.message);
  }
  // Always return 200 so Telegram stops retrying
  return ContentService.createTextOutput('OK');
}


function handleTelegramUpdate(update) {
  var msg = update.message || update.channel_post;
  if (!msg) return;

  var chatId   = msg.chat.id;
  var chatIdStr = String(chatId);

  // Auth check
  var allowed = CONFIG.ALLOWED_CHAT_IDS;
  if (allowed && allowed.split(',').map(function(s){ return s.trim(); }).indexOf(chatIdStr) === -1) {
    tgSend(chatId, '⛔ Unauthorized.');
    return;
  }

  // Text commands
  if (msg.text) {
    var txt = msg.text.trim().toLowerCase();
    if (txt === '/start' || txt === '/help') {
      tgSend(chatId,
        '*BOM System*\n\n' +
        'Send a GTP PDF to calculate pricing.\n' +
        'Caption your PDF with the BOM type: *A*, *B*, or *C*\n' +
        '(Default: calculates all 3, shares Type A)\n\n' +
        '/prices — view current RM prices\n' +
        '/status — last processed GTP'
      );
    } else if (txt === '/prices') {
      sendRmPrices(chatId);
    } else if (txt === '/status') {
      sendStatus(chatId);
    }
    return;
  }

  // PDF document
  if (msg.document) {
    var doc = msg.document;
    var mime = doc.mime_type || '';
    if (mime !== 'application/pdf') {
      tgSend(chatId, 'Please send a PDF file.');
      return;
    }

    // Parse requested BOM type from caption (default A)
    var caption = (msg.caption || '').trim().toUpperCase();
    var requestedType = ['A','B','C'].indexOf(caption) !== -1 ? caption : 'A';

    tgSend(chatId, '⏳ Received GTP. Processing all BOM types — please wait...');

    try {
      var blob = downloadTelegramFile(doc.file_id);
      var result = runPipeline(blob, requestedType, chatId);
      tgSend(chatId, result.summary);
    } catch (err) {
      tgSend(chatId, '❌ Error: ' + err.message);
      Logger.log('Pipeline error: ' + err.stack);
    }
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// SHEETS CUSTOM MENU
// ═══════════════════════════════════════════════════════════════════════════

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('BOM System')
    .addItem('Process GTP from Drive link…', 'showGtpDialog')
    .addSeparator()
    .addItem('Setup Drive folders', 'setupDriveFolders')
    .addItem('Register Telegram webhook', 'registerWebhook')
    .addToUi();
}

function showGtpDialog() {
  var ui = SpreadsheetApp.getUi();
  var linkResult = ui.prompt(
    'Process GTP',
    'Paste the Google Drive link or File ID of the GTP PDF:',
    ui.ButtonSet.OK_CANCEL
  );
  if (linkResult.getSelectedButton() !== ui.Button.OK) return;

  var typeResult = ui.prompt(
    'BOM Type',
    'Which BOM type to share? (A / B / C — all 3 are calculated):',
    ui.ButtonSet.OK_CANCEL
  );
  var requestedType = 'A';
  if (typeResult.getSelectedButton() === ui.Button.OK) {
    var t = typeResult.getResponseText().trim().toUpperCase();
    if (['A','B','C'].indexOf(t) !== -1) requestedType = t;
  }

  var fileId = extractFileId(linkResult.getResponseText().trim());
  if (!fileId) { ui.alert('Could not parse file ID from that link.'); return; }

  try {
    var blob = DriveApp.getFileById(fileId).getBlob();
    var result = runPipeline(blob, requestedType, null);
    ui.alert('BOM Complete', result.summary.replace(/\*/g, ''), ui.ButtonSet.OK);
  } catch (err) {
    ui.alert('Error: ' + err.message);
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// MAIN PIPELINE
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Full BOM pipeline: PDF → GTP JSON → BOM (A/B/C) → Sheets → pricing summary
 * @param {Blob}   blob           - PDF blob
 * @param {string} requestedType - 'A', 'B', or 'C'
 * @param {number} chatId        - Telegram chat ID (null if from menu)
 * @returns {{summary: string, pricing: Object}}
 */
function runPipeline(blob, requestedType, chatId) {
  var ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);

  // 1. Load config from Sheets
  var rmPrices   = loadRmPrices(ss);
  var rmMap      = loadRmCodeMap(ss);
  var margins    = sheetToObjects(ss, SHEETS.MARGINS);
  var drumCosts  = sheetToObjects(ss, SHEETS.DRUMS);

  // 2. PDF → text
  var pdfText = pdfToText(blob);

  // 3. Text → structured GTP JSON via Claude
  var gtpData = extractGtpWithClaude(pdfText);
  var gtpNo   = (gtpData.gtp_ref || 'UNKNOWN').replace(/[\/\\]/g, '-');
  var cables  = gtpData.cables || [];

  Logger.log('GTP: ' + gtpNo + ' | Cables: ' + cables.length);

  var processedItems = [];
  var skippedItems   = [];
  var now = new Date().toISOString().replace('T', ' ').substring(0, 19) + ' UTC';

  for (var i = 0; i < cables.length; i++) {
    var cable    = cables[i];
    var itemNo   = String(cable.item_no || (i + 1));
    var itemName = cable.designation || '';
    var itemCode = cable.item_code   || '';
    var config   = cable.config      || '';

    // Dedup check
    var existing = findInRegistry(ss, gtpNo, itemNo);
    if (existing) {
      skippedItems.push({
        item_no: itemNo, item_name: itemName, config: config,
        price_a: existing['Price — Type A (₹/km)'] || 0,
        price_b: existing['Price — Type B (₹/km)'] || 0,
        price_c: existing['Price — Type C (₹/km)'] || 0,
      });
      Logger.log('Skipped (exists): ' + gtpNo + ' / Item ' + itemNo);
      continue;
    }

    Logger.log('Processing: [' + itemNo + '] ' + config + ' ' + itemName);

    // Compute BOM for all 3 types × 2 calc modes
    var boms = {};
    var BOM_TYPES = ['A','B','C'];
    for (var j = 0; j < BOM_TYPES.length; j++) {
      var t = BOM_TYPES[j];
      boms[t] = {
        costing:    buildBomForCable(cable, t, 'costing'),
        production: buildBomForCable(cable, t, 'production'),
      };
    }

    // Pricing
    var productFamily  = inferProductType(itemName, config);
    var areaMm2        = extractArea(config);
    var deliveryM      = cable.delivery_length_m || 1000;
    var drumType       = cable.drum_type || 'wooden';
    var marginPct      = lookupMargin(margins, productFamily, areaMm2);
    var drumCostKm     = lookupDrumCost(drumCosts, productFamily, areaMm2, drumType, deliveryM);

    var prices = {};
    for (var k = 0; k < BOM_TYPES.length; k++) {
      var bt = BOM_TYPES[k];
      prices[bt] = calcPrice(boms[bt].costing, rmPrices, marginPct, drumCostKm);
    }

    var cableResult = {
      item_no: itemNo, item_name: itemName, item_code: itemCode,
      config: config, product_family: productFamily,
      price_a: prices['A'], price_b: prices['B'], price_c: prices['C'],
    };
    processedItems.push(cableResult);

    // Write to Sheets
    writeToRegistry(ss, gtpNo, itemNo, itemName, itemCode, cable,
                    productFamily, areaMm2, prices, now);

    var prodRows = [], costRows = [];
    for (var m = 0; m < BOM_TYPES.length; m++) {
      var bt2   = BOM_TYPES[m];
      var bomNo = gtpNo + '-' + itemNo + '-' + bt2;
      prodRows = prodRows.concat(
        toBomSheetRows(boms[bt2].production, bomNo, gtpNo, bt2, itemNo, itemName, itemCode, rmMap)
      );
      costRows = costRows.concat(
        toBomSheetRows(boms[bt2].costing,    bomNo, gtpNo, bt2, itemNo, itemName, itemCode, rmMap)
      );
    }
    appendRows(ss, SHEETS.BOM_PRODUCTION, prodRows);
    appendRows(ss, SHEETS.BOM_COSTING,    costRows);
    Logger.log('Written: ' + prodRows.length + ' prod / ' + costRows.length + ' cost rows');
  }

  // Build summary
  var allItems = processedItems.concat(skippedItems);
  var summary  = buildSummary(gtpNo, requestedType, allItems);
  return { summary: summary, pricing: allItems };
}


// ═══════════════════════════════════════════════════════════════════════════
// PDF → TEXT  (Drive API conversion)
// ═══════════════════════════════════════════════════════════════════════════

function pdfToText(blob) {
  // Save PDF to Drive inbox folder
  var folder  = CONFIG.GTP_FOLDER_ID
                ? DriveApp.getFolderById(CONFIG.GTP_FOLDER_ID)
                : DriveApp.getRootFolder();
  var pdfFile = folder.createFile(blob.setName('gtp_temp_' + Date.now() + '.pdf'));

  // Convert to Google Doc (Drive extracts text from PDF)
  var resource = { title: 'gtp_temp_doc', mimeType: 'application/vnd.google-apps.document' };
  var docFile  = Drive.Files.copy(resource, pdfFile.getId(), { convert: true });
  var text     = DocumentApp.openById(docFile.id).getBody().getText();

  // Clean up temp files
  Drive.Files.remove(docFile.id);
  pdfFile.setTrashed(true);

  return text;
}


// ═══════════════════════════════════════════════════════════════════════════
// CLAUDE API — GTP extraction
// ═══════════════════════════════════════════════════════════════════════════

function extractGtpWithClaude(pdfText) {
  var prompt = [
    'You are a cable GTP (General Test Procedure / Technical Parameter) parser.',
    'Extract all cable specifications from the text below and return ONLY valid JSON — no explanation, no markdown.',
    '',
    'JSON schema:',
    '{',
    '  "gtp_ref": "string",',
    '  "cables": [{',
    '    "item_no": "string",',
    '    "designation": "string",',
    '    "config": "string (e.g. 3.5C x 70mm²)",',
    '    "voltage_kv": "string",',
    '    "conductor_material": "copper|aluminium",',
    '    "conductor_shape": "circular|sector",',
    '    "conductor_type": "stranded|compacted|flexible",',
    '    "num_cores": number,',
    '    "is_half_neutral": boolean,',
    '    "conductor_area_mm2": number,',
    '    "neutral_area_mm2": number|null,',
    '    "conductor_resistance_ohm_per_km": number,',
    '    "neutral_resistance_ohm_per_km": number|null,',
    '    "delivery_length_m": number,',
    '    "layers": [{',
    '      "layer_name": "string",',
    '      "material_key": "one of: conductor|conductor_screen|xlpe_insulation|pvc_insulation|insulation_screen|copper_tape_screen|glass_mica_tape|pe_tape|al_mylar_pe_tape|drain_wire|binder_tape|swelling_tape|bedding|pvc_inner_sheath|gs_flat_strip_armour|gs_round_wire_armour|pp_filler|pvc_filler|frlsh_outer_sheath|pvc_outer_sheath|pvc_armoured_sheath|lszh_outer_sheath|copper_wire_screen",',
    '      "nominal_thickness_mm": number|null,',
    '      "minimum_thickness_mm": number|null,',
    '      "thickness_type": "Nominal|Minimum|Average",',
    '      "tape_thickness_mm": number|null,',
    '      "tape_overlap_pct": number|null,',
    '      "wire_diameter_mm": number|null,',
    '      "armour_strip_width_mm": number|null,',
    '      "armour_strip_thickness_mm": number|null,',
    '      "n_tapes": number|null,',
    '      "n_pairs": number|null,',
    '      "od_mm": number|null',
    '    }]',
    '  }]',
    '}',
    '',
    'Rules:',
    '- Use Nominal thickness when available; fall back to Average, then Minimum',
    '- For 3.5C cables: list phase conductor (3C) and neutral (0.5C) as separate layer entries with their respective resistances',
    '- If a layer OD is given in the GTP, include it as od_mm; otherwise null',
    '- armour wire_diameter_mm default: 1.6mm for LT, 2.0mm for HT if not stated',
    '- Do NOT invent data not present in the GTP text',
    '',
    'GTP TEXT:',
    '---',
    pdfText.substring(0, 28000),  // Claude context limit guard
    '---',
  ].join('\n');

  var payload = {
    model:      'claude-haiku-4-5-20251001',  // fast + cheap for extraction
    max_tokens: 4096,
    messages:   [{ role: 'user', content: prompt }],
  };

  var resp = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method:  'post',
    headers: {
      'x-api-key':         CONFIG.ANTHROPIC_KEY,
      'anthropic-version': '2023-06-01',
      'content-type':      'application/json',
    },
    payload:              JSON.stringify(payload),
    muteHttpExceptions:   true,
  });

  var body = JSON.parse(resp.getContentText());
  if (resp.getResponseCode() !== 200) {
    throw new Error('Claude API error: ' + JSON.stringify(body));
  }

  var raw = body.content[0].text.trim();
  // Strip markdown fences if present
  raw = raw.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '');
  return JSON.parse(raw);
}


// ═══════════════════════════════════════════════════════════════════════════
// SHEETS HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function sheetToObjects(ss, sheetName) {
  var ws = ss.getSheetByName(sheetName);
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

function loadRmPrices(ss) {
  var rows = sheetToObjects(ss, SHEETS.MATERIALS);
  var prices = {};
  rows.forEach(function(r) {
    if (r['material_code'] && r['rm_price_per_kg']) {
      prices[r['material_code']] = parseFloat(r['rm_price_per_kg']) || 0;
    }
  });
  return prices;
}

function loadRmCodeMap(ss) {
  var rows = sheetToObjects(ss, SHEETS.RM_MASTER);
  var map = {};
  rows.forEach(function(r) {
    if (r['material_key']) {
      map[r['material_key']] = {
        rm_code:        r['RM Code']        || '',
        rm_description: r['RM Description'] || r['material_key'],
      };
    }
  });
  return map;
}

function findInRegistry(ss, gtpNo, itemNo) {
  var rows = sheetToObjects(ss, SHEETS.GTP_REGISTRY);
  for (var i = 0; i < rows.length; i++) {
    if (String(rows[i]['GTP No.']) === String(gtpNo) &&
        String(rows[i]['Item No.']) === String(itemNo)) {
      return rows[i];
    }
  }
  return null;
}

function writeToRegistry(ss, gtpNo, itemNo, itemName, itemCode, cable,
                         productFamily, areaMm2, prices, now) {
  var ws = ss.getSheetByName(SHEETS.GTP_REGISTRY);
  ws.appendRow([
    '',                                        // Min Margin % (user fills)
    gtpNo,
    itemNo,
    itemName,
    itemCode,                                  // user fills Item Code later
    productFamily,
    cable.voltage_kv  || '',
    cable.num_cores   || '',
    areaMm2,
    cable.conductor_material || '',
    cable.conductor_shape    || '',
    inferInsulation(cable),
    inferArmour(cable),
    inferSheath(cable),
    '',                                        // Overall OD (filled by calc if available)
    prices['A'],
    prices['B'],
    prices['C'],
    now,
    now,
  ]);
}

function toBomSheetRows(bomRows, bomNo, gtpNo, bomType,
                        itemNo, itemName, itemCode, rmMap) {
  return bomRows.map(function(r) {
    var rm = rmMap[r.material] || {};
    return [
      bomNo, gtpNo, bomType, itemNo, itemName, itemCode,
      rm.rm_code        || '',
      rm.rm_description || r.material,
      r.weight_kg_per_km || 0,
    ];
  });
}

function appendRows(ss, sheetName, rows) {
  if (!rows.length) return;
  var ws = ss.getSheetByName(sheetName);
  ws.getRange(ws.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
}


// ═══════════════════════════════════════════════════════════════════════════
// PRICING
// ═══════════════════════════════════════════════════════════════════════════

function calcPrice(costingBom, rmPrices, marginPct, drumCostKm) {
  var materialCost = 0;
  costingBom.forEach(function(r) {
    var price  = rmPrices[r.material] || 0;
    materialCost += (r.weight_kg_per_km || 0) * price;
  });
  var totalCost = materialCost + drumCostKm;
  if (marginPct >= 100) return totalCost;
  return Math.round(totalCost / (1 - marginPct / 100));
}

function lookupMargin(margins, productFamily, areaMm2) {
  for (var i = 0; i < margins.length; i++) {
    var r = margins[i];
    if (String(r['product_family']).toUpperCase() === productFamily.toUpperCase()) {
      if (areaMm2 >= parseFloat(r['min_area_mm2']) && areaMm2 <= parseFloat(r['max_area_mm2'])) {
        return parseFloat(r['margin_pct']) || 15;
      }
    }
  }
  return 15;
}

function lookupDrumCost(drumCosts, productFamily, areaMm2, drumType, deliveryM) {
  for (var i = 0; i < drumCosts.length; i++) {
    var r = drumCosts[i];
    if (String(r['product_type']).toUpperCase() === productFamily.toUpperCase() &&
        String(r['drum_type']).toLowerCase() === (drumType || 'wooden').toLowerCase()) {
      if (areaMm2 >= parseFloat(r['size_range_from_mm2']) &&
          areaMm2 <= parseFloat(r['size_range_to_mm2'])) {
        var length = parseFloat(r['drum_length_m']) || deliveryM;
        var cost   = parseFloat(r['cost_per_drum']) || 0;
        return length > 0 ? cost / (length / 1000) : 0;
      }
    }
  }
  return 0;
}


// ═══════════════════════════════════════════════════════════════════════════
// TELEGRAM HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function tgSend(chatId, text) {
  if (!CONFIG.TELEGRAM_TOKEN) return;
  UrlFetchApp.fetch(
    'https://api.telegram.org/bot' + CONFIG.TELEGRAM_TOKEN + '/sendMessage',
    {
      method:  'post',
      headers: { 'Content-Type': 'application/json' },
      payload: JSON.stringify({ chat_id: chatId, text: text, parse_mode: 'Markdown' }),
      muteHttpExceptions: true,
    }
  );
}

function downloadTelegramFile(fileId) {
  var infoResp = UrlFetchApp.fetch(
    'https://api.telegram.org/bot' + CONFIG.TELEGRAM_TOKEN + '/getFile?file_id=' + fileId,
    { muteHttpExceptions: true }
  );
  var filePath = JSON.parse(infoResp.getContentText()).result.file_path;
  var fileResp = UrlFetchApp.fetch(
    'https://api.telegram.org/file/bot' + CONFIG.TELEGRAM_TOKEN + '/' + filePath,
    { muteHttpExceptions: true }
  );
  return fileResp.getBlob().setName('gtp.pdf').setContentType('application/pdf');
}

function sendRmPrices(chatId) {
  try {
    var ss   = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
    var rows = sheetToObjects(ss, SHEETS.MATERIALS);
    var lines = ['*Current RM Prices (₹/kg)*\n'];
    rows.forEach(function(r) {
      if (r['rm_price_per_kg']) {
        lines.push('• ' + (r['material_name'] || r['material_code']) +
                   ': ₹' + Number(r['rm_price_per_kg']).toLocaleString('en-IN'));
      }
    });
    tgSend(chatId, lines.join('\n'));
  } catch (e) {
    tgSend(chatId, 'Could not load prices: ' + e.message);
  }
}

function sendStatus(chatId) {
  try {
    var ss  = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
    var ws  = ss.getSheetByName(SHEETS.GTP_REGISTRY);
    var lr  = ws.getLastRow();
    if (lr < 2) { tgSend(chatId, 'No GTPs processed yet.'); return; }
    var row = ws.getRange(lr, 1, 1, ws.getLastColumn()).getValues()[0];
    var h   = ws.getRange(1, 1, 1, ws.getLastColumn()).getValues()[0];
    var obj = {};
    h.forEach(function(k, i) { obj[k] = row[i]; });
    tgSend(chatId,
      '*Last processed GTP*\n' +
      'GTP No.: `' + obj['GTP No.'] + '`\n' +
      'Item: ' + obj['Item Name'] + '\n' +
      'Type A: ₹' + Number(obj['Price — Type A (₹/km)']).toLocaleString('en-IN') + '/km\n' +
      'Date: ' + obj['Created At']
    );
  } catch (e) {
    tgSend(chatId, 'Could not load status: ' + e.message);
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// SUMMARY BUILDER
// ═══════════════════════════════════════════════════════════════════════════

function buildSummary(gtpNo, requestedType, items) {
  var priceKey = 'price_' + requestedType.toLowerCase();
  var lines = ['*GTP: ' + gtpNo + ' | BOM Type ' + requestedType + '*\n'];

  if (items.length === 1) {
    var r = items[0];
    lines.push(
      '*' + (r.config || '') + ' ' + r.item_name + '*\n' +
      'Price: ₹' + Number(r[priceKey] || 0).toLocaleString('en-IN') + '/km'
    );
  } else {
    lines.push('```');
    lines.push(pad('No.', 4) + pad('Description', 32) + lpad('₹/km', 14));
    lines.push('─'.repeat(50));
    items.forEach(function(r) {
      var desc  = ((r.config || '') + ' ' + r.item_name).trim().substring(0, 31);
      var price = Number(r[priceKey] || 0).toLocaleString('en-IN');
      lines.push(pad(r.item_no, 4) + pad(desc, 32) + lpad(price, 14));
    });
    lines.push('```');
  }
  return lines.join('\n');
}


// ═══════════════════════════════════════════════════════════════════════════
// MISC HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function inferProductType(designation, config) {
  var d = (designation + ' ' + config).toUpperCase();
  if (d.indexOf('11KV') !== -1 || d.indexOf('XIFY') !== -1) return 'HT_11KV';
  if (d.indexOf('XFY')  !== -1 || d.indexOf('ARMOUR') !== -1) return 'LT_XLPE_ARMOURED';
  if (d.indexOf('2X')   !== -1 || d.indexOf('A2X')    !== -1) return 'LT_XLPE_UNARM';
  if (d.indexOf('FIRE') !== -1 || d.indexOf(' FS ')   !== -1) return 'FIRE_SURVIVAL';
  if (d.indexOf('INSTR') !== -1|| d.indexOf('PAIR')   !== -1) return 'INSTR_SCREENED';
  if (d.indexOf('FLEX') !== -1) return 'LT_PVC_FLEX';
  return 'LT_PVC';
}

function inferInsulation(cable) {
  var layers = cable.layers || [];
  for (var i = 0; i < layers.length; i++) {
    var k = layers[i].material_key || '';
    if (k.indexOf('xlpe') !== -1) return 'XLPE';
    if (k.indexOf('pvc_insulation') !== -1) return 'PVC';
    if (k.indexOf('rubber') !== -1) return 'Rubber/EPDM';
  }
  return '';
}

function inferArmour(cable) {
  var layers = cable.layers || [];
  for (var i = 0; i < layers.length; i++) {
    var k = layers[i].material_key || '';
    if (k === 'gs_flat_strip_armour') return 'GS Flat Strip';
    if (k === 'gs_round_wire_armour') return 'GS Round Wire';
  }
  return 'Unarmoured';
}

function inferSheath(cable) {
  var layers = cable.layers || [];
  for (var i = layers.length - 1; i >= 0; i--) {
    var k = layers[i].material_key || '';
    if (k.indexOf('sheath') !== -1) return k.replace(/_/g, ' ').toUpperCase();
  }
  return '';
}

function extractArea(config) {
  var m = (config || '').match(/(\d+(?:\.\d+)?)\s*mm/);
  return m ? parseFloat(m[1]) : 0;
}

function extractFileId(linkOrId) {
  var m = linkOrId.match(/\/d\/([a-zA-Z0-9_-]+)/);
  if (m) return m[1];
  m = linkOrId.match(/id=([a-zA-Z0-9_-]+)/);
  if (m) return m[1];
  if (/^[a-zA-Z0-9_-]{25,}$/.test(linkOrId)) return linkOrId;
  return null;
}

function pad(str, len)  { str = String(str); while (str.length < len) str += ' '; return str; }
function lpad(str, len) { str = String(str); while (str.length < len) str = ' ' + str; return str; }


// ═══════════════════════════════════════════════════════════════════════════
// ONE-TIME SETUP FUNCTIONS (run from editor)
// ═══════════════════════════════════════════════════════════════════════════

function setupDriveFolders() {
  var root   = DriveApp.getRootFolder();
  var folder = root.createFolder('BOM_GTP_Inbox');
  PropertiesService.getScriptProperties().setProperty('GTP_FOLDER_ID', folder.getId());
  SpreadsheetApp.getUi().alert(
    'Created folder: BOM_GTP_Inbox\nFolder ID saved to script properties.\nURL: ' + folder.getUrl()
  );
}

function registerWebhook() {
  var webAppUrl = ScriptApp.getService().getUrl();
  var resp = UrlFetchApp.fetch(
    'https://api.telegram.org/bot' + CONFIG.TELEGRAM_TOKEN +
    '/setWebhook?url=' + encodeURIComponent(webAppUrl),
    { muteHttpExceptions: true }
  );
  var result = JSON.parse(resp.getContentText());
  SpreadsheetApp.getUi().alert(
    result.ok ? '✅ Webhook registered:\n' + webAppUrl : '❌ Failed:\n' + JSON.stringify(result)
  );
}

/**
 * Run this once from the Apps Script editor to store your secrets.
 * Edit values here, run once, then DELETE this function for security.
 */
function setScriptProperties() {
  PropertiesService.getScriptProperties().setProperties({
    'TELEGRAM_TOKEN':  'YOUR_TELEGRAM_BOT_TOKEN',
    'ANTHROPIC_KEY':   'YOUR_ANTHROPIC_API_KEY',
    'SPREADSHEET_ID':  'YOUR_SPREADSHEET_ID',
    'ALLOWED_CHAT_IDS': 'CHAT_ID_1,CHAT_ID_2',  // comma-separated, or leave blank
  });
  Logger.log('Properties saved.');
}
