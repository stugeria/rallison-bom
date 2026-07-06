// ============================================================
// Setup.gs — Run these functions ONCE to configure everything
// ============================================================

// STEP 1: Run this first — creates the Google Sheet with all tabs
function createSpreadsheet() {
  var ss = SpreadsheetApp.create("Cable_BOM_System");

  // Share with yourself
  ss.addEditor(Session.getActiveUser().getEmail());

  Logger.log("Created spreadsheet: " + ss.getUrl());
  Logger.log("Spreadsheet ID: " + ss.getId());

  // Save the ID so other functions can find it
  PropertiesService.getScriptProperties().setProperty("SPREADSHEET_ID", ss.getId());

  createAllSheets(ss);
  Logger.log("\nSpreadsheet ready! URL: " + ss.getUrl());
  Logger.log("SPREADSHEET_ID saved to Script Properties: " + ss.getId());
}

// If the spreadsheet already exists, run this to (re)create all sheets in it
function setupExistingSpreadsheet(spreadsheetId) {
  var id = spreadsheetId || PropertiesService.getScriptProperties().getProperty("SPREADSHEET_ID");
  var ss = SpreadsheetApp.openById(id);
  createAllSheets(ss);
  Logger.log("Sheets created in: " + ss.getUrl());
}

function createAllSheets(ss) {
  var sheets = [
    // Config sheets
    ["Config/Materials",          materialsData()],
    ["Config/Lay_Factors",        layFactorsData()],
    ["Config/Extrusion_Tolerances", extrusionTolData()],
    ["Config/GTP_Types",          gtpTypesData()],
    ["Config/Drums",              drumsData()],
    ["Config/Margins",            marginsData()],
    ["Config/Operations",         operationsData()],
    ["Config/Formulas",           formulasData()],
    // Data sheets
    ["BOM_Results",               bomResultsHeader()],
    ["Costing_Results",           costingResultsHeader()],
    ["GTP_Log",                   gtpLogHeader()],
  ];

  sheets.forEach(function(item) {
    var name = item[0];
    var data = item[1];
    var ws = ss.getSheetByName(name);
    if (!ws) {
      ws = ss.insertSheet(name);
    }
    ws.clearContents();
    var range = ws.getRange(1, 1, data.length, data[0].length);
    range.setValues(data);
    // Style header row
    ws.getRange(1, 1, 1, data[0].length)
      .setBackground("#003366")
      .setFontColor("#FFFFFF")
      .setFontWeight("bold");
    ws.setFrozenRows(1);
    Logger.log("Created sheet: " + name);
  });

  // Remove default "Sheet1" if present
  var defaultSheet = ss.getSheetByName("Sheet1");
  if (defaultSheet && ss.getSheets().length > 1) {
    ss.deleteSheet(defaultSheet);
  }
}

// STEP 2: Run this to register the Telegram webhook
// Before running: deploy the script as a Web App (Publish > Deploy as web app)
// Then paste the deployment URL as webAppUrl below
function registerTelegramWebhook() {
  var webAppUrl = PropertiesService.getScriptProperties().getProperty("WEB_APP_URL");
  if (!webAppUrl) {
    Logger.log("ERROR: Set WEB_APP_URL in Script Properties first");
    Logger.log("1. Deploy this script as a Web App");
    Logger.log("2. Copy the URL");
    Logger.log('3. Run: PropertiesService.getScriptProperties().setProperty("WEB_APP_URL", "<url>")');
    return;
  }
  var token = PropertiesService.getScriptProperties().getProperty("TELEGRAM_BOT_TOKEN");
  var url = "https://api.telegram.org/bot" + token + "/setWebhook?url=" + encodeURIComponent(webAppUrl);
  var resp = UrlFetchApp.fetch(url);
  Logger.log("Telegram webhook response: " + resp.getContentText());
}

// Run this to check webhook status
function checkTelegramWebhook() {
  var token = PropertiesService.getScriptProperties().getProperty("TELEGRAM_BOT_TOKEN");
  var resp = UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/getWebhookInfo");
  Logger.log(resp.getContentText());
}

