// ═══════════════════════════════════════════════════════════════════════════
// GtpExtractor.gs  —  GTP PDF → structured JSON via Claude
//
// Primary function: extractGtpWithClaude(pdfText) → gtpData object
// Test functions: run from the Apps Script editor to verify extraction
//   testExtractionFromDriveFile()  — paste a Drive file ID in the prompt
//   testExtractionOnActiveCell()   — put a Drive file ID in the active cell
// ═══════════════════════════════════════════════════════════════════════════


// ── Prompt template ───────────────────────────────────────────────────────

var GTP_EXTRACTION_PROMPT = [
  'You are a cable GTP (General Test Procedure / Technical Parameter sheet) parser.',
  'Extract all cable specifications and return ONLY valid JSON — no markdown, no explanation.',
  '',
  'Return this exact schema:',
  '{',
  '  "gtp_ref": "string — GTP document number",',
  '  "customer": "string — customer name if present",',
  '  "cables": [',
  '    {',
  '      "item_no": "string",',
  '      "designation": "string — cable type code e.g. XLPE, XFY, 2XFY",',
  '      "config": "string — e.g. 3.5C x 70 Sqmm or 4C x 16mm²",',
  '      "voltage_kv": "string — e.g. 1.1kV, 11kV",',
  '      "conductor_material": "copper or aluminium",',
  '      "conductor_shape": "circular or sector",',
  '      "conductor_type": "stranded or compacted or flexible",',
  '      "n_wires": null or number,',
  '      "num_cores": number,',
  '      "is_half_neutral": false or true,',
  '      "conductor_area_mm2": number,',
  '      "neutral_area_mm2": null or number,',
  '      "conductor_resistance_ohm_per_km": null or number,',
  '      "neutral_resistance_ohm_per_km": null or number,',
  '      "delivery_length_m": number,',
  '      "drum_type": "wooden or steel",',
  '      "layers": [',
  '        {',
  '          "layer_name": "string",',
  '          "material_key": "one of the values listed below",',
  '          "nominal_thickness_mm": null or number,',
  '          "minimum_thickness_mm": null or number,',
  '          "average_thickness_mm": null or number,',
  '          "thickness_type": "Nominal or Minimum or Average",',
  '          "tape_thickness_mm": null or number,',
  '          "tape_overlap_pct": null or number,',
  '          "wire_diameter_mm": null or number,',
  '          "armour_strip_width_mm": null or number,',
  '          "armour_strip_thickness_mm": null or number,',
  '          "n_tapes": null or number,',
  '          "n_pairs": null or number,',
  '          "od_mm": null or number',
  '        }',
  '      ]',
  '    }',
  '  ]',
  '}',
  '',
  'Valid material_key values (use exactly as written):',
  '  conductor, conductor_screen, xlpe_insulation, pvc_insulation,',
  '  rubber_insulation, insulation_screen, copper_tape_screen,',
  '  glass_mica_tape, pe_tape, al_mylar_pe_tape, drain_wire,',
  '  binder_tape, swelling_tape, binding_tape_pp,',
  '  bedding, pvc_inner_sheath, pvc_armoured_sheath,',
  '  gs_flat_strip_armour, gs_round_wire_armour,',
  '  pp_filler, pvc_filler,',
  '  frlsh_outer_sheath, pvc_outer_sheath, lszh_outer_sheath,',
  '  copper_wire_screen',
  '',
  'Parsing rules — follow exactly:',
  '1. Thickness priority: use Nominal if present; else Average; else Minimum.',
  '   Always set thickness_type to match whichever you used.',
  '2. For 3.5C cables: create TWO conductor layer entries.',
  '   First: phase conductor (num_cores=3, area=phase_area, resistance=phase_R).',
  '   Second: neutral conductor (num_cores=1, area=neutral_area, resistance=neutral_R, is_half_neutral=true).',
  '3. Layers must be in construction order (inside → outside).',
  '4. Include conductor as the first layer entry.',
  '5. For instrumentation cables with individual pair screens, list layers as:',
  '   drain_wire → pe_tape → al_mylar_pe_tape (per pair).',
  '6. armour wire_diameter_mm: use GTP value; default 1.6mm LT, 2.0mm HT if absent.',
  '7. delivery_length_m: use GTP delivery length; default 1000 if not found.',
  '8. Do NOT invent values not present in the GTP.',
  '',
  'GTP TEXT:',
  '---',
].join('\n');


// ── Main extraction function (called from Code.gs pipeline) ──────────────

