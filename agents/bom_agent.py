"""
BOM Agent — parse GTP, compute all three BOM types (A/B/C), price them, and
write to Google Sheets. Only shares the requested type's price with the caller.

Sheet writes (all in one go per GTP):
  GTP_Registry  — one row per cable item (metadata + Price A/B/C)
  BOM_Production — production weights, one row per layer × 3 types
  BOM_Costing    — costing weights, one row per layer × 3 types

Dedup key: (GTP No., Item No.) — skips already-stored items.
"""

import sys
import os
import json
import re
from typing import Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gtp_parser import parse_gtp
from core.bom_calculator import build_bom_for_cable
from core.local_registry import get_margin, upsert_row, DEFAULT_MARGIN
from core.local_bom_store import write_bom_rows
from core.drum_costs import lookup_drum_cost_per_km, is_steel_drum
from core.rm_prices_reader import load_rm_prices

BOM_TYPES = ["A", "B", "C"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _infer_product_type(designation: str, cable: Optional[dict] = None) -> str:
    """Infer cable family from designation string and optional cable dict."""
    d = designation.upper()
    layers = [l.get("material_key", "") for l in (cable or {}).get("layers", [])]

    # HT / MV voltage-class cables
    if any(kw in d for kw in ("11KV", "22KV", "33KV", "XIFY", "XHFY")):
        return "HT_11KV_SCREENED"
    if any(kw in d for kw in ("MV", "33/33")):
        return "MV_XLPE_SCREENED"

    # Fire Survival (IS 17505 / BS 7846) — detect from layers (glass mica tape = FS)
    if "glass_mica_tape" in layers:
        return "FIRE_SURVIVAL"
    if any(kw in d for kw in ("FIRE", "FS CABLE", " FS ", "17505")):
        return "FIRE_SURVIVAL"

    # BMS — overall shielded, flexible, unarmoured
    if "BMS" in d or "OVERALL SHIELD" in d:
        return "BMS_SCREENED"

    # Instrumentation
    if any(kw in d for kw in ("INSTR", "PAIR", "TRIAD", "IBS", "MULTIPAIR")):
        return "INSTR_SCREENED"

    # Flexible IS 694 (Class 5/6)
    if any(kw in d for kw in ("FLEX", "CLASS 5", "CLASS5", "CLASS-5", "CLASS 6", "694")):
        return "LT_PVC_FLEX"
    if cable and cable.get("conductor_class") in (5, 6):
        return "LT_PVC_FLEX"

    # LT XLPE armoured
    if any(kw in d for kw in ("XFY", "2XFY", "A2XFY", "ARMOUR", "SWA")):
        return "LT_XLPE_ARMOURED"

    # LT XLPE unarmoured
    if any(kw in d for kw in ("2X", "A2X", "XLPE")):
        return "LT_XLPE_UNARM"

    # Control cable armoured
    if "CTRL" in d and any(kw in d for kw in ("ARM", "WY", "WFY")):
        return "CTRL_PVC_ARMOURED"

    # Control cable unarmoured
    if "CTRL" in d or "CONTROL" in d:
        return "CTRL_PVC_UNARM"

    return "LT"


_margins_cache: dict = {}

def _load_margins() -> list:
    global _margins_cache
    if not _margins_cache:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "margins.json")
        with open(path) as f:
            _margins_cache = json.load(f)
    return _margins_cache.get("rules", [])


def _lookup_margin_local(product_family: str, conductor_material: str,
                         area_mm2: float, num_cores: float) -> Optional[float]:
    """Look up margin from data/margins.json. Returns None if no rule matches."""
    fam = (product_family or "").upper()
    mat = (conductor_material or "copper").lower()
    for rule in _load_margins():
        families = [f.upper() for f in rule.get("families", [])]
        if fam not in families:
            continue
        # conductor filter
        if "conductor" in rule and rule["conductor"].lower() != mat:
            continue
        # core count filters
        if "max_cores" in rule and num_cores > rule["max_cores"]:
            continue
        if "min_cores" in rule and num_cores < rule["min_cores"]:
            continue
        # matched — find band
        for band in rule.get("bands", []):
            if area_mm2 <= band.get("up_to_mm2", float("inf")):
                return float(band["margin_pct"])
    return None