// Helper to set all required properties at once
function setScriptProperties() {
  // FILL IN YOUR VALUES BEFORE RUNNING
  PropertiesService.getScriptProperties().setProperties({
    "ANTHROPIC_API_KEY":  "sk-ant-FILL_IN",
    "TELEGRAM_BOT_TOKEN": "FILL_IN",
    "COMPANY_NAME":       "FILL_IN (e.g. ABC Cables)",
    // WEB_APP_URL and SPREADSHEET_ID are set by createSpreadsheet() and registerTelegramWebhook()
  });
  Logger.log("Script properties set.");
}


// ── Sheet Data ────────────────────────────────────────────────

function materialsData() {
  return [
    ["material_code","material_name","density_costing","density_production","rm_price_per_kg","unit","last_updated","notes"],
    ["copper_conductor","Copper Conductor",8.89,8.89,0,"g/cm3","",""],
    ["aluminium_conductor","Aluminium Conductor",2.703,2.703,0,"g/cm3","",""],
    ["xlpe_insulation","XLPE Insulation Compound",0.92,0.92,0,"g/cm3","",""],
    ["semicon_screen","Semi-con Screen Compound",1.20,1.20,0,"g/cm3","","Confirm density"],
    ["pvc_flexible","PVC Compound (Flexible)",1.50,1.50,0,"g/cm3","","IS 694 cables"],
    ["pvc_armoured_sheath","PVC Compound (Armoured)",1.60,1.60,0,"g/cm3","","Inner/outer sheath armoured"],
    ["frlsh_sheath","FR-LSH Sheath Compound",1.50,1.50,0,"g/cm3","","Confirm compound grade"],
    ["gs_flat_strip_armour","GS Flat Strip Armour",7.85,7.85,0,"g/cm3","","Galvanised steel"],
    ["copper_tape_screen","Copper Tape Screen",8.89,8.89,0,"g/cm3","","HT 11kV cables"],
    ["filler_compound","Filler Compound",1.40,1.40,0,"g/cm3","",""],
    ["binder_tape","Binder Tape",1.35,1.35,0,"g/cm3","",""],
  ];
}

function layFactorsData() {
  return [
    ["category","condition","min_wires","max_wires","costing_value","production_value","notes"],
    ["conductor","Up to 7 wires",1,7,1.005,1.005,""],
    ["conductor","8 to 19 wires",8,19,1.010,1.010,""],
    ["conductor","20 to 37 wires",20,37,1.020,1.020,""],
    ["conductor","37+ wires",38,999,1.025,1.025,""],
    ["cabling","All","","",1.008,1.007,"Apply to outer layers"],
    ["armour","All","","",1.008,1.007,"Apply to armour layer"],
  ];
}

function extrusionTolData() {
  return [
    ["thickness_type","band","min_mm","max_mm","costing_factor","production_factor","notes"],
    ["Nominal","thin",0.0,1.0,1.10,1.00,"< 1mm nominal"],
    ["Nominal","medium",1.0,2.0,1.08,1.00,"1-2mm nominal"],
    ["Nominal","thick",2.0,99.0,1.05,1.00,"> 2mm nominal"],
    ["Minimum","thin",0.0,1.0,1.15,1.05,"Minimum < 1mm"],
    ["Minimum","medium",1.0,2.0,1.12,1.05,"Minimum 1-2mm"],
    ["Minimum","thick",2.0,99.0,1.08,1.05,"Minimum > 2mm"],
  ];
}

function gtpTypesData() {
  return [
    ["product_type","gtp_suffix","conductor_resistance_factor","armour_coverage","strip_thickness","notes"],
    ["LT_XLPE_UNARM","A",1.000,1.000,"","Standard"],
    ["LT_XLPE_UNARM","B",1.000,1.000,"","Intermediate"],
    ["LT_XLPE_UNARM","C",1.000,1.000,"","Premium"],
    ["LT_XLPE_ARMOURED","A",1.000,1.000,"","Standard"],
    ["LT_XLPE_ARMOURED","B",1.000,1.000,"","Intermediate"],
    ["LT_XLPE_ARMOURED","C",1.000,1.000,"","Premium"],
    ["HT_11KV","A",1.000,1.000,"","Standard"],
    ["HT_11KV","B",1.000,1.000,"","Intermediate"],
    ["HT_11KV","C",1.000,1.000,"","Premium"],
    ["LT_PVC","A",1.000,1.000,"","Standard"],
    ["LT_PVC","B",1.000,1.000,"","Intermediate"],
    ["LT_PVC","C",1.000,1.000,"","Premium"],
  ];
}

