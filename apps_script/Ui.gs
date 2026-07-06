// ═══════════════════════════════════════════════════════════════════════════
// Ui.gs  —  Dialogs, sidebars, and menu wiring
//
// All user-facing UI components. Uses HtmlService for rich dialogs.
// onOpen() in Code.gs wires the full menu — Ui.gs adds the extra items.
// ═══════════════════════════════════════════════════════════════════════════


// ── Extended menu (merged into onOpen in Code.gs) ─────────────────────────
// Add these items to the BOM System menu in Code.gs onOpen():
//   .addSeparator()
//   .addItem('View BOM for GTP…', 'showBomViewer')
//   .addItem('Update RM Prices…', 'showPriceEditor')
//   .addItem('Pricing Summary…',  'showPricingSummaryForSelected')


// ═══════════════════════════════════════════════════════════════════════════
// PRICING SUMMARY DIALOG
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Shows pricing for all 3 BOM types for a selected GTP+Item.
 * User picks from a dropdown of all items in GTP_Registry.
 */
function showPricingSummaryForSelected() {
  var ss   = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  var rows = sheetToObjects(ss, SHEETS.GTP_REGISTRY);
  if (!rows.length) {
    SpreadsheetApp.getUi().alert('No GTPs in registry yet.');
    return;
  }

  // Build options list
  var options = rows.map(function(r, i) {
    return {
      idx:       i,
      label:     r['GTP No.'] + ' | ' + (r['Item Name'] || 'Item ' + r['Item No.']),
      gtp_no:    r['GTP No.'],
      item_no:   r['Item No.'],
      item_name: r['Item Name'] || '',
      price_a:   r['Price — Type A (₹/km)'] || 0,
      price_b:   r['Price — Type B (₹/km)'] || 0,
      price_c:   r['Price — Type C (₹/km)'] || 0,
      margin:    r['Min Margin %'] || '',
    };
  });

  var html = HtmlService.createHtmlOutput(_pricingSummaryHtml(options))
    .setTitle('Pricing Summary')
    .setWidth(560)
    .setHeight(480);
  SpreadsheetApp.getUi().showModalDialog(html, 'Pricing Summary');
}

function _pricingSummaryHtml(options) {
  var optionHtml = options.map(function(o) {
    return '<option value="' + o.idx + '">' + _esc(o.label) + '</option>';
  }).join('');

  var dataJson = JSON.stringify(options);

  return '<!DOCTYPE html><html><head><style>' +
    'body{font-family:Google Sans,Arial,sans-serif;margin:0;padding:16px;font-size:13px}' +
    'select{width:100%;padding:8px;font-size:13px;border:1px solid #dadce0;border-radius:4px;margin-bottom:16px}' +
    '.card{background:#f8f9fa;border-radius:8px;padding:16px;margin-bottom:12px}' +
    '.type-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid #e0e0e0}' +
    '.type-row:last-child{border-bottom:none}' +
    '.type-badge{display:inline-block;width:28px;height:28px;border-radius:50%;text-align:center;line-height:28px;font-weight:bold;color:#fff;margin-right:10px}' +
    '.badge-a{background:#1a73e8}.badge-b{background:#34a853}.badge-c{background:#ea4335}' +
    '.price{font-size:18px;font-weight:bold;color:#202124}' +
    '.label{color:#5f6368;font-size:12px}' +
    '.margin-note{font-size:11px;color:#5f6368;margin-top:8px}' +
    'h3{margin:0 0 12px;color:#202124}' +
    '</style></head><body>' +
    '<h3>Select GTP / Item</h3>' +
    '<select id="sel" onchange="update()">' + optionHtml + '</select>' +
    '<div id="card" class="card"></div>' +
    '<script>' +
    'var data=' + dataJson + ';' +
    'function fmt(n){return n?Number(n).toLocaleString("en-IN",{maximumFractionDigits:0}):"—";}' +
    'function update(){' +
    '  var o=data[document.getElementById("sel").value];' +
    '  document.getElementById("card").innerHTML=' +
    '    "<h3>"+o.item_name+"</h3>"' +
    '    +"<div class=\'type-row\'><span><span class=\'type-badge badge-a\'>A</span><span class=\'label\'>Type A</span></span><span class=\'price\'>₹"+fmt(o.price_a)+"/km</span></div>"' +
    '    +"<div class=\'type-row\'><span><span class=\'type-badge badge-b\'>B</span><span class=\'label\'>Type B</span></span><span class=\'price\'>₹"+fmt(o.price_b)+"/km</span></div>"' +
    '    +"<div class=\'type-row\'><span><span class=\'type-badge badge-c\'>C</span><span class=\'label\'>Type C</span></span><span class=\'price\'>₹"+fmt(o.price_c)+"/km</span></div>"' +
    '    +(o.margin?"<p class=\'margin-note\'>Min margin override: "+o.margin+"%</p>":"");' +
    '}' +
    'update();' +
    '</script></body></html>';
}


