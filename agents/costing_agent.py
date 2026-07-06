"""
Costing Agent — reads BOM results and calculates material cost, drum cost,
conversion cost, margin, and selling price.
"""

import sys
import os
import json
from typing import Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.report_generator import generate_costing_pdf


def _lookup_margin(margins: list[dict], product_family: str, area_mm2: float) -> float:
    """Find margin% for a product family and conductor area."""
    for row in margins:
        if row.get("product_family", "").upper() == product_family.upper():
            min_a = float(row.get("min_area_mm2", 0))
            max_a = float(row.get("max_area_mm2", 9999))
            if min_a <= area_mm2 <= max_a:
                return float(row["margin_pct"])
    return 15.0  # default fallback margin


def _lookup_drum_cost(drum_costs: list[dict], product_family: str, area_mm2: float,
                      drum_type: str, delivery_length_m: int) -> float:
    """Return drum cost per km."""
    dt = (drum_type or "wooden").lower()
    for row in drum_costs:
        if row.get("product_type", "").upper() == product_family.upper():
            if row.get("drum_type", "").lower() == dt:
                min_a = float(row.get("size_range_from_mm2", 0))
                max_a = float(row.get("size_range_to_mm2", 9999))
                if min_a <= area_mm2 <= max_a:
                    drum_length = float(row.get("drum_length_m", delivery_length_m))
                    cost_per_drum = float(row.get("cost_per_drum", 0))
                    return cost_per_drum / (drum_length / 1000) if drum_length else 0
    return 0.0