function extractGtpWithClaude(pdfText) {
  var text = pdfText.substring(0, 28000);  // guard against context overflow
  var prompt = GTP_EXTRACTION_PROMPT + '\n' + text + '\n---';

  var payload = {
    model:      'claude-haiku-4-5-20251001',
    max_tokens: 4096,
    messages:   [{ role: 'user', content: prompt }],
  };

  var resp = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method:            'post',
    headers: {
      'x-api-key':         CONFIG.ANTHROPIC_KEY,
      'anthropic-version': '2023-06-01',
      'content-type':      'application/json',
    },
    payload:           JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  var code = resp.getResponseCode();
  var body = JSON.parse(resp.getContentText());

  if (code !== 200) {
    throw new Error('Claude API error ' + code + ': ' + (body.error && body.error.message || resp.getContentText()));
  }

  var raw = body.content[0].text.trim();
  raw = raw.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '').trim();

  var gtpData = JSON.parse(raw);
  validateGtpData(gtpData);
  return gtpData;
}


// ── Validation ────────────────────────────────────────────────────────────

function validateGtpData(gtpData) {
  if (!gtpData || typeof gtpData !== 'object') {
    throw new Error('GTP extraction returned invalid object');
  }
  if (!gtpData.gtp_ref) {
    Logger.log('Warning: gtp_ref missing — using UNKNOWN');
    gtpData.gtp_ref = 'UNKNOWN';
  }
  if (!Array.isArray(gtpData.cables) || gtpData.cables.length === 0) {
    throw new Error('No cables found in GTP — check PDF quality or GTP format');
  }
  gtpData.cables.forEach(function(cable, idx) {
    if (!cable.conductor_area_mm2) {
      Logger.log('Warning: cable[' + idx + '] missing conductor_area_mm2');
    }
    if (!Array.isArray(cable.layers) || cable.layers.length === 0) {
      Logger.log('Warning: cable[' + idx + '] has no layers');
    }
    // Default mandatory fields
    cable.num_cores           = cable.num_cores           || 1;
    cable.conductor_material  = cable.conductor_material  || 'copper';
    cable.conductor_shape     = cable.conductor_shape     || 'circular';
    cable.conductor_type      = cable.conductor_type      || 'stranded';
    cable.delivery_length_m   = cable.delivery_length_m   || 1000;
  });
}


// ═══════════════════════════════════════════════════════════════════════════
// TEST FUNCTIONS  —  run from the Apps Script editor
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Paste a Google Drive PDF file ID when prompted.
 * Runs full extraction and logs the result to the Apps Script console.
 */
function testExtractionFromDriveFile() {
  var ui     = SpreadsheetApp.getUi();
  var result = ui.prompt('Test GTP Extraction', 'Enter Google Drive file ID of a GTP PDF:', ui.ButtonSet.OK_CANCEL);
  if (result.getSelectedButton() !== ui.Button.OK) return;

  var fileId = result.getResponseText().trim();
  try {
    var blob    = DriveApp.getFileById(fileId).getBlob();
    var pdfText = pdfToText(blob);
    Logger.log('PDF text length: ' + pdfText.length + ' chars');
    Logger.log('First 500 chars:\n' + pdfText.substring(0, 500));

    var gtpData = extractGtpWithClaude(pdfText);
    Logger.log('Extracted GTP:\n' + JSON.stringify(gtpData, null, 2));

    var summary = 'GTP: ' + gtpData.gtp_ref + '\nCables: ' + gtpData.cables.length + '\n\n';
    gtpData.cables.forEach(function(c) {
      summary += '• ' + (c.config || '') + ' ' + (c.designation || '') +
                 ' (' + c.num_cores + 'C × ' + c.conductor_area_mm2 + 'mm²)\n';
      summary += '  Layers: ' + (c.layers || []).map(function(l){ return l.material_key; }).join(', ') + '\n';
    });
    ui.alert('Extraction Result', summary, ui.ButtonSet.OK);

  } catch (err) {
    ui.alert('Error', err.message + '\n\nCheck Apps Script logs for details.', ui.ButtonSet.OK);
    Logger.log('testExtraction error: ' + err.stack);
  }
}

/**
 * Put a Drive file ID in the currently active cell, then run this.
 * Extracts GTP and writes a summary table starting two rows below the cell.
 */