// ═══════════════════════════════════════════════════════════════════════════
// BOM DETAIL VIEWER
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Shows full BOM breakdown (all 3 types, production & costing) for a selected item.
 */
function showBomViewer() {
  var ui  = SpreadsheetApp.getUi();
  var ss  = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  var rows = sheetToObjects(ss, SHEETS.GTP_REGISTRY);
  if (!rows.length) { ui.alert('No GTPs in registry yet.'); return; }

  // Prompt for GTP No.
  var gtpRes = ui.prompt('View BOM', 'Enter GTP No.:', ui.ButtonSet.OK_CANCEL);
  if (gtpRes.getSelectedButton() !== ui.Button.OK) return;
  var gtpNo = gtpRes.getResponseText().trim();

  var itemRes = ui.prompt('View BOM', 'Enter Item No. (or leave blank for item 1):', ui.ButtonSet.OK_CANCEL);
  var itemNo  = (itemRes.getSelectedButton() === ui.Button.OK && itemRes.getResponseText().trim()) || '1';

  var typeRes = ui.prompt('View BOM', 'BOM Type (A / B / C):', ui.ButtonSet.OK_CANCEL);
  var bomType = (typeRes.getSelectedButton() === ui.Button.OK && typeRes.getResponseText().trim().toUpperCase()) || 'A';

  var prodRows = getBomRows(gtpNo, itemNo, bomType, SHEETS.BOM_PRODUCTION);
  var costRows = getBomRows(gtpNo, itemNo, bomType, SHEETS.BOM_COSTING);

  if (!prodRows.length && !costRows.length) {
    ui.alert('No BOM found for GTP ' + gtpNo + ' / Item ' + itemNo + ' / Type ' + bomType);
    return;
  }

  var html = HtmlService.createHtmlOutput(_bomViewerHtml(gtpNo, itemNo, bomType, prodRows, costRows))
    .setTitle('BOM — ' + gtpNo + ' / Type ' + bomType)
    .setWidth(700)
    .setHeight(520);
  ui.showModalDialog(html, 'BOM Detail');
}

function _bomViewerHtml(gtpNo, itemNo, bomType, prodRows, costRows) {
  // Merge prod & cost by layer
  var layers = prodRows.map(function(p, i) {
    var c = costRows[i] || {};
    return {
      rm_code: p['RM Code'] || '',
      rm_desc: p['RM Description'] || '',
      prod:    Number(p['Weight (kg/km)'] || 0),
      cost:    Number(c['Weight (kg/km)'] || 0),
    };
  });

  var totalProd = layers.reduce(function(s, r){ return s + r.prod; }, 0);
  var totalCost = layers.reduce(function(s, r){ return s + r.cost; }, 0);

  var rows = layers.map(function(l) {
    return '<tr><td>' + _esc(l.rm_code) + '</td><td>' + _esc(l.rm_desc) + '</td>' +
           '<td class="num">' + l.prod.toFixed(3) + '</td>' +
           '<td class="num">' + l.cost.toFixed(3) + '</td></tr>';
  }).join('');

  return '<!DOCTYPE html><html><head><style>' +
    'body{font-family:Google Sans,Arial,sans-serif;font-size:12px;margin:0;padding:12px}' +
    'h3{margin:0 0 4px;font-size:14px;color:#202124}' +
    '.sub{color:#5f6368;margin-bottom:12px;font-size:11px}' +
    'table{width:100%;border-collapse:collapse}' +
    'th{background:#1a3b6e;color:#fff;padding:7px 8px;text-align:left;font-size:11px}' +
    'th.num,td.num{text-align:right}' +
    'tr:nth-child(even){background:#f8f9fa}' +
    'td{padding:5px 8px;border-bottom:1px solid #e0e0e0}' +
    '.total{font-weight:bold;background:#e8f0fe!important}' +
    '</style></head><body>' +
    '<h3>BOM — GTP ' + _esc(gtpNo) + ' / Item ' + _esc(itemNo) + ' / Type ' + _esc(bomType) + '</h3>' +
    '<p class="sub">All weights in kg/km</p>' +
    '<table><thead><tr>' +
    '<th>RM Code</th><th>Description</th>' +
    '<th class="num">Production</th><th class="num">Costing</th>' +
    '</tr></thead><tbody>' + rows +
    '<tr class="total"><td colspan="2">TOTAL</td>' +
    '<td class="num">' + totalProd.toFixed(3) + '</td>' +
    '<td class="num">' + totalCost.toFixed(3) + '</td>' +
    '</tr></tbody></table></body></html>';
}


