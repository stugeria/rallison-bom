// ═══════════════════════════════════════════════════════════════════════════
// SheetWriter.gs  —  One-time sheet setup + ongoing write helpers
//
// Run setupAllSheets() once from the BOM System menu (or Apps Script editor)
// to create all sheets with headers, formatting, and pre-populated config data.
// Safe to re-run — existing sheets are cleared and rebuilt.
// ═══════════════════════════════════════════════════════════════════════════


// ── Formatting constants ──────────────────────────────────────────────────

var FMT = {
  HEADER:   { backgroundColor: '#1a3b6e', textFormat: { bold: true, foregroundColor: '#ffffff' } },
  EDITABLE: { backgroundColor: '#fff3cd' },  // amber — "you can change this"
  READONLY: { backgroundColor: '#f8f9fa' },
  PRICE:    { backgroundColor: '#e8f5e9' },  // light green — price columns
};


// ── Called from BOM System menu ───────────────────────────────────────────

function setupAllSheets() {
  var ss  = SpreadsheetApp.getActiveSpreadsheet();
  var ui  = SpreadsheetApp.getUi();

  var confirm = ui.alert(
    'Setup All Sheets',
    'This will create/rebuild all BOM sheets.\nExisting data in output sheets will be cleared. Config data will be reset.\n\nContinue?',
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  try {
    _setupOutputSheets(ss);
    _setupConfigSheets(ss);
    _deleteDefaultSheet(ss);
    ss.toast('All sheets created successfully.', 'BOM System Setup', 5);
  } catch (err) {
    ui.alert('Setup failed: ' + err.message + '\nCheck Apps Script logs.');
    Logger.log(err.stack);
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// OUTPUT SHEETS
// ═══════════════════════════════════════════════════════════════════════════

function _setupOutputSheets(ss) {
  // ── RM_Master ─────────────────────────────────────────────────────────
  var rmHeaders = ['material_key', 'RM Code', 'RM Description', 'Unit'];
  var rmData = [
    // Conductors
    ['copper_conductor',       'RM-CU-001', 'Copper Conductor Wire / Rod',          'kg/km'],
    ['aluminium_conductor',    'RM-AL-001', 'Aluminium Conductor Wire / Rod',        'kg/km'],
    ['drain_wire',             'RM-CU-002', 'Bare Copper Drain Wire (7/0.3mm)',      'kg/km'],
    // Insulation
    ['xlpe_insulation',        'RM-IN-001', 'XLPE Insulation Compound',              'kg/km'],
    ['pvc_insulation',         'RM-IN-002', 'PVC Insulation Compound',               'kg/km'],
    ['pvc_flexible',           'RM-IN-003', 'PVC Flexible Compound (IS 694)',        'kg/km'],
    ['rubber_insulation',      'RM-IN-004', 'Rubber / EPDM Insulation Compound',     'kg/km'],
    ['rubber_epdm',            'RM-IN-005', 'EPDM Compound',                         'kg/km'],
    // Sheaths
    ['pvc_armoured_sheath',    'RM-SH-001', 'PVC Compound — Armoured Sheath',        'kg/km'],
    ['pvc_outer_sheath',       'RM-SH-002', 'PVC Compound — Outer Sheath',           'kg/km'],
    ['pvc_inner_sheath',       'RM-SH-003', 'PVC Compound — Inner Sheath',           'kg/km'],
    ['pvc_frlsh_sheath',       'RM-SH-004', 'PVC / FR-LSH Compound',                 'kg/km'],
    ['hffr_sheath',            'RM-SH-005', 'HFFR / ZHFR Compound',                  'kg/km'],
    ['lszh_sheath',            'RM-SH-006', 'LSZH Compound',                         'kg/km'],
    ['lszh_outer_sheath',      'RM-SH-007', 'LSZH Outer Sheath Compound',            'kg/km'],
    ['frlsh_sheath',           'RM-SH-008', 'FR-LSH Compound',                       'kg/km'],
    ['frlsh_outer_sheath',     'RM-SH-009', 'FR-LSH Outer Sheath Compound',          'kg/km'],
    ['bedding',                'RM-SH-010', 'Bedding / Inner Sheath Compound',       'kg/km'],
    // Screens & armour
    ['conductor_screen',       'RM-SC-001', 'Semi-conducting Screen Compound',       'kg/km'],
    ['insulation_screen',      'RM-SC-002', 'Semi-conducting Insulation Screen',     'kg/km'],
    ['copper_tape_screen',     'RM-SC-003', 'Copper Tape Screen',                    'kg/km'],
    ['copper_wire_screen',     'RM-SC-004', 'Copper Wire Concentric Screen',         'kg/km'],
    ['gs_flat_strip_armour',   'RM-AR-001', 'GS Flat Strip Armour',                  'kg/km'],
    ['gs_round_wire_armour',   'RM-AR-002', 'GS Round Wire Armour',                  'kg/km'],
    // Tapes
    ['pe_tape',                'RM-TP-001', 'PE Tape (Individual Pair Screen)',       'kg/km'],
    ['al_mylar_pe_tape',       'RM-TP-002', 'Al Mylar + PE Laminate Tape Screen',    'kg/km'],
    ['glass_mica_tape',        'RM-TP-003', 'Glass Mica Fire Barrier Tape',          'kg/km'],
    ['binder_tape',            'RM-TP-004', 'Binder Tape',                           'kg/km'],
    ['binding_tape_pp',        'RM-TP-005', 'PP Binding Tape',                       'kg/km'],
    ['swelling_tape',          'RM-TP-006', 'Swelling Tape (Water Blocking)',        'kg/km'],
    ['petp_tape',              'RM-TP-007', 'PETP Tape',                             'kg/km'],
    // Fillers
    ['pp_filler',              'RM-FL-001', 'PP Rope Filler',                        'kg/km'],
    ['pvc_filler',             'RM-FL-002', 'PVC Filler (Sector Cables)',            'kg/km'],
    ['filler_compound',        'RM-FL-003', 'Filler Compound',                       'kg/km'],
  ];
  var wsRm = _createSheet(ss, 'RM_Master', [rmHeaders].concat(rmData));
  wsRm.getRange('A2:A' + (rmData.length + 1))
      .setFontFamily('Courier New').setBackground('#f0f0f0');
  wsRm.setColumnWidth(1, 180);
  wsRm.setColumnWidth(2, 100);
  wsRm.setColumnWidth(3, 300);

  // ── GTP_Registry ──────────────────────────────────────────────────────
  var regHeaders = [
    'Min Margin %', 'GTP No.', 'Item No.', 'Item Name', 'Item Code',
    'Cable Family', 'Voltage Grade', 'No. of Cores',
    'Conductor Area (mm²)', 'Conductor Material', 'Conductor Shape',
    'Insulation', 'Armour', 'Sheath', 'Overall OD (mm)',
    'Price — Type A (₹/km)', 'Price — Type B (₹/km)', 'Price — Type C (₹/km)',
    'Created At', 'Last Updated',
  ];
  var wsReg = _createSheet(ss, SHEETS.GTP_REGISTRY, [regHeaders]);
  wsReg.setFrozenRows(1);
  wsReg.getDataRange().createFilter();
  // Col A amber (manually editable)
  wsReg.getRange('A2:A1000').setBackground(FMT.EDITABLE.backgroundColor);
  wsReg.getRange('A1').setNote('Enter minimum acceptable margin % per product. Overrides Config/Margins if set.');
  // Price columns green
  wsReg.getRange('P2:R1000').setBackground(FMT.PRICE.backgroundColor);
  // Column widths
  [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20].forEach(function(c, i) {
    wsReg.setColumnWidth(c, [80,120,60,200,90,120,80,70,100,100,90,80,100,100,80,130,130,130,140,110][i]);
  });

  // ── BOM_Production & BOM_Costing ──────────────────────────────────────
  var bomHeaders = [
    'BOM No.', 'GTP No.', 'BOM Type', 'Item No.', 'Item Name', 'Item Code',
    'RM Code', 'RM Description', 'Weight (kg/km)',
  ];
  var wsProd = _createSheet(ss, SHEETS.BOM_PRODUCTION, [bomHeaders]);
  wsProd.setFrozenRows(1);
  wsProd.getDataRange().createFilter();
  wsProd.getRange('I2:I5000').setNumberFormat('#,##0.000');
  [120,120,70,60,200,90,100,250,110].forEach(function(w, i) { wsProd.setColumnWidth(i+1, w); });

  var wsCost = _createSheet(ss, SHEETS.BOM_COSTING, [bomHeaders]);
  wsCost.setFrozenRows(1);
  wsCost.getDataRange().createFilter();
  wsCost.getRange('I2:I5000').setNumberFormat('#,##0.000');
  [120,120,70,60,200,90,100,250,110].forEach(function(w, i) { wsCost.setColumnWidth(i+1, w); });
}


// ═══════════════════════════════════════════════════════════════════════════
// CONFIG SHEETS
// ═══════════════════════════════════════════════════════════════════════════

function _setupConfigSheets(ss) {
  // ── Config/Materials ──────────────────────────────────────────────────
  _createSheet(ss, SHEETS.MATERIALS, [
    ['material_code','material_name','density_costing','density_production','rm_price_per_kg','unit','last_updated','notes'],
    ['copper_conductor',     'Copper Conductor',          8.89,  8.89,  0, 'g/cm3', '', ''],
    ['aluminium_conductor',  'Aluminium Conductor',       2.703, 2.703, 0, 'g/cm3', '', ''],
    ['xlpe_insulation',      'XLPE Insulation',           0.92,  0.92,  0, 'g/cm3', '', ''],
    ['pvc_insulation',       'PVC Insulation',            1.50,  1.50,  0, 'g/cm3', '', ''],
    ['pvc_flexible',         'PVC Flexible (IS 694)',     1.50,  1.50,  0, 'g/cm3', '', ''],
    ['rubber_insulation',    'Rubber/EPDM Insulation',    1.35,  1.35,  0, 'g/cm3', '', ''],
    ['conductor_screen',     'Semicon Screen',            1.20,  1.20,  0, 'g/cm3', '', ''],
    ['insulation_screen',    'Semicon Ins Screen',        1.20,  1.20,  0, 'g/cm3', '', ''],
    ['pvc_armoured_sheath',  'PVC Armoured Sheath',       1.60,  1.60,  0, 'g/cm3', '', ''],
    ['pvc_outer_sheath',     'PVC Outer Sheath',          1.60,  1.60,  0, 'g/cm3', '', ''],
    ['pvc_inner_sheath',     'PVC Inner Sheath',          1.60,  1.60,  0, 'g/cm3', '', ''],
    ['bedding',              'Bedding Compound',          1.60,  1.60,  0, 'g/cm3', '', ''],
    ['frlsh_sheath',         'FR-LSH Sheath',             1.50,  1.50,  0, 'g/cm3', '', ''],
    ['frlsh_outer_sheath',   'FR-LSH Outer Sheath',       1.50,  1.50,  0, 'g/cm3', '', ''],
    ['hffr_sheath',          'HFFR Sheath',               1.50,  1.50,  0, 'g/cm3', '', ''],
    ['lszh_sheath',          'LSZH Sheath',               1.50,  1.50,  0, 'g/cm3', '', ''],
    ['lszh_outer_sheath',    'LSZH Outer Sheath',         1.50,  1.50,  0, 'g/cm3', '', ''],
    ['gs_flat_strip_armour', 'GS Flat Strip Armour',      7.85,  7.85,  0, 'g/cm3', '', ''],
    ['gs_round_wire_armour', 'GS Round Wire Armour',      7.85,  7.85,  0, 'g/cm3', '', ''],
    ['copper_tape_screen',   'Copper Tape Screen',        8.89,  8.89,  0, 'g/cm3', '', ''],
    ['copper_wire_screen',   'Copper Wire Screen',        8.89,  8.89,  0, 'g/cm3', '', ''],
    ['filler_compound',      'Filler Compound',           1.40,  1.40,  0, 'g/cm3', '', ''],
    ['binder_tape',          'Binder Tape',               1.35,  1.35,  0, 'g/cm3', '', ''],
    ['binding_tape_pp',      'PP Binding Tape',           0.91,  0.91,  0, 'g/cm3', '', ''],
    ['swelling_tape',        'Swelling Tape',             1.00,  1.00,  0, 'g/cm3', '', ''],
    ['petp_tape',            'PETP Tape',                 1.39,  1.39,  0, 'g/cm3', '', ''],
    ['pe_tape',              'PE Tape',                   1.50,  1.50,  0, 'g/cm3', '', ''],
    ['al_mylar_pe_tape',     'Al Mylar + PE Tape',        1.50,  1.50,  0, 'g/cm3', '', ''],
    ['glass_mica_tape',      'Glass Mica Tape',           1.40,  1.40,  0, 'g/cm3', '', 'Fire survival'],
    ['pp_filler',            'PP Rope Filler',            0.91,  0.91,  0, 'g/cm3', '', ''],
    ['pvc_filler',           'PVC Filler',                1.70,  1.70,  0, 'g/cm3', '', 'Sector cables'],
  ]);

  // ── Config/GTP_Types ─────────────────────────────────────────────────
  _createSheet(ss, SHEETS.GTP_TYPES, [
    ['product_type','gtp_suffix','conductor_resistance_factor','armour_coverage','strip_thickness','notes'],
    ['LT_XLPE_UNARM',    'A', 1.000, 1.000, '', ''],
    ['LT_XLPE_UNARM',    'B', 0.920, 1.000, '', ''],
    ['LT_XLPE_UNARM',    'C', 0.900, 1.000, '', ''],
    ['LT_XLPE_ARMOURED', 'A', 1.000, 0.900, '', ''],
    ['LT_XLPE_ARMOURED', 'B', 0.920, 0.800, '', ''],
    ['LT_XLPE_ARMOURED', 'C', 0.900, 0.800, '', ''],
    ['HT_11KV',          'A', 1.000, 0.900, '', ''],
    ['HT_11KV',          'B', 0.920, 0.800, '', ''],
    ['HT_11KV',          'C', 0.900, 0.800, '', ''],
    ['LT_PVC',           'A', 1.000, 0.900, '', ''],
    ['LT_PVC',           'B', 0.920, 0.800, '', ''],
    ['LT_PVC',           'C', 0.900, 0.800, '', ''],
    ['LT_PVC_FLEX',      'A', 1.000, 1.000, '', ''],
    ['LT_PVC_FLEX',      'B', 0.920, 1.000, '', ''],
    ['LT_PVC_FLEX',      'C', 0.900, 1.000, '', ''],
    ['INSTR_SCREENED',   'A', 1.000, 1.000, '', ''],
    ['INSTR_SCREENED',   'B', 0.920, 1.000, '', ''],
    ['INSTR_SCREENED',   'C', 0.900, 1.000, '', ''],
    ['FIRE_SURVIVAL',    'A', 1.000, 0.900, '', ''],
    ['FIRE_SURVIVAL',    'B', 0.920, 0.800, '', ''],
    ['FIRE_SURVIVAL',    'C', 0.900, 0.800, '', ''],
  ]);

  // ── Config/Margins ────────────────────────────────────────────────────
  _createSheet(ss, SHEETS.MARGINS, [
    ['product_family','min_area_mm2','max_area_mm2','margin_pct','notes'],
    ['LT_PVC',           0,    2.5,  20, ''],
    ['LT_PVC',           4,    10,   18, ''],
    ['LT_PVC',           16,   500,  15, ''],
    ['LT_PVC_FLEX',      0,    2.5,  20, ''],
    ['LT_PVC_FLEX',      4,    10,   18, ''],
    ['LT_PVC_FLEX',      16,   500,  15, ''],
    ['LT_XLPE_UNARM',    0,    10,   18, ''],
    ['LT_XLPE_UNARM',    16,   70,   16, ''],
    ['LT_XLPE_UNARM',    95,   300,  14, ''],
    ['LT_XLPE_UNARM',    400,  9999, 12, ''],
    ['LT_XLPE_ARMOURED', 0,    10,   18, ''],
    ['LT_XLPE_ARMOURED', 16,   70,   16, ''],
    ['LT_XLPE_ARMOURED', 95,   300,  14, ''],
    ['LT_XLPE_ARMOURED', 400,  9999, 12, ''],
    ['HT_11KV',          0,    70,   15, ''],
    ['HT_11KV',          95,   300,  13, ''],
    ['HT_11KV',          400,  9999, 11, ''],
    ['INSTR_SCREENED',   0,    9999, 18, ''],
    ['FIRE_SURVIVAL',    0,    9999, 17, ''],
  ]);

  // ── Config/Drums ──────────────────────────────────────────────────────
  _createSheet(ss, SHEETS.DRUMS, [
    ['product_type','size_range_from_mm2','size_range_to_mm2','drum_type','drum_length_m','cost_per_drum','notes'],
    ['LT_XLPE_UNARM',    0,   16,   'wooden', 1000, 0, ''],
    ['LT_XLPE_UNARM',    25,  120,  'wooden', 1000, 0, ''],
    ['LT_XLPE_UNARM',    150, 500,  'wooden', 500,  0, ''],
    ['LT_XLPE_ARMOURED', 0,   16,   'wooden', 1000, 0, ''],
    ['LT_XLPE_ARMOURED', 25,  120,  'wooden', 1000, 0, ''],
    ['LT_XLPE_ARMOURED', 150, 500,  'wooden', 500,  0, ''],
    ['LT_XLPE_ARMOURED', 0,   16,   'steel',  1000, 0, ''],
    ['LT_XLPE_ARMOURED', 25,  120,  'steel',  1000, 0, ''],
    ['LT_XLPE_ARMOURED', 150, 500,  'steel',  500,  0, ''],
    ['HT_11KV',          0,   500,  'steel',  500,  0, ''],
    ['LT_PVC',           0,   10,   'wooden', 1000, 0, ''],
    ['LT_PVC',           10,  500,  'wooden', 1000, 0, ''],
    ['LT_PVC_FLEX',      0,   500,  'wooden', 1000, 0, ''],
    ['INSTR_SCREENED',   0,   500,  'wooden', 1000, 0, ''],
    ['FIRE_SURVIVAL',    0,   500,  'wooden', 1000, 0, ''],
  ]);

  // ── Config/Extrusion_Tolerances ───────────────────────────────────────
  _createSheet(ss, 'Config/Extrusion_Tolerances', [
    ['thickness_type','band','min_mm','max_mm','costing_factor','production_factor','notes'],
    ['Nominal','thin',   0,   1.0, 1.10, 1.00, '<1mm nominal'],
    ['Nominal','medium', 1.0, 2.0, 1.08, 1.00, '1–2mm nominal'],
    ['Nominal','thick',  2.0, 99,  1.05, 1.00, '>2mm nominal'],
    ['Minimum','thin',   0,   1.0, 1.15, 1.05, 'GTP states minimum <1mm'],
    ['Minimum','medium', 1.0, 2.0, 1.12, 1.05, 'GTP states minimum 1–2mm'],
    ['Minimum','thick',  2.0, 99,  1.08, 1.05, 'GTP states minimum >2mm'],
  ]);

  // ── Config/Lay_Factors ────────────────────────────────────────────────
  _createSheet(ss, 'Config/Lay_Factors', [
    ['category','condition','min_wires','max_wires','costing_value','production_value','notes'],
    ['conductor','Solid/compacted', 1,  1,   1.000, 1.000, 'Forced — no twist'],
    ['conductor','Up to 7 wires',   2,  7,   1.005, 1.005, ''],
    ['conductor','8 to 19 wires',   8,  19,  1.010, 1.010, ''],
    ['conductor','20 to 37 wires',  20, 37,  1.020, 1.020, ''],
    ['conductor','38 to 61 wires',  38, 61,  1.025, 1.025, ''],
    ['conductor','62+ (fine wire)', 62, 130, 1.030, 1.025, 'Fine wire flexible'],
    ['conductor','131+ (fine wire)',131, 999, 1.040, 1.035, 'Fine wire flexible'],
    ['cabling',  'All',             '',  '',  1.008, 1.007, 'Per-core layers'],
    ['armour',   'All',             '',  '',  1.008, 1.007, 'Armour layer'],
  ]);
}


// ═══════════════════════════════════════════════════════════════════════════
// WRITE HELPERS (used by Code.gs pipeline)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Update RM price for a material — called from UI price editor.
 */
function updateRmPrice(materialCode, newPrice) {
  var ss  = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  var ws  = ss.getSheetByName(SHEETS.MATERIALS);
  var data = ws.getDataRange().getValues();
  var headers = data[0];
  var codeCol  = headers.indexOf('material_code');
  var priceCol = headers.indexOf('rm_price_per_kg');
  var updCol   = headers.indexOf('last_updated');

  for (var i = 1; i < data.length; i++) {
    if (data[i][codeCol] === materialCode) {
      ws.getRange(i + 1, priceCol + 1).setValue(newPrice);
      ws.getRange(i + 1, updCol   + 1).setValue(new Date().toISOString().substring(0, 10));
      return true;
    }
  }
  return false;
}

/**
 * Get BOM rows for a specific GTP+Item+Type from BOM_Production or BOM_Costing.
 */
function getBomRows(gtpNo, itemNo, bomType, sheetName) {
  var ss   = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  var ws   = ss.getSheetByName(sheetName);
  var data = ws.getDataRange().getValues();
  if (data.length < 2) return [];
  var h   = data[0];
  return data.slice(1)
    .filter(function(row) {
      return String(row[h.indexOf('GTP No.')])  === String(gtpNo) &&
             String(row[h.indexOf('Item No.')]) === String(itemNo) &&
             String(row[h.indexOf('BOM Type')]) === String(bomType);
    })
    .map(function(row) {
      var obj = {};
      h.forEach(function(k, i) { obj[k] = row[i]; });
      return obj;
    });
}


// ═══════════════════════════════════════════════════════════════════════════
// INTERNAL HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function _createSheet(ss, name, data) {
  var ws = ss.getSheetByName(name);
  if (!ws) {
    ws = ss.insertSheet(name);
    Logger.log('Created sheet: ' + name);
  } else {
    ws.clearContents().clearFormats();
  }
  if (data && data.length > 0) {
    ws.getRange(1, 1, data.length, data[0].length).setValues(data);
    ws.getRange(1, 1, 1, data[0].length).setBackground(FMT.HEADER.backgroundColor)
      .setFontColor(FMT.HEADER.textFormat.foregroundColor)
      .setFontWeight('bold')
      .setHorizontalAlignment('center');
    ws.setFrozenRows(1);
  }
  return ws;
}

function _deleteDefaultSheet(ss) {
  try {
    var ws = ss.getSheetByName('Sheet1');
    if (ws && ss.getSheets().length > 1) ss.deleteSheet(ws);
  } catch (e) {}
}