def _lookup_margin(margins: list[dict], product_family: str, area_mm2: float) -> float:
    """Sheets-sourced margin lookup (legacy path, used when Google Sheets is active)."""
    for row in margins:
        if row.get("product_family", "").upper() == product_family.upper():
            if float(row.get("min_area_mm2", 0)) <= area_mm2 <= float(row.get("max_area_mm2", 9999)):
                return float(row["margin_pct"])
    return 0.0


def _lookup_drum_cost(drum_costs: list[dict], product_family: str,
                      area_mm2: float, drum_type: str, delivery_length_m: float) -> float:
    dt = (drum_type or "wooden").lower()
    for row in drum_costs:
        if row.get("product_type", "").upper() == product_family.upper():
            if row.get("drum_type", "").lower() == dt:
                if float(row.get("size_range_from_mm2", 0)) <= area_mm2 <= float(row.get("size_range_to_mm2", 9999)):
                    length = float(row.get("drum_length_m", delivery_length_m))
                    cost = float(row.get("cost_per_drum", 0))
                    return cost / (length / 1000) if length else 0.0
    return 0.0


def _material_breakdown(bom_rows: list[dict], rm_prices: dict) -> list[dict]:
    """Per-layer weight × RM price. The single source of truth for material cost —
    both _price_bom and the standalone costing report are built from this."""
    rows = []
    for r in bom_rows:
        mat = r.get("material", "")
        weight = float(r.get("weight_kg_per_km", 0))
        price = rm_prices.get(mat, 0.0)
        rows.append({
            "layer": r.get("layer"),
            "material": mat,
            "weight_kg_per_km": weight,
            "price_per_kg": price,
            "cost_per_km": round(weight * price, 2),
        })
    return rows


def _material_cost(bom_rows: list[dict], rm_prices: dict) -> float:
    return round(sum(row["cost_per_km"] for row in _material_breakdown(bom_rows, rm_prices)), 2)


def _price_bom(bom_rows: list[dict], rm_prices: dict,
               drum_cost_per_km: float, conversion_cost_per_km: float,
               margin_pct: float, min_margin_override: Optional[float] = None) -> float:
    material_cost = _material_cost(bom_rows, rm_prices)
    # Margin base = material cost + packing (drum) cost; conversion cost added on top
    margin_base = material_cost + drum_cost_per_km
    effective_margin = max(margin_pct, min_margin_override or 0.0)
    price = round(margin_base / (1 - effective_margin / 100), 2) if effective_margin < 100 else margin_base
    return round(price + conversion_cost_per_km, 2)


_drawing_costs_cache: dict = {}

def _calc_drawing_cost(bom_rows: list[dict], product_family: str) -> float:
    """Drawing cost for flexible cables: ₹/kg × conductor weight, based on wire diameter."""
    global _drawing_costs_cache
    if not _drawing_costs_cache:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "drawing_costs.json")
        with open(path) as f:
            _drawing_costs_cache = json.load(f)

    families = [f.upper() for f in _drawing_costs_cache.get("applies_to_families", [])]
    if product_family.upper() not in families:
        return 0.0

    bands = _drawing_costs_cache.get("bands", [])
    total = 0.0
    for row in bom_rows:
        if "conductor" not in row.get("material", ""):
            continue
        wire_dia = row.get("wire_dia_mm") or 0
        weight   = float(row.get("weight_kg_per_km", 0))
        rate = 0.0
        for band in bands:
            if wire_dia <= band.get("up_to_mm", float("inf")):
                rate = float(band["cost_per_kg"])
                break
        total += weight * rate
    return round(total, 2)


def _bom_sheet_rows(bom_rows: list[dict], bom_no: str, gtp_no: str, bom_type: str,
                    item_no: str, item_name: str, item_code: str,
                    rm_map: dict) -> list[dict]:
    rows = []
    for r in bom_rows:
        mat = r.get("material", "")
        rm = rm_map.get(mat, {})
        rows.append({
            "BOM No.":         bom_no,
            "GTP No.":         gtp_no,
            "BOM Type":        bom_type,
            "Item No.":        item_no,
            "Item Name":       item_name,
            "Item Code":       item_code,
            "RM Code":         rm.get("rm_code", ""),
            "RM Description":  rm.get("rm_description", mat),
            "Weight (kg/km)":  r.get("weight_kg_per_km", ""),
        })
    return rows


# ── Main entry point ──────────────────────────────────────────────────────────