def run_costing_agent(
    bom_json_path: str,
    use_sheets: bool = True,
    output_dir: Optional[str] = None,
) -> dict:
    output_dir = output_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
    os.makedirs(output_dir, exist_ok=True)

    with open(bom_json_path) as f:
        bom_data = json.load(f)

    gtp_ref = (bom_data.get("gtp_ref") or "UNKNOWN").replace("/", "-").replace("\\", "-")
    gtp_type = bom_data.get("gtp_type", "A")
    print(f"[Costing Agent] Processing: {gtp_ref} Type {gtp_type}")

    # Load pricing data
    rm_prices = {}
    margins = []
    drum_costs = []

    if use_sheets:
        try:
            from integrations.sheets_client import SheetsClient
            sc = SheetsClient()
            rm_prices = sc.get_rm_prices()
            margins = sc.get_margins()
            drum_costs = sc.get_drum_costs()
            print("[Costing Agent] Loaded prices/margins from Google Sheets")
        except Exception as e:
            print(f"[Costing Agent] Sheets unavailable ({e}), using zeros for prices")
            use_sheets = False

    costing_results = []
    all_costing_rows_for_sheet = []

    for cable in bom_data.get("cables", []):
        designation = cable.get("designation", "")
        config = cable.get("config", "")
        area_mm2 = _extract_area(config)
        product_family = _infer_product_type(designation)
        delivery_length_m = cable.get("delivery_length_m", 1000)
        drum_type = cable.get("drum_type", "wooden")

        print(f"  → Costing: {config} {designation}")

        # Material cost from costing BOM
        material_cost_per_km = 0.0
        material_breakdown = []
        for row in cable.get("bom_costing", []):
            mat = row.get("material", "")
            weight = float(row.get("weight_kg_per_km", 0))
            price = rm_prices.get(mat, 0.0)
            line_cost = weight * price
            material_cost_per_km += line_cost
            material_breakdown.append({
                "layer": row.get("layer"),
                "material": mat,
                "weight_kg_per_km": weight,
                "price_per_kg": price,
                "cost_per_km": round(line_cost, 2),
            })

        drum_cost_per_km = _lookup_drum_cost(drum_costs, product_family, area_mm2, drum_type, delivery_length_m)
        conversion_cost_per_km = 0.0  # to be populated when operation costs are defined

        total_cost_per_km = material_cost_per_km + drum_cost_per_km + conversion_cost_per_km

        margin_pct = _lookup_margin(margins, product_family, area_mm2)
        floor_price_per_km = total_cost_per_km
        selling_price_per_km = floor_price_per_km / (1 - margin_pct / 100) if margin_pct < 100 else floor_price_per_km

        delivery_km = delivery_length_m / 1000.0
        result = {
            "item_no": cable.get("item_no"),
            "designation": designation,
            "config": config,
            "voltage_kv": cable.get("voltage_kv"),
            "delivery_length_m": delivery_length_m,
            "drum_type": drum_type,
            "material_cost_per_km": round(material_cost_per_km, 2),
            "drum_cost_per_km": round(drum_cost_per_km, 2),
            "conversion_cost_per_km": round(conversion_cost_per_km, 2),
            "total_cost_per_km": round(total_cost_per_km, 2),
            "margin_pct": margin_pct,
            "floor_price_per_km": round(floor_price_per_km, 2),
            "selling_price_per_km": round(selling_price_per_km, 2),
            "selling_price_per_drum_length": round(selling_price_per_km * delivery_km, 2),
            "material_breakdown": material_breakdown,
        }
        costing_results.append(result)

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        all_costing_rows_for_sheet.append({
            "gtp_ref": gtp_ref,
            "gtp_type": gtp_type,
            "cable_designation": designation,
            "config": config,
            "material_cost_per_km": result["material_cost_per_km"],
            "drum_cost_per_km": result["drum_cost_per_km"],
            "conversion_cost_per_km": result["conversion_cost_per_km"],
            "total_cost_per_km": result["total_cost_per_km"],
            "margin_pct": margin_pct,
            "floor_price_per_km": result["floor_price_per_km"],
            "selling_price_per_km": result["selling_price_per_km"],
            "selling_price_per_drum_length": result["selling_price_per_drum_length"],
            "delivery_length_m": delivery_length_m,
            "drum_type": drum_type,
            "date": now,
        })

    # Write to sheets
    if use_sheets:
        try:
            sc.append_costing_results(all_costing_rows_for_sheet)
        except Exception as e:
            print(f"[Costing Agent] Failed to write costing to sheets: {e}")

    # Save JSON
    output_json = os.path.join(output_dir, f"costing_{gtp_ref}_{gtp_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.json")
    with open(output_json, "w") as f:
        json.dump({"gtp_ref": gtp_ref, "gtp_type": gtp_type, "results": costing_results}, f, indent=2)

    # Generate pricing PDF
    pdf_out = os.path.join(output_dir, f"Pricing_{gtp_ref}_{gtp_type}.pdf")
    generate_costing_pdf(gtp_ref, gtp_type, costing_results, pdf_out)
    print(f"[Costing Agent] Pricing PDF: {pdf_out}")

    # Build Telegram summary
    tg_summary = _build_telegram_summary(gtp_ref, gtp_type, costing_results)

    return {
        "gtp_ref": gtp_ref,
        "gtp_type": gtp_type,
        "results": costing_results,
        "json_path": output_json,
        "pdf_path": pdf_out,
        "telegram_summary": tg_summary,
    }


def _extract_area(config: str) -> float:
    """Extract conductor area from config string like '3.5C x 70mm²'."""
    import re
    m = re.search(r'(\d+(?:\.\d+)?)\s*mm', config)
    return float(m.group(1)) if m else 0.0


def _infer_product_type(designation: str) -> str:
    d = designation.upper()
    if "XIFY" in d:
        return "HT_11KV"
    elif "XFY" in d:
        return "LT_XLPE_ARMOURED"
    elif "2X" in d or "A2X" in d:
        return "LT_XLPE_UNARM"
    else:
        return "LT_PVC"


def _build_telegram_summary(gtp_ref: str, gtp_type: str, results: list[dict]) -> str:
    lines = [f"*Pricing — GTP: {gtp_ref} (Type {gtp_type})*\n"]
    for r in results:
        lines.append(
            f"• *{r['config']} {r['designation']}*\n"
            f"  Floor: ₹{r['floor_price_per_km']:,.0f}/km | "
            f"Selling: ₹{r['selling_price_per_km']:,.0f}/km | "
            f"Per {r['delivery_length_m']}m drum: ₹{r['selling_price_per_drum_length']:,.0f}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python costing_agent.py <path_to_bom.json>")
        sys.exit(1)
    result = run_costing_agent(sys.argv[1], use_sheets=False)
    print(result["telegram_summary"])