function drumsData() {
  return [
    ["product_type","size_range_from_mm2","size_range_to_mm2","drum_type","drum_length_m","cost_per_drum","cost_per_km","notes"],
    ["LT_XLPE_UNARM",0,16,"wooden",1000,0,0,"Enter actual drum cost"],
    ["LT_XLPE_UNARM",25,120,"wooden",1000,0,0,""],
    ["LT_XLPE_UNARM",150,500,"wooden",500,0,0,""],
    ["LT_XLPE_ARMOURED",0,16,"wooden",1000,0,0,""],
    ["LT_XLPE_ARMOURED",25,120,"wooden",1000,0,0,""],
    ["LT_XLPE_ARMOURED",150,500,"wooden",500,0,0,""],
    ["LT_XLPE_ARMOURED",0,16,"steel",1000,0,0,""],
    ["LT_XLPE_ARMOURED",25,120,"steel",1000,0,0,""],
    ["LT_XLPE_ARMOURED",150,500,"steel",500,0,0,""],
    ["HT_11KV",0,500,"steel",500,0,0,"HT cables on steel drums"],
    ["LT_PVC",0,10,"wooden",1000,0,0,""],
    ["LT_PVC",10,500,"wooden",1000,0,0,""],
  ];
}

function marginsData() {
  return [
    ["product_family","min_area_mm2","max_area_mm2","margin_pct","notes"],
    ["LT_PVC",0,2.5,20.0,"Small flexible cables — higher margin"],
    ["LT_PVC",4,10,18.0,""],
    ["LT_PVC",16,500,15.0,""],
    ["LT_XLPE_UNARM",0,10,18.0,""],
    ["LT_XLPE_UNARM",16,70,16.0,""],
    ["LT_XLPE_UNARM",95,300,14.0,""],
    ["LT_XLPE_UNARM",400,9999,12.0,"Large cables — lower margin"],
    ["LT_XLPE_ARMOURED",0,10,18.0,""],
    ["LT_XLPE_ARMOURED",16,70,16.0,""],
    ["LT_XLPE_ARMOURED",95,300,14.0,""],
    ["LT_XLPE_ARMOURED",400,9999,12.0,""],
    ["HT_11KV",0,70,15.0,""],
    ["HT_11KV",95,300,13.0,""],
    ["HT_11KV",400,9999,11.0,""],
  ];
}