def run_bom_agent(
    pdf_path: str,
    requested_bom_type: str = "A",
    use_sheets: bool = True,
    output_dir: Optional[str] = None,
) -> dict:
    """
    Parse GTP, compute all 3 BOM types (A/B/C) for every cable, store in Sheets,
    and return pricing only for `requested_bom_type`.

    Returns:
        {
          gtp_no, cables_processed, skipped_existing,
          summary_table: str   (Telegram-ready markdown),
          pricing: {item_no: price_per_km},
          json_path
        }
    """
    output_dir = output_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"
    )
    os.makedirs(output_dir, exist_ok=True)

    print(f"[BOM Agent] Parsing GTP: {pdf_path}")
    gtp_data = parse_gtp(pdf_path)
    gtp_no = (gtp_data.get("gtp_ref") or "UNKNOWN").replace("/", "-").replace("\\", "-")
    cables = gtp_data.get("cables", [])
    print(f"[BOM Agent] GTP: {gtp_no} | Cables: {len(cables)}")

    # ── Load config from Sheets ───────────────────────────────────────────────
    sc = None
    rm_prices, margins, drum_costs, rm_map = {}, [], [], {}
    cfg: dict[str, dict] = {"A": {}, "B": {}, "C": {}}  # per-type overrides

    # Load RM prices: Excel RM_Prices sheet overrides JSON (procurement updates Excel directly)
    rm_prices = load_rm_prices()
    print(f"[BOM Agent] Loaded {len(rm_prices)} RM prices (Excel+JSON merged)")

    if use_sheets:
        try:
            from integrations.sheets_client import SheetsClient, SHEET_BOM_PRODUCTION, SHEET_BOM_COSTING
            sc = SheetsClient()
            rm_prices    = sc.get_rm_prices()
            margins      = sc.get_margins()
            drum_costs   = sc.get_drum_costs()
            rm_map       = sc.get_rm_code_map()
            for t in BOM_TYPES:
                cfg[t] = {
                    "density_costing":    sc.get_density_overrides("costing"),
                    "density_production": sc.get_density_overrides("production"),
                    "lay_costing":        sc.get_lay_factor_overrides("costing"),
                    "lay_production":     sc.get_lay_factor_overrides("production"),
                    "tol_costing":        sc.get_tolerance_table("costing"),
                    "tol_production":     sc.get_tolerance_table("production"),
                }
            print("[BOM Agent] Config loaded from Sheets")
        except Exception as e:
            print(f"[BOM Agent] Sheets unavailable ({e}) — using local defaults")
            use_sheets = False

    # ── Process each cable ────────────────────────────────────────────────────
    results = []
    skipped = []
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    for idx, cable in enumerate(cables, start=1):
        item_no   = str(cable.get("item_no") or idx)
        item_name = cable.get("designation", "")
        item_code = cable.get("item_code", "")  # usually blank from GTP — user fills in sheet
        config    = cable.get("config", "")
        area_mm2  = float(cable.get("conductor_area_mm2") or _extract_area(config) or 0)
        num_cores = float(cable.get("num_cores") or 1)
        conductor_material = cable.get("conductor_material", "copper")
        product_family = _infer_product_type(item_name, cable)
        delivery_m     = float(cable.get("delivery_length_m") or 1000)
        drum_type      = cable.get("drum_type", "wooden")

        # Dedup check
        if use_sheets and sc:
            exists, reg_row_num, reg_row = sc.check_gtp_exists(gtp_no, item_no)
            if exists:
                print(f"  [skip] {gtp_no} / Item {item_no} already in GTP_Registry")
                skipped.append({
                    "item_no": item_no,
                    "item_name": item_name,
                    "config": config,
                    "price_a": reg_row.get("Price — Type A (₹/km)", 0),
                    "price_b": reg_row.get("Price — Type B (₹/km)", 0),
                    "price_c": reg_row.get("Price — Type C (₹/km)", 0),
                    "min_margin": reg_row.get("Min Margin %", ""),
                })
                continue

        print(f"  → [{item_no}] {config} {item_name}")

        # Compute BOM for all 3 types × 2 modes (costing / production)
        boms: dict[str, dict] = {}
        for t in BOM_TYPES:
            tf = {}
            if use_sheets and sc:
                try:
                    tf = sc.get_gtp_type_factors(product_family, t)
                except Exception:
                    pass

            boms[t] = {
                "costing": build_bom_for_cable(
                    cable, "costing", gtp_type=t, gtp_type_factors=tf,
                    density_overrides=cfg[t].get("density_costing"),
                    lay_factor_overrides=cfg[t].get("lay_costing"),
                    tolerance_table=cfg[t].get("tol_costing"),
                ),
                "production": build_bom_for_cable(
                    cable, "production", gtp_type=t, gtp_type_factors=tf,
                    density_overrides=cfg[t].get("density_production"),
                    lay_factor_overrides=cfg[t].get("lay_production"),
                    tolerance_table=cfg[t].get("tol_production"),
                ),
            }

        # Pricing for each type (uses costing BOM weights)
        # Margin priority: 1) user override in local registry  2) margins.json rules  3) Sheets  4) DEFAULT
        margin_pct = get_margin(gtp_no, item_no)   # returns DEFAULT_MARGIN if no override
        if margin_pct == DEFAULT_MARGIN:
            # Not yet user-set — derive from product rules
            local_margin = _lookup_margin_local(product_family, conductor_material, area_mm2, num_cores)
            if local_margin is not None:
                margin_pct = local_margin
            elif use_sheets:
                sheets_margin = _lookup_margin(margins, product_family, area_mm2)
                if sheets_margin:
                    margin_pct = sheets_margin
        print(f"     margin={margin_pct}% | family={product_family} | {conductor_material} {area_mm2}mm² {num_cores}C")

        # Steel drum alert — flag for Telegram bot to surface to user
        steel_drum_alert = is_steel_drum(drum_type)
        if steel_drum_alert:
            print(f"     ⚠ STEEL DRUM specified — manual loading cost adjustment may be needed")

        # Material cost + breakdown for all 3 types (type A used as the drum-sizing reference)
        material_breakdown = {t: _material_breakdown(boms[t]["costing"], rm_prices) for t in BOM_TYPES}
        material_costs = {t: _material_cost(boms[t]["costing"], rm_prices) for t in BOM_TYPES}

        # Drum cost: material-% fallback or Excel exact entry
        drum_cost_km, drum_source = lookup_drum_cost_per_km(
            material_cost_per_km=material_costs["A"],
            product_family=product_family,
            conductor_material=conductor_material,
            area_mm2=area_mm2,
            num_cores=num_cores,
            drum_type=drum_type,
            gtp_no=gtp_no,
            item_no=item_no,
        )
        print(f"     drum={drum_source} → ₹{drum_cost_km:.0f}/km")

        # Wire drawing cost for flexible cables (added to conversion cost, outside margin base)
        drawing_cost_km = _calc_drawing_cost(boms["A"]["costing"], product_family)
        if drawing_cost_km:
            print(f"     drawing cost → ₹{drawing_cost_km:.0f}/km")

        prices = {
            t: _price_bom(boms[t]["costing"], rm_prices, drum_cost_km, drawing_cost_km, margin_pct)
            for t in BOM_TYPES
        }

        # Write to local registry (creates file if new, preserves margin if existing)
        upsert_row(gtp_no, item_no, cable, prices, margin_pct)

        # Write per-layer BOM weights to bom_detail.xlsx
        write_bom_rows(gtp_no, item_no, item_name, item_code, boms)

        cable_result = {
            "item_no": item_no, "item_name": item_name, "item_code": item_code,
            "config": config, "voltage_kv": cable.get("voltage_kv"),
            "delivery_length_m": delivery_m, "drum_type": drum_type,
            "price_a": prices["A"], "price_b": prices["B"], "price_c": prices["C"],
            "drum_cost_per_km": drum_cost_km, "drum_source": drum_source,
            "steel_drum_alert": steel_drum_alert,
            "margin_pct": margin_pct,
            "material_cost_per_km": material_costs,        # {"A": .., "B": .., "C": ..}
            "material_breakdown": material_breakdown,      # {"A": [...], "B": [...], "C": [...]}
            "drawing_cost_per_km": drawing_cost_km,
            "boms": boms,
        }
        results.append(cable_result)

        # ── Write to Sheets ───────────────────────────────────────────────────
        if use_sheets and sc:
            try:
                registry_record = {
                    "Min Margin %":            "",
                    "GTP No.":                 gtp_no,
                    "Item No.":                item_no,
                    "Item Name":               item_name,
                    "Item Code":               item_code,
                    "Cable Family":            product_family,
                    "Voltage Grade":           cable.get("voltage_kv", ""),
                    "No. of Cores":            cable.get("num_cores", ""),
                    "Conductor Area (mm²)":    area_mm2,
                    "Conductor Material":      cable.get("conductor_material", ""),
                    "Conductor Shape":         cable.get("conductor_shape", ""),
                    "Insulation":              cable.get("insulation_material", ""),
                    "Armour":                  cable.get("armour_type", ""),
                    "Sheath":                  cable.get("sheath_material", ""),
                    "Overall OD (mm)":         "",  # filled from BOM if available
                    "Price — Type A (₹/km)":   prices["A"],
                    "Price — Type B (₹/km)":   prices["B"],
                    "Price — Type C (₹/km)":   prices["C"],
                    "Created At":              now,
                    "Last Updated":            now,
                }
                sc.append_gtp_registry(registry_record)

                prod_rows, cost_rows = [], []
                for t in BOM_TYPES:
                    bom_no = f"{gtp_no}-{item_no}-{t}"
                    prod_rows.extend(_bom_sheet_rows(
                        boms[t]["production"], bom_no, gtp_no, t,
                        item_no, item_name, item_code, rm_map
                    ))
                    cost_rows.extend(_bom_sheet_rows(
                        boms[t]["costing"], bom_no, gtp_no, t,
                        item_no, item_name, item_code, rm_map
                    ))

                sc.append_bom_rows(prod_rows, SHEET_BOM_PRODUCTION)
                sc.append_bom_rows(cost_rows, SHEET_BOM_COSTING)
                print(f"    Written to Sheets: {len(prod_rows)} prod rows, {len(cost_rows)} costing rows")
            except Exception as e:
                print(f"    [Sheets write error] {e}")

    # ── Build output ──────────────────────────────────────────────────────────
    all_items = results + skipped
    price_key = f"price_{requested_bom_type.lower()}"

    summary = _build_summary(gtp_no, requested_bom_type, all_items, price_key)

    output = {
        "gtp_no": gtp_no,
        "requested_bom_type": requested_bom_type,
        "cables_processed": len(results),
        "skipped_existing": len(skipped),
        "summary_table": summary,
        "pricing": {item["item_no"]: item.get(price_key, 0) for item in all_items},
        "_all_items": [{k: v for k, v in item.items() if k != "boms"} for item in all_items],
    }

    json_path = os.path.join(
        output_dir,
        f"bom_{gtp_no}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.json"
    )
    with open(json_path, "w") as f:
        json.dump({
            "gtp_no": gtp_no,
            "requested_bom_type": requested_bom_type,
            "items": [
                {k: v for k, v in item.items() if k != "boms"}  # exclude large BOM dicts
                for item in results
            ] + skipped,
        }, f, indent=2)

    output["json_path"] = json_path
    print(f"[BOM Agent] Done. {len(results)} new, {len(skipped)} skipped. JSON: {json_path}")
    return output


