"""
Google Sheets client — abstracted so it can be swapped for PostgreSQL.
All public methods mirror what a DB adapter would expose.
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from config.settings import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_NAME, SPREADSHEET_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Output sheet names ───────────────────────────────────────────────────────
SHEET_GTP_REGISTRY   = "GTP_Registry"
SHEET_BOM_PRODUCTION = "BOM_Production"
SHEET_BOM_COSTING    = "BOM_Costing"
SHEET_RM_MASTER      = "RM_Master"

# ── Config sheet names ───────────────────────────────────────────────────────
SHEET_MATERIALS       = "Config/Materials"
SHEET_LAY_FACTORS     = "Config/Lay_Factors"
SHEET_GTP_TYPES       = "Config/GTP_Types"
SHEET_OPERATIONS      = "Config/Operations"
SHEET_FORMULAS        = "Config/Formulas"
SHEET_DRUMS           = "Config/Drums"
SHEET_MARGINS         = "Config/Margins"
SHEET_EXTRUSION_TOL   = "Config/Extrusion_Tolerances"

# Column indices in GTP_Registry (1-based, for cell updates)
_REG_COL_GTP_NO    = 2
_REG_COL_ITEM_NO   = 4   # Item No. (internal sequence within GTP)
_REG_COL_PRICE_A   = 15
_REG_COL_PRICE_B   = 16
_REG_COL_PRICE_C   = 17
_REG_COL_UPDATED   = 19


class SheetsClient:
    def __init__(self):
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        if SPREADSHEET_ID:
            self._ss = self._gc.open_by_key(SPREADSHEET_ID)
        else:
            self._ss = self._gc.open(SPREADSHEET_NAME)
        self._rm_cache: dict | None = None

    def _ws(self, name: str) -> gspread.Worksheet:
        try:
            return self._ss.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            raise ValueError(f"Sheet '{name}' not found in spreadsheet '{SPREADSHEET_NAME}'")

    # ── Config read helpers ──────────────────────────────────────────────────

    def get_all_records(self, sheet_name: str) -> list[dict]:
        return self._ws(sheet_name).get_all_records()

    def get_rm_prices(self) -> dict:
        """Returns {material_key: rm_price_per_kg}"""
        rows = self.get_all_records(SHEET_MATERIALS)
        return {r["material_code"]: float(r["rm_price_per_kg"]) for r in rows if r.get("rm_price_per_kg")}

    def get_density_overrides(self, bom_type: str) -> dict:
        rows = self.get_all_records(SHEET_MATERIALS)
        col = "density_costing" if bom_type == "costing" else "density_production"
        return {r["material_code"]: float(r[col]) for r in rows if r.get(col)}

    def get_lay_factor_overrides(self, bom_type: str) -> dict:
        rows = self.get_all_records(SHEET_LAY_FACTORS)
        result = {"conductor": [], "cabling": {}, "armour": {}}
        for r in rows:
            cat = r.get("category", "").lower()
            if cat == "conductor":
                result["conductor"].append({
                    "min_wires": int(r["min_wires"]),
                    "max_wires": int(r["max_wires"]),
                    bom_type: float(r[f"{bom_type}_value"]),
                })
            elif cat in ("cabling", "armour"):
                result[cat][bom_type] = float(r[f"{bom_type}_value"])
        return result

    def get_tolerance_table(self, bom_type: str) -> list:
        rows = self.get_all_records(SHEET_EXTRUSION_TOL)
        return [
            (r["thickness_type"], r["band"], float(r["min_mm"]), float(r["max_mm"]),
             float(r["costing_factor"]), float(r["production_factor"]))
            for r in rows
        ]

    def get_gtp_type_factors(self, product_type: str, bom_type: str) -> dict:
        """bom_type here = 'A', 'B', or 'C'."""
        rows = self.get_all_records(SHEET_GTP_TYPES)
        for r in rows:
            if r["product_type"] == product_type and r["gtp_suffix"].upper() == bom_type.upper():
                return {
                    "conductor_resistance_factor": float(r.get("conductor_resistance_factor", 1.0)),
                    "armour_coverage": float(r.get("armour_coverage", 1.0)),
                    "strip_thickness_override": r.get("strip_thickness") or None,
                }
        return {"conductor_resistance_factor": 1.0, "armour_coverage": 1.0}

    def get_margins(self) -> list[dict]:
        return self.get_all_records(SHEET_MARGINS)

    def get_drum_costs(self) -> list[dict]:
        return self.get_all_records(SHEET_DRUMS)

    # ── RM Master ────────────────────────────────────────────────────────────

    def get_rm_code_map(self) -> dict:
        """Returns {material_key: {"rm_code": str, "rm_description": str}}. Cached."""
        if self._rm_cache is not None:
            return self._rm_cache
        rows = self.get_all_records(SHEET_RM_MASTER)
        self._rm_cache = {
            r["material_key"]: {"rm_code": r["RM Code"], "rm_description": r["RM Description"]}
            for r in rows if r.get("material_key")
        }
        return self._rm_cache

    # ── GTP Registry ─────────────────────────────────────────────────────────

    def check_gtp_exists(self, gtp_no: str, item_no: str) -> tuple[bool, int, dict]:
        """
        Returns (exists, row_number, registry_row_dict).
        row_number is 1-based (row 1 = header). Returns 0 if not found.
        """
        ws = self._ws(SHEET_GTP_REGISTRY)
        records = ws.get_all_records()
        for i, row in enumerate(records, start=2):
            if str(row.get("GTP No.", "")) == str(gtp_no) and str(row.get("Item No.", "")) == str(item_no):
                return True, i, row
        return False, 0, {}

    def append_gtp_registry(self, record: dict):
        """Appends one row to GTP_Registry. record keys must match header columns."""
        ws = self._ws(SHEET_GTP_REGISTRY)
        headers = ws.row_values(1)
        row = [record.get(h, "") for h in headers]
        ws.append_row(row, value_input_option="USER_ENTERED")

    def update_gtp_registry_prices(self, row_num: int, price_a: float, price_b: float, price_c: float):
        ws = self._ws(SHEET_GTP_REGISTRY)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        ws.update_cell(row_num, _REG_COL_PRICE_A, round(price_a, 2))
        ws.update_cell(row_num, _REG_COL_PRICE_B, round(price_b, 2))
        ws.update_cell(row_num, _REG_COL_PRICE_C, round(price_c, 2))
        ws.update_cell(row_num, _REG_COL_UPDATED, now)

    # ── BOM sheets ───────────────────────────────────────────────────────────

    def append_bom_rows(self, rows: list[dict], sheet_name: str):
        """Batch-appends rows to BOM_Production or BOM_Costing."""
        if not rows:
            return
        ws = self._ws(sheet_name)
        headers = ws.row_values(1)
        data = [[row.get(h, "") for h in headers] for row in rows]
        ws.append_rows(data, value_input_option="USER_ENTERED")

    # ── Legacy helpers (kept for backward compat) ────────────────────────────

    def get_layer_registry(self) -> dict:
        import json, os
        try:
            rows = self.get_all_records("Layer_Registry")
            return {r["layer_key"]: r for r in rows if r.get("layer_key")}
        except ValueError:
            _data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
            with open(os.path.join(_data_dir, "layer_registry.json")) as f:
                return json.load(f)

    def update_rm_price(self, material_code: str, new_price: float):
        ws = self._ws(SHEET_MATERIALS)
        records = ws.get_all_records()
        for i, row in enumerate(records, start=2):
            if row["material_code"] == material_code:
                headers = ws.row_values(1)
                ws.update_cell(i, headers.index("rm_price_per_kg") + 1, new_price)
                ws.update_cell(i, headers.index("last_updated") + 1,
                               datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
                return
        raise ValueError(f"Material '{material_code}' not found in Materials sheet")