function operationsData() {
  return [
    ["operation_name","cable_family","sequence_order","waste_pct_costing","waste_pct_production","notes"],
    ["Bunching/Stranding","LT_PVC",1,0,0,""],
    ["Insulation Extrusion (PVC)","LT_PVC",2,0,0,""],
    ["Core Colour/Numbering","LT_PVC",3,0,0,""],
    ["Cabling/Laying Up","LT_PVC",4,0,0,""],
    ["Outer Sheath Extrusion","LT_PVC",5,0,0,""],
    ["Drum Winding","LT_PVC",6,0,0,""],
    ["Stranding","LT_XLPE_UNARM",1,0,0,""],
    ["Insulation Extrusion (XLPE)","LT_XLPE_UNARM",2,0,0,""],
    ["Core Colour/Numbering","LT_XLPE_UNARM",3,0,0,""],
    ["Cabling/Laying Up","LT_XLPE_UNARM",4,0,0,""],
    ["Outer Sheath Extrusion","LT_XLPE_UNARM",5,0,0,""],
    ["Drum Winding","LT_XLPE_UNARM",6,0,0,""],
    ["Stranding","LT_XLPE_ARMOURED",1,0,0,""],
    ["Insulation Extrusion (XLPE)","LT_XLPE_ARMOURED",2,0,0,""],
    ["Core Colour/Numbering","LT_XLPE_ARMOURED",3,0,0,""],
    ["Cabling/Laying Up","LT_XLPE_ARMOURED",4,0,0,""],
    ["Bedding Tape/Extrusion","LT_XLPE_ARMOURED",5,0,0,""],
    ["Armoring","LT_XLPE_ARMOURED",6,0,0,""],
    ["Outer Sheath Extrusion","LT_XLPE_ARMOURED",7,0,0,""],
    ["Drum Winding","LT_XLPE_ARMOURED",8,0,0,""],
    ["Stranding","HT_11KV",1,0,0,""],
    ["3-in-1 Extrusion (CS+XLPE+IS)","HT_11KV",2,0,0,"Cond.Screen + XLPE + Ins.Screen"],
    ["Core Identification","HT_11KV",3,0,0,""],
    ["Copper Tape Wrapping","HT_11KV",4,0,0,""],
    ["Cabling/Laying Up","HT_11KV",5,0,0,""],
    ["Bedding Extrusion","HT_11KV",6,0,0,""],
    ["Armoring","HT_11KV",7,0,0,""],
    ["Outer Sheath Extrusion","HT_11KV",8,0,0,""],
    ["Drum Winding","HT_11KV",9,0,0,""],
  ];
}

function formulasData() {
  return [
    ["formula_name","formula_text","variables","notes"],
    ["Conductor Area","area_mm2 = (rho / R_DC_per_m) × resistance_factor","rho_Cu=1/58, rho_Al=1/35 (Ω·mm²/m); R_DC_per_m = R_DC_per_km/1000","Effective area from measured resistance"],
    ["Conductor Weight","weight_kg_per_km = area × density × lay_factor","area from formula above; lay_factor from Lay_Factors sheet",""],
    ["Annular Layer Weight","weight_kg_per_km = (π/4) × (OD² - ID²) × density","OD = ID + 2×effective_thickness; effective_thickness = nominal × tolerance_factor","Used for insulation, sheaths, bedding"],
    ["Extrusion Tolerance","effective_thickness = nominal × tolerance_factor(thickness_type, band, bom_type)","Band: thin (<1mm), medium (1-2mm), thick (>2mm); type: Nominal or Minimum","Costing uses higher factor; Production lower"],
    ["Armour Num Strips","num_strips = ROUND(π × (D + strip_t) / (strip_w + gap))","D=OD under armour; gap=1mm default","IS 7098 — confirm with user"],
    ["Armour Weight","weight = strips × width × thickness × 7.85 × lay_factor × coverage_factor","coverage_factor from GTP_Types sheet by A/B/C suffix",""],
    ["Copper Tape Screen","weight = π × mean_OD × (1 + overlap/100) × tape_t × 8.89 × num_cores","mean_OD = OD at mid-tape; overlap from GTP","PENDING IS 7098-2 confirmation"],
    ["Drum Cost/km","drum_cost_per_km = cost_per_drum / (drum_length_m / 1000)","drum costs from Drums sheet","Both wooden and steel drum costs tracked"],
    ["Selling Price","selling_price = total_cost / (1 - margin/100)","total = material + drum + conversion cost","margin from Margins sheet by family + area"],
  ];
}

function bomResultsHeader() {
  return [["gtp_ref","gtp_type","cable_designation","config","voltage_kv","layer","material","qty_costing_kg_per_km","qty_production_kg_per_km","unit","date"]];
}

function costingResultsHeader() {
  return [["gtp_ref","gtp_type","cable_designation","config","material_cost_per_km","drum_cost_per_km","conversion_cost_per_km","total_cost_per_km","margin_pct","floor_price_per_km","selling_price_per_km","selling_price_per_drum_length","delivery_length_m","drum_type","date"]];
}

function gtpLogHeader() {
  return [["gtp_ref","customer","cable_types_count","date_received","date_processed","status"]];
}
