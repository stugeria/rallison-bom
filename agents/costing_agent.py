"""
Costing Agent — pure reporting layer over agents/bom_agent.py's own numbers.

bom_agent.py is the single source of truth for material cost, drum cost,
margin %, and price (see _material_breakdown / _material_cost / _price_bom
there). This module never recomputes any of that — it only reads the figures
bom_agent already stored on each item and formats them into a Pricing PDF and
a Telegram-ready summary. This guarantees the two can never disagree.
"""

import sys
import os
import json
from typing import Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.report_generator import generate_costing_pdf


def run_costing_agent(
    bom_json_path: str,
    requested_bom_type: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> dict:
    """
    Build a pricing report from a BOM Agent output JSON (agents/bom_agent.py's
    `items` schema). `requested_bom_type` selects which of A/B/C to report;
    defaults to whatever bom_agent.py was asked for when it ran.
    """
    output_dir = output_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
    os.makedirs(output_dir, exist_ok=True)

    with open(bom_json_path) as f:
        bom_data = json.load(f)

    gtp_ref = (bom_data.get("gtp_no") or "UNKNOWN").replace("/", "-").replace("\\", "-")
    bom_type = (requested_bom_type or bom_data.get("requested_bom_type") or "A").upper()
    print(f"[Costing Agent] Reporting: {gtp_ref} Type {bom_type}")

    costing_results = []
    for item in bom_data.get("items", []):
        price_key = f"price_{bom_type.lower()}"
        # Items skipped as already-registered by bom_agent.py don't carry a
        # per-type cost breakdown — nothing new to report on them here.
        if "material_cost_per_km" not in item or price_key not in item:
            continue

        material_cost = float(item["material_cost_per_km"].get(bom_type, 0.0))
        drum_cost = float(item.get("drum_cost_per_km", 0.0))
        conversion_cost = float(item.get("drawing_cost_per_km", 0.0))
        total_cost = round(material_cost + drum_cost + conversion_cost, 2)
        margin_pct = item.get("margin_pct", 0.0)
        selling_price = float(item.get(price_key, total_cost))
        delivery_m = item.get("delivery_length_m", 1000)
        delivery_km = delivery_m / 1000.0

        costing_results.append({
            "item_no": item.get("item_no"),
            "designation": item.get("item_name", ""),
            "config": item.get("config", ""),
            "voltage_kv": item.get("voltage_kv"),
            "delivery_length_m": delivery_m,
            "drum_type": item.get("drum_type", "wooden"),
            "material_cost_per_km": round(material_cost, 2),
            "drum_cost_per_km": round(drum_cost, 2),
            "conversion_cost_per_km": round(conversion_cost, 2),
            "total_cost_per_km": total_cost,
            "margin_pct": margin_pct,
            "floor_price_per_km": total_cost,
            "selling_price_per_km": round(selling_price, 2),
            "selling_price_per_drum_length": round(selling_price * delivery_km, 2),
            "material_breakdown": item.get("material_breakdown", {}).get(bom_type, []),
        })

    # Save JSON
    output_json = os.path.join(output_dir, f"costing_{gtp_ref}_{bom_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.json")
    with open(output_json, "w") as f:
        json.dump({"gtp_ref": gtp_ref, "gtp_type": bom_type, "results": costing_results}, f, indent=2)

    # Generate pricing PDF
    pdf_out = os.path.join(output_dir, f"Pricing_{gtp_ref}_{bom_type}.pdf")
    generate_costing_pdf(gtp_ref, bom_type, costing_results, pdf_out)
    print(f"[Costing Agent] Pricing PDF: {pdf_out}")

    # Build Telegram summary
    tg_summary = _build_telegram_summary(gtp_ref, bom_type, costing_results)

    return {
        "gtp_ref": gtp_ref,
        "gtp_type": bom_type,
        "results": costing_results,
        "json_path": output_json,
        "pdf_path": pdf_out,
        "telegram_summary": tg_summary,
    }


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
        print("Usage: python costing_agent.py <path_to_bom_agent_output.json> [A|B|C]")
        sys.exit(1)
    result = run_costing_agent(sys.argv[1], requested_bom_type=sys.argv[2] if len(sys.argv) > 2 else None)
    print(result["telegram_summary"])