// ═══════════════════════════════════════════════════════════════════════════
// RM PRICE EDITOR
// ═══════════════════════════════════════════════════════════════════════════

function showPriceEditor() {
  var ss    = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  var mats  = sheetToObjects(ss, SHEETS.MATERIALS);
  var priced = mats.filter(function(r){ return r['material_code']; });

  var html = HtmlService.createHtmlOutput(_priceEditorHtml(priced))
    .setTitle('Update RM Prices')
    .setWidth(520)
    .setHeight(540);
  SpreadsheetApp.getUi().showModalDialog(html, 'Update RM Prices');
}

function _priceEditorHtml(materials) {
  var rows = materials.map(function(m) {
    var code  = m['material_code'] || '';
    var name  = m['material_name'] || code;
    var price = m['rm_price_per_kg'] || 0;
    return '<tr><td>' + _esc(name) + '</td>' +
           '<td><input type="number" id="' + _esc(code) + '" value="' + price +
           '" min="0" step="0.01" style="width:100px;padding:4px;border:1px solid #dadce0;border-radius:3px"></td></tr>';
  }).join('');

  var codes = JSON.stringify(materials.map(function(m){ return m['material_code']; }));

  return '<!DOCTYPE html><html><head><style>' +
    'body{font-family:Google Sans,Arial,sans-serif;font-size:13px;margin:0;padding:12px}' +
    'h3{margin:0 0 12px}table{width:100%;border-collapse:collapse}' +
    'td{padding:6px 4px;border-bottom:1px solid #f0f0f0}' +
    'tr:hover td{background:#f8f9fa}' +
    '.btn{background:#1a73e8;color:#fff;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;font-size:13px;margin-top:12px}' +
    '.btn:hover{background:#1557b0}.msg{color:#34a853;font-size:12px;margin-top:8px}' +
    '</style></head><body>' +
    '<h3>Update RM Prices (₹/kg)</h3>' +
    '<div style="max-height:380px;overflow-y:auto">' +
    '<table><thead><tr><th style="text-align:left">Material</th><th>Price</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div>' +
    '<button class="btn" onclick="saveAll()">Save All Prices</button>' +
    '<div id="msg" class="msg"></div>' +
    '<script>' +
    'var codes=' + codes + ';' +
    'function saveAll(){' +
    '  var updates=codes.map(function(c){return{code:c,price:parseFloat(document.getElementById(c).value)||0};});' +
    '  document.getElementById("msg").innerText="Saving...";' +
    '  google.script.run.withSuccessHandler(function(n){' +
    '    document.getElementById("msg").innerText="✓ "+n+" prices saved.";' +
    '  }).withFailureHandler(function(e){' +
    '    document.getElementById("msg").innerText="Error: "+e.message;' +
    '  }).saveAllRmPrices(updates);' +
    '}' +
    '</script></body></html>';
}

/** Called from client-side google.script.run in _priceEditorHtml */
function saveAllRmPrices(updates) {
  var saved = 0;
  updates.forEach(function(u) {
    if (u.price >= 0 && updateRmPrice(u.code, u.price)) saved++;
  });
  return saved;
}


// ═══════════════════════════════════════════════════════════════════════════
// FULL MENU WIRING  — replace onOpen in Code.gs with this version
// (or just copy the addItem lines into the existing onOpen)
// ═══════════════════════════════════════════════════════════════════════════

function buildFullMenu() {
  SpreadsheetApp.getUi()
    .createMenu('BOM System')
    // GTP processing
    .addItem('Process GTP from Drive link…', 'showGtpDialog')
    .addSeparator()
    // Viewing
    .addItem('Pricing Summary…',             'showPricingSummaryForSelected')
    .addItem('View BOM Detail…',             'showBomViewer')
    .addSeparator()
    // Config
    .addItem('Update RM Prices…',            'showPriceEditor')
    .addSeparator()
    // Admin
    .addItem('Setup All Sheets',             'setupAllSheets')
    .addItem('Setup Drive Folders',          'setupDriveFolders')
    .addItem('Register Telegram Webhook',    'registerWebhook')
    .addToUi();
}


// ═══════════════════════════════════════════════════════════════════════════
// UTILITY
// ═══════════════════════════════════════════════════════════════════════════

function _esc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