function testExtractionOnActiveCell() {
  var cell   = SpreadsheetApp.getActiveSpreadsheet().getActiveCell();
  var fileId = cell.getValue().toString().trim();
  if (!fileId) { SpreadsheetApp.getUi().alert('Active cell must contain a Drive file ID.'); return; }

  try {
    var blob    = DriveApp.getFileById(fileId).getBlob();
    var pdfText = pdfToText(blob);
    var gtpData = extractGtpWithClaude(pdfText);

    var sheet   = cell.getSheet();
    var startRow = cell.getRow() + 2;
    sheet.getRange(startRow, cell.getColumn()).setValue('GTP: ' + gtpData.gtp_ref);
    startRow++;

    var headers = ['Item', 'Config', 'Cores', 'Area (mm²)', 'Conductor', 'Layers'];
    sheet.getRange(startRow, cell.getColumn(), 1, headers.length).setValues([headers]);
    sheet.getRange(startRow, cell.getColumn(), 1, headers.length)
         .setFontWeight('bold').setBackground('#1a3b6e').setFontColor('#ffffff');
    startRow++;

    gtpData.cables.forEach(function(c) {
      var layerList = (c.layers || []).map(function(l){ return l.material_key; }).join(', ');
      sheet.getRange(startRow, cell.getColumn(), 1, headers.length).setValues([[
        c.item_no || '', c.config || '', c.num_cores,
        c.conductor_area_mm2, c.conductor_material, layerList,
      ]]);
      startRow++;
    });

    SpreadsheetApp.getUi().alert('Done — ' + gtpData.cables.length + ' cable(s) extracted.');
  } catch (err) {
    SpreadsheetApp.getUi().alert('Error: ' + err.message);
    Logger.log(err.stack);
  }
}

/**
 * Test BOM calculation independently — extract from Drive file then run calculator.
 * Shows weights in a new sheet named 'BOM_Test'.
 */
function testFullBomCalculation() {
  var ui     = SpreadsheetApp.getUi();
  var result = ui.prompt('Test Full BOM', 'Drive file ID of GTP PDF:', ui.ButtonSet.OK_CANCEL);
  if (result.getSelectedButton() !== ui.Button.OK) return;

  var fileId  = result.getResponseText().trim();
  var typeRes = ui.prompt('BOM Type', 'A, B, or C:', ui.ButtonSet.OK_CANCEL);
  var bomType = (typeRes.getSelectedButton() === ui.Button.OK && typeRes.getResponseText().trim().toUpperCase()) || 'A';

  try {
    var blob    = DriveApp.getFileById(fileId).getBlob();
    var pdfText = pdfToText(blob);
    var gtpData = extractGtpWithClaude(pdfText);
    var ss      = SpreadsheetApp.getActiveSpreadsheet();

    // Write results to a test sheet
    var ws = ss.getSheetByName('BOM_Test');
    if (ws) ss.deleteSheet(ws);
    ws = ss.insertSheet('BOM_Test');

    var headers = ['Cable', 'Layer', 'Material Key', 'Weight Costing (kg/km)', 'Weight Production (kg/km)', 'OD After (mm)'];
    ws.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold')
      .setBackground('#1a3b6e').setFontColor('#ffffff');

    var row = 2;
    gtpData.cables.forEach(function(cable) {
      var label   = (cable.config || '') + ' ' + (cable.designation || '');
      var costBom = buildBomForCable(cable, bomType, 'costing');
      var prodBom = buildBomForCable(cable, bomType, 'production');

      for (var i = 0; i < costBom.length; i++) {
        var prodRow = prodBom[i] || {};
        ws.getRange(row, 1, 1, headers.length).setValues([[
          i === 0 ? label : '',
          costBom[i].layer,
          costBom[i].material,
          costBom[i].weight_kg_per_km,
          prodRow.weight_kg_per_km || '',
          costBom[i].od_after_mm || '',
        ]]);
        row++;
      }
      // Totals
      var totalCost = costBom.reduce(function(s, r){ return s + (r.weight_kg_per_km || 0); }, 0);
      var totalProd = prodBom.reduce(function(s, r){ return s + (r.weight_kg_per_km || 0); }, 0);
      ws.getRange(row, 1, 1, headers.length).setValues([['', 'TOTAL', '', Math.round(totalCost*100)/100, Math.round(totalProd*100)/100, '']])
        .setFontWeight('bold').setBackground('#e8f0fe');
      row += 2;
    });

    ws.autoResizeColumns(1, headers.length);
    ss.setActiveSheet(ws);
    ui.alert('BOM_Test sheet created — ' + gtpData.cables.length + ' cable(s).');

  } catch (err) {
    ui.alert('Error: ' + err.message);
    Logger.log(err.stack);
  }
}