# ── Telegram summary builder ──────────────────────────────────────────────────

def _build_summary(gtp_no: str, bom_type: str, items: list[dict], price_key: str) -> str:
    lines = [f"*GTP: {gtp_no} | BOM Type {bom_type}*\n"]

    # Steel drum alerts
    steel_items = [r for r in items if r.get("steel_drum_alert")]
    if steel_items:
        descs = ", ".join(r.get("config", r.get("item_name", "")) for r in steel_items)
        lines.append(f"⚠️ *Steel drum specified for: {descs}*")
        lines.append("_Please consider additional loading / handling cost before finalising price._\n")

    if len(items) == 1:
        r = items[0]
        price = r.get(price_key, 0)
        drum_note = f" _(drum: ₹{r.get('drum_cost_per_km', 0):,.0f}/km)_" if r.get("drum_cost_per_km") else ""
        lines.append(
            f"*{r.get('config', '')} {r.get('item_name', '')}*\n"
            f"Price: ₹{price:,.0f}/km{drum_note}"
        )
        return "\n".join(lines)

    # Multiple items — tabular summary
    lines.append("```")
    lines.append(f"{'No.':<4} {'Description':<30} {'Price (₹/km)':>14}")
    lines.append("─" * 50)
    for r in items:
        desc = f"{r.get('config', '')} {r.get('item_name', '')}".strip()[:29]
        price = r.get(price_key, 0)
        lines.append(f"{r.get('item_no', ''):<4} {desc:<30} {price:>14,.0f}")
    lines.append("```")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python bom_agent.py <gtp.pdf> [A|B|C]")
        sys.exit(1)
    result = run_bom_agent(
        sys.argv[1],
        requested_bom_type=sys.argv[2] if len(sys.argv) > 2 else "A",
        use_sheets=False,
    )
    print(result["summary_table"])
