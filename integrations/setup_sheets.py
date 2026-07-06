"""
One-time setup script — creates the Cable_BOM_System spreadsheet with all sheets.
Run once: python integrations/setup_sheets.py
Re-running is safe: existing sheets are cleared and re-populated.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gspread
from google.oauth2.service_account import Credentials
from config.settings import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_NAME

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER_FMT   = {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.13, "green": 0.29, "blue": 0.53}, "horizontalAlignment": "CENTER"}
EDITABLE_FMT = {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.80}}  # light amber — manually editable


def _sheet(ss, title: str, rows: list[list], cols: int = 20) -> gspread.Worksheet:
    try:
        ws = ss.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=max(len(rows) + 200, 500), cols=max(cols, 20))
        print(f"  Created: {title}")
    ws.clear()
    ws.update("A1", rows)
    ws.format("1:1", HEADER_FMT)
    return ws


def setup():
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)

    try:
        ss = gc.open(SPREADSHEET_NAME)
        print(f"Opened: {SPREADSHEET_NAME}")
    except gspread.exceptions.SpreadsheetNotFound:
        ss = gc.create(SPREADSHEET_NAME)
        ss.share("ekanshbabbar@gmail.com", perm_type="user", role="owner")
        print(f"Created: {SPREADSHEET_NAME}")

    print(f"URL: {ss.url}\nID:  {ss.id}\n")
    print("Creating sheets...")

    # ── RM_Master ─────────────────────────────────────────────────────────────
    rm_rows = [
        ["material_key", "RM Code", "RM Description", "Unit"],
        # Conductors
        ["copper_conductor",       "RM-CU-001", "Copper Conductor Wire / Rod",          "kg/km"],
        ["aluminium_conductor",    "RM-AL-001", "Aluminium Conductor Wire / Rod",        "kg/km"],
        ["drain_wire",             "RM-CU-002", "Bare Copper Drain Wire (7/0.3mm)",      "kg/km"],
        # Insulation compounds
        ["xlpe_insulation",        "RM-IN-001", "XLPE Insulation Compound",              "kg/km"],
        ["pvc_insulation",         "RM-IN-002", "PVC Insulation Compound",               "kg/km"],
        ["pvc_flexible",           "RM-IN-003", "PVC Flexible Compound (IS 694)",        "kg/km"],
        ["rubber_epdm",            "RM-IN-004", "Rubber / EPDM Insulation Compound",     "kg/km"],
        # Sheath compounds
        ["pvc_armoured_sheath",    "RM-SH-001", "PVC Compound — Armoured Sheath",        "kg/km"],
        ["pvc_outer_sheath",       "RM-SH-002", "PVC Compound — Outer Sheath",           "kg/km"],
        ["pvc_inner_sheath",       "RM-SH-003", "PVC Compound — Inner Sheath",           "kg/km"],
        ["pvc_frlsh_sheath",       "RM-SH-004", "PVC / FR-LSH Sheath Compound",          "kg/km"],
        ["hffr_sheath",            "RM-SH-005", "HFFR / ZHFR Sheath Compound",           "kg/km"],
        ["lszh_sheath",            "RM-SH-006", "LSZH Sheath Compound",                  "kg/km"],
        ["frlsh_sheath",           "RM-SH-007", "FR-LSH Sheath Compound",                "kg/km"],
        ["bedding",                "RM-SH-008", "Bedding / Inner Sheath Compound",       "kg/km"],
        # Screen / armour
        ["semicon_screen",         "RM-SC-001", "Semi-conducting Screen Compound",       "kg/km"],
        ["copper_tape_screen",     "RM-SC-002", "Copper Tape Screen",                    "kg/km"],
        ["copper_wire_screen",     "RM-SC-003", "Copper Wire Concentric Screen",         "kg/km"],
        ["gs_flat_strip_armour",   "RM-AR-001", "GS Flat Strip Armour",                  "kg/km"],
        ["gs_round_wire_armour",   "RM-AR-002", "GS Round Wire Armour",                  "kg/km"],
        # Tapes
        ["pe_tape",                "RM-TP-001", "PE Tape (Individual Pair Screen)",       "kg/km"],
        ["al_mylar_pe_tape",       "RM-TP-002", "Al Mylar + PE Laminate Tape Screen",    "kg/km"],
        ["glass_mica_tape",        "RM-TP-003", "Glass Mica Fire Barrier Tape",          "kg/km"],
        ["binder_tape",            "RM-TP-004", "Binder Tape",                           "kg/km"],
        ["binding_tape_pp",        "RM-TP-005", "PP Binding Tape",                       "kg/km"],
        ["swelling_tape",          "RM-TP-006", "Swelling Tape (Water Blocking)",        "kg/km"],
        ["petp_tape",              "RM-TP-007", "PETP Tape",                             "kg/km"],
        # Fillers
        ["pp_filler",              "RM-FL-001", "PP Rope Filler",                        "kg/km"],
        ["pvc_filler",             "RM-FL-002", "PVC Filler (Sector Cables)",            "kg/km"],
        ["filler_compound",        "RM-FL-003", "Filler Compound",                       "kg/km"],
    ]
    ws_rm = _sheet(ss, "RM_Master", rm_rows, cols=4)
    ws_rm.format("A2:A100", {"textFormat": {"fontFamily": "Courier New"}})
    print("  RM_Master populated")

    # ── GTP_Registry ──────────────────────────────────────────────────────────
    reg_headers = [
        "Min Margin %",
        "GTP No.", "Item No.", "Item Name", "Item Code",
        "Cable Family", "Voltage Grade", "No. of Cores",
        "Conductor Area (mm²)", "Conductor Material", "Conductor Shape",
        "Insulation", "Armour", "Sheath", "Overall OD (mm)",
        "Price — Type A (₹/km)", "Price — Type B (₹/km)", "Price — Type C (₹/km)",
        "Created At", "Last Updated",
    ]
    ws_reg = _sheet(ss, "GTP_Registry", [reg_headers], cols=len(reg_headers))
    # Amber highlight on Min Margin % column (col A) to signal it's manually editable
    ws_reg.format("A2:A1000", EDITABLE_FMT)
    ws_reg.freeze(rows=1)
    ws_reg.set_basic_filter()

    # ── BOM_Production ────────────────────────────────────────────────────────
    bom_headers = [
        "BOM No.", "GTP No.", "BOM Type", "Item No.", "Item Name", "Item Code",
        "RM Code", "RM Description", "Weight (kg/km)",
    ]
    ws_prod = _sheet(ss, "BOM_Production", [bom_headers], cols=len(bom_headers))
    ws_prod.freeze(rows=1)
    ws_prod.set_basic_filter()

    # ── BOM_Costing ───────────────────────────────────────────────────────────
    ws_cost = _sheet(ss, "BOM_Costing", [bom_headers], cols=len(bom_headers))
    ws_cost.freeze(rows=1)
    ws_cost.set_basic_filter()

    # ── Config/Materials ──────────────────────────────────────────────────────
    _sheet(ss, "Config/Materials", [
        ["material_code", "material_name", "density_costing", "density_production",
         "rm_price_per_kg", "unit", "last_updated", "notes"],
        ["copper_conductor",     "Copper Conductor",          8.89,  8.89,  0, "g/cm3", "", ""],
        ["aluminium_conductor",  "Aluminium Conductor",       2.703, 2.703, 0, "g/cm3", "", ""],
        ["xlpe_insulation",      "XLPE Insulation",           0.92,  0.92,  0, "g/cm3", "", ""],
        ["pvc_insulation",       "PVC Insulation",            1.40,  1.40,  0, "g/cm3", "", ""],
        ["semicon_screen",       "Semi-con Screen",           1.20,  1.20,  0, "g/cm3", "", ""],
        ["pvc_flexible",         "PVC Flexible",              1.50,  1.50,  0, "g/cm3", "", "IS 694"],
        ["pvc_armoured_sheath",  "PVC Armoured Sheath",       1.60,  1.60,  0, "g/cm3", "", ""],
        ["pvc_outer_sheath",     "PVC Outer Sheath",          1.60,  1.60,  0, "g/cm3", "", ""],
        ["pvc_inner_sheath",     "PVC Inner Sheath",          1.60,  1.60,  0, "g/cm3", "", ""],
        ["frlsh_sheath",         "FR-LSH Sheath",             1.50,  1.50,  0, "g/cm3", "", ""],
        ["hffr_sheath",          "HFFR Sheath",               1.50,  1.50,  0, "g/cm3", "", ""],
        ["lszh_sheath",          "LSZH Sheath",               1.50,  1.50,  0, "g/cm3", "", ""],
        ["gs_flat_strip_armour", "GS Flat Strip Armour",      7.85,  7.85,  0, "g/cm3", "", ""],
        ["gs_round_wire_armour", "GS Round Wire Armour",      7.85,  7.85,  0, "g/cm3", "", ""],
        ["copper_tape_screen",   "Copper Tape Screen",        8.89,  8.89,  0, "g/cm3", "", ""],
        ["copper_wire_screen",   "Copper Wire Screen",        8.89,  8.89,  0, "g/cm3", "", ""],
        ["filler_compound",      "Filler Compound",           1.40,  1.40,  0, "g/cm3", "", ""],
        ["binder_tape",          "Binder Tape",               1.35,  1.35,  0, "g/cm3", "", ""],
        ["binding_tape_pp",      "PP Binding Tape",           0.91,  0.91,  0, "g/cm3", "", ""],
        ["swelling_tape",        "Swelling Tape",             1.00,  1.00,  0, "g/cm3", "", ""],
        ["petp_tape",            "PETP Tape",                 1.39,  1.39,  0, "g/cm3", "", ""],
        ["pe_tape",              "PE Tape",                   1.50,  1.50,  0, "g/cm3", "", "Individual pair screen"],
        ["al_mylar_pe_tape",     "Al Mylar + PE Tape",        1.50,  1.50,  0, "g/cm3", "", "Individual pair screen"],
        ["glass_mica_tape",      "Glass Mica Tape",           1.40,  1.40,  0, "g/cm3", "", "Fire survival"],
        ["pp_filler",            "PP Rope Filler",            0.91,  0.91,  0, "g/cm3", "", ""],
        ["pvc_filler",           "PVC Filler",                1.70,  1.70,  0, "g/cm3", "", "Sector cables"],
        ["rubber_epdm",          "Rubber / EPDM",             1.35,  1.35,  0, "g/cm3", "", ""],
    ])

    # ── Config/Lay_Factors ────────────────────────────────────────────────────
    _sheet(ss, "Config/Lay_Factors", [
        ["category", "condition", "min_wires", "max_wires", "costing_value", "production_value", "notes"],
        ["conductor", "1 wire",      1,   1,   1.000, 1.000, "Solid / compacted — no lay factor"],
        ["conductor", "Up to 7",     2,   7,   1.005, 1.005, ""],
        ["conductor", "8 to 19",     8,   19,  1.010, 1.010, ""],
        ["conductor", "20 to 37",    20,  37,  1.020, 1.020, ""],
        ["conductor", "38 to 61",    38,  61,  1.025, 1.025, ""],
        ["conductor", "62+",         62,  999, 1.030, 1.030, "Fine wire flexible"],
        ["cabling",   "All",         "",  "",  1.008, 1.007, "Applied to per-core layers during cabling"],
        ["armour",    "All",         "",  "",  1.008, 1.007, "Applied to armour layer"],
    ])

    # ── Config/Extrusion_Tolerances ───────────────────────────────────────────
    _sheet(ss, "Config/Extrusion_Tolerances", [
        ["thickness_type", "band", "min_mm", "max_mm", "costing_factor", "production_factor", "notes"],
        ["Nominal", "thin",   0.0, 1.0,  1.10, 1.00, "< 1mm"],
        ["Nominal", "medium", 1.0, 2.0,  1.08, 1.00, "1–2mm"],
        ["Nominal", "thick",  2.0, 99.0, 1.05, 1.00, "> 2mm"],
        ["Minimum", "thin",   0.0, 1.0,  1.15, 1.05, "GTP states minimum < 1mm"],
        ["Minimum", "medium", 1.0, 2.0,  1.12, 1.05, "GTP states minimum 1–2mm"],
        ["Minimum", "thick",  2.0, 99.0, 1.08, 1.05, "GTP states minimum > 2mm"],
    ])

    # ── Config/GTP_Types ──────────────────────────────────────────────────────
    _sheet(ss, "Config/GTP_Types", [
        ["product_type", "gtp_suffix", "conductor_resistance_factor", "armour_coverage", "strip_thickness", "notes"],
        ["LT_XLPE_UNARM",    "A", 1.000, 1.000, "", ""],
        ["LT_XLPE_UNARM",    "B", 0.920, 1.000, "", ""],
        ["LT_XLPE_UNARM",    "C", 0.900, 1.000, "", ""],
        ["LT_XLPE_ARMOURED", "A", 1.000, 0.900, "", ""],
        ["LT_XLPE_ARMOURED", "B", 0.920, 0.800, "", ""],
        ["LT_XLPE_ARMOURED", "C", 0.900, 0.800, "", ""],
        ["HT_11KV",          "A", 1.000, 0.900, "", ""],
        ["HT_11KV",          "B", 0.920, 0.800, "", ""],
        ["HT_11KV",          "C", 0.900, 0.800, "", ""],
        ["LT_PVC",           "A", 1.000, 0.900, "", ""],
        ["LT_PVC",           "B", 0.920, 0.800, "", ""],
        ["LT_PVC",           "C", 0.900, 0.800, "", ""],
        ["LT_PVC_FLEX",      "A", 1.000, 1.000, "", ""],
        ["LT_PVC_FLEX",      "B", 0.920, 1.000, "", ""],
        ["LT_PVC_FLEX",      "C", 0.900, 1.000, "", ""],
        ["INSTR_SCREENED",   "A", 1.000, 1.000, "", ""],
        ["INSTR_SCREENED",   "B", 0.920, 1.000, "", ""],
        ["INSTR_SCREENED",   "C", 0.900, 1.000, "", ""],
        ["FIRE_SURVIVAL",    "A", 1.000, 0.900, "", ""],
        ["FIRE_SURVIVAL",    "B", 0.920, 0.800, "", ""],
        ["FIRE_SURVIVAL",    "C", 0.900, 0.800, "", ""],
    ])

    # ── Config/Margins ────────────────────────────────────────────────────────
    _sheet(ss, "Config/Margins", [
        ["product_family", "min_area_mm2", "max_area_mm2", "margin_pct", "notes"],
        ["LT_PVC",           0,    2.5,  20.0, ""],
        ["LT_PVC",           4,    10,   18.0, ""],
        ["LT_PVC",           16,   500,  15.0, ""],
        ["LT_PVC_FLEX",      0,    2.5,  20.0, ""],
        ["LT_PVC_FLEX",      4,    10,   18.0, ""],
        ["LT_PVC_FLEX",      16,   500,  15.0, ""],
        ["LT_XLPE_UNARM",    0,    10,   18.0, ""],
        ["LT_XLPE_UNARM",    16,   70,   16.0, ""],
        ["LT_XLPE_UNARM",    95,   300,  14.0, ""],
        ["LT_XLPE_UNARM",    400,  9999, 12.0, ""],
        ["LT_XLPE_ARMOURED", 0,    10,   18.0, ""],
        ["LT_XLPE_ARMOURED", 16,   70,   16.0, ""],
        ["LT_XLPE_ARMOURED", 95,   300,  14.0, ""],
        ["LT_XLPE_ARMOURED", 400,  9999, 12.0, ""],
        ["HT_11KV",          0,    70,   15.0, ""],
        ["HT_11KV",          95,   300,  13.0, ""],
        ["HT_11KV",          400,  9999, 11.0, ""],
        ["INSTR_SCREENED",   0,    9999, 18.0, ""],
        ["FIRE_SURVIVAL",    0,    9999, 17.0, ""],
    ])

    # ── Config/Drums ──────────────────────────────────────────────────────────
    _sheet(ss, "Config/Drums", [
        ["product_type", "size_range_from_mm2", "size_range_to_mm2",
         "drum_type", "drum_length_m", "cost_per_drum", "notes"],
        ["LT_XLPE_UNARM",    0,   16,   "wooden", 1000, 0, ""],
        ["LT_XLPE_UNARM",    25,  120,  "wooden", 1000, 0, ""],
        ["LT_XLPE_UNARM",    150, 500,  "wooden", 500,  0, ""],
        ["LT_XLPE_ARMOURED", 0,   16,   "wooden", 1000, 0, ""],
        ["LT_XLPE_ARMOURED", 25,  120,  "wooden", 1000, 0, ""],
        ["LT_XLPE_ARMOURED", 150, 500,  "wooden", 500,  0, ""],
        ["LT_XLPE_ARMOURED", 0,   16,   "steel",  1000, 0, ""],
        ["LT_XLPE_ARMOURED", 25,  120,  "steel",  1000, 0, ""],
        ["LT_XLPE_ARMOURED", 150, 500,  "steel",  500,  0, ""],
        ["HT_11KV",          0,   500,  "steel",  500,  0, ""],
        ["LT_PVC",           0,   10,   "wooden", 1000, 0, ""],
        ["LT_PVC",           10,  500,  "wooden", 1000, 0, ""],
        ["LT_PVC_FLEX",      0,   10,   "wooden", 1000, 0, ""],
        ["LT_PVC_FLEX",      10,  500,  "wooden", 1000, 0, ""],
        ["INSTR_SCREENED",   0,   500,  "wooden", 1000, 0, ""],
        ["FIRE_SURVIVAL",    0,   500,  "wooden", 1000, 0, ""],
    ])

    # ── Config/Operations ─────────────────────────────────────────────────────
    _sheet(ss, "Config/Operations", [
        ["operation_name", "cable_family", "sequence_order",
         "waste_pct_costing", "waste_pct_production", "notes"],
        ["Stranding",                    "LT_XLPE_ARMOURED", 1, 0, 0, ""],
        ["Insulation Extrusion (XLPE)",  "LT_XLPE_ARMOURED", 2, 0, 0, ""],
        ["Cabling/Laying Up",            "LT_XLPE_ARMOURED", 3, 0, 0, ""],
        ["Bedding Extrusion",            "LT_XLPE_ARMOURED", 4, 0, 0, ""],
        ["Armoring",                     "LT_XLPE_ARMOURED", 5, 0, 0, ""],
        ["Outer Sheath Extrusion",       "LT_XLPE_ARMOURED", 6, 0, 0, ""],
        ["Stranding",                    "LT_XLPE_UNARM",    1, 0, 0, ""],
        ["Insulation Extrusion (XLPE)",  "LT_XLPE_UNARM",    2, 0, 0, ""],
        ["Cabling/Laying Up",            "LT_XLPE_UNARM",    3, 0, 0, ""],
        ["Outer Sheath Extrusion",       "LT_XLPE_UNARM",    4, 0, 0, ""],
        ["3-in-1 Extrusion",             "HT_11KV",          1, 0, 0, "CS + XLPE + IS"],
        ["Copper Tape Wrapping",         "HT_11KV",          2, 0, 0, ""],
        ["Cabling/Laying Up",            "HT_11KV",          3, 0, 0, ""],
        ["Armoring",                     "HT_11KV",          4, 0, 0, ""],
        ["Outer Sheath Extrusion",       "HT_11KV",          5, 0, 0, ""],
        ["Bunching",                     "LT_PVC",           1, 0, 0, ""],
        ["Insulation Extrusion (PVC)",   "LT_PVC",           2, 0, 0, ""],
        ["Cabling/Laying Up",            "LT_PVC",           3, 0, 0, ""],
        ["Outer Sheath Extrusion",       "LT_PVC",           4, 0, 0, ""],
    ])

    # ── Config/Formulas ───────────────────────────────────────────────────────
    _sheet(ss, "Config/Formulas", [
        ["formula_name", "formula_text", "variables", "notes"],
        ["Conductor Weight",
         "weight = area × density × lay_factor",
         "area = ρ / R_DC_per_m × cr_factor; ρ_Cu=1/58; ρ_Al=1/35",
         "CR factor: A=1.0, B=0.92, C=0.90"],
        ["Annular Layer Weight",
         "weight = (π/4) × (OD² - ID²) × density × lay_factor",
         "OD = ID + 2×t_eff; t_eff = t_nominal × tolerance_factor",
         "Insulation, sheaths, screens"],
        ["Tape Wrap Weight",
         "weight = π × (D+t) × (1+overlap/100) × t × density × n_layers",
         "n_layers = n_tapes or n_pairs as applicable",
         "Glass mica: 0.12/0.11mm; PE/Al Mylar: 0.04mm default"],
        ["Armour Weight (simplified)",
         "weight = (coverage/100) × π × D × t × density",
         "Flat strip: t=strip_thickness; Round wire: t = π/4 × wire_dia",
         "Coverage: A=90%, B=80%, C=80%"],
        ["Selling Price",
         "price = total_cost / (1 - margin/100)",
         "total_cost = material + drum + conversion",
         "Min Margin % from GTP_Registry col A overrides Config/Margins"],
    ])

    # Remove default Sheet1 if present
    try:
        ss.del_worksheet(ss.worksheet("Sheet1"))
    except Exception:
        pass

    print(f"\nSetup complete!")
    print(f"Spreadsheet URL: {ss.url}")
    print(f"Add to .env → SPREADSHEET_ID={ss.id}")
    return ss.id


if __name__ == "__main__":
    setup()
