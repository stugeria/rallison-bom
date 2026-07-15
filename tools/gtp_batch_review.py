"""
GTP Batch Review — drop a folder of GTP PDFs (20-50 real documents) and get
one Markdown report per file showing every value read from the GTP and how
the BOM + price was calculated, plus an index summarizing the whole batch.

This is a read-only review tool: it runs the exact same parse → BOM →
costing pipeline as the production Telegram bot (agents/bom_agent.py), but
with write_local=False so it never touches output/gtp_registry.xlsx or
output/bom_detail.xlsx — safe to re-run on the same folder as many times as
you like without polluting production state or getting silently deduped
against real GTPs already in the registry.

Note on "values read from the GTP": each report shows the parser's *output*
values (what ended up in the cable dict) and the raw layer specs — it does
not cite the exact source line/byte-offset each value came from, since doing
that generically across all 5 parser formats would need per-parser
instrumentation. For close verification, open the source PDF alongside the
report and search for the field label.

Usage:
    python tools/gtp_batch_review.py <input_folder>
    python tools/gtp_batch_review.py <input_folder> --output-dir <dir> --gtp-type A
"""

import argparse
import os
import sys
import traceback
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core.gtp_parser_direct import parse_gtp_direct, _detect_format, extract_pdf_text
from core.gtp_validator import validate_cable
from agents.bom_agent import run_bom_agent


_INPUT_FIELDS = [
    ("Conductor material", "conductor_material"),
    ("Number of cores", "num_cores"),
    ("Conductor shape", "conductor_shape"),
    ("Nominal cross-section (mm²)", "conductor_area_mm2"),
    ("DC resistance (Ω/km)", "dc_resistance_ohm_per_km"),
    ("Conductor class", "conductor_class"),
    ("Voltage (kV)", "voltage_kv"),
    ("Standard", "standard"),
    ("Overall OD (mm)", "overall_od_mm"),
    ("Delivery length (m)", "delivery_length_m"),
    ("Drum type", "drum_type"),
]

_LAYER_FIELD_LABELS = {
    "nominal_thickness_mm": "Thickness (mm)",
    "thickness_type": "Thickness type",
    "wire_diameter_mm": "Wire diameter (mm)",
    "armour_strip_width_mm": "Strip width (mm)",
    "armour_strip_thickness_mm": "Strip thickness (mm)",
    "tape_thickness_mm": "Tape thickness (mm)",
    "tape_overlap_pct": "Tape overlap (%)",
}


# ── Markdown helpers ────────────────────────────────────────────────────────

def _md_table(headers: list, rows: list) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        cells = ["—" if c in (None, "") else str(c) for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _cable_input_section(cable: dict) -> str:
    rows = [(label, cable.get(key)) for label, key in _INPUT_FIELDS]
    out = ["### Parsed input values", "", _md_table(["Field", "Value"], rows), ""]

    layer_rows = []
    for layer in cable.get("layers", []):
        details = [
            f"{label}: {layer[key]}"
            for key, label in _LAYER_FIELD_LABELS.items()
            if layer.get(key) not in (None, "")
        ]
        layer_rows.append([layer.get("layer_name", ""), layer.get("material_key", ""), "; ".join(details)])
    out.append("### Layers read from GTP")
    out.append("")
    out.append(_md_table(["Layer", "Material key", "Details"], layer_rows))
    out.append("")

    confidence, issues = validate_cable(cable)
    out.append(f"**Validator confidence:** {confidence:.2f} ({round(confidence * 6)}/6 checks passed)")
    if issues:
        out.append("")
        out.append("**Flagged:**")
        for issue in issues:
            out.append(f"- {issue}")
    return "\n".join(out)


def _costing_section(item: dict, bom_type: str) -> str:
    breakdown = item.get("material_breakdown", {}).get(bom_type, [])
    if not breakdown:
        return "_No costing breakdown available for this item/type — likely a dedup-skipped item._"

    rows = [
        [r.get("layer", ""), r.get("material", ""), f"{r.get('weight_kg_per_km', 0):.3f}",
         f"{r.get('price_per_kg', 0):,.2f}", f"{r.get('cost_per_km', 0):,.2f}"]
        for r in breakdown
    ]
    out = [
        f"### BOM + Costing (Type {bom_type})", "",
        _md_table(["Layer", "Material", "Wt (kg/km)", "Rate (₹/kg)", "Cost (₹/km)"], rows), "",
    ]

    material_cost = item.get("material_cost_per_km", {}).get(bom_type, 0.0)
    drum_cost = item.get("drum_cost_per_km", 0.0)
    drawing_cost = item.get("drawing_cost_per_km", 0.0)
    floor_cost = material_cost + drum_cost + drawing_cost
    margin_pct = item.get("margin_pct", 0.0)
    price = item.get(f"price_{bom_type.lower()}", floor_cost)

    out.append(f"- **Material cost/km:** ₹{material_cost:,.2f}")
    out.append(f"- **Drum cost/km:** ₹{drum_cost:,.2f} (source: {item.get('drum_source', '—')})")
    if drawing_cost:
        out.append(f"- **Drawing cost/km:** ₹{drawing_cost:,.2f}")
    out.append(f"- **Floor cost/km:** ₹{floor_cost:,.2f}")
    out.append(f"- **Margin:** {margin_pct}%")
    out.append(f"- **Selling price/km:** ₹{price:,.2f}")
    if item.get("steel_drum_alert"):
        out.append("- ⚠️ **Steel drum specified** — review loading/handling cost.")
    return "\n".join(out)


# ── Per-file processing ──────────────────────────────────────────────────────

def process_file(pdf_path: str, gtp_type: str, output_dir: str) -> dict:
    """Parses + prices one GTP, writes its Markdown report, returns an index row."""
    filename = os.path.basename(pdf_path)
    record = {"file": filename, "status": "ERROR", "format": "?", "cables": 0,
              "error": None, "report_path": None}

    try:
        raw_text = extract_pdf_text(pdf_path)
        record["format"] = _detect_format(raw_text)
    except Exception:
        pass  # informational only — parse below will surface the real error

    try:
        parsed = parse_gtp_direct(pdf_path, gtp_type)
        raw_cables = {str(c.get("item_no")): c for c in parsed.get("cables", [])}
    except Exception:
        record["error"] = "parse_gtp_direct failed:\n" + traceback.format_exc()
        _write_report(pdf_path, output_dir, record, None, {}, {})
        return record

    if not raw_cables:
        record["status"] = "WARN"
        record["error"] = "Parser ran but found 0 cables — format not recognised, or every cable failed validation."
        _write_report(pdf_path, output_dir, record, parsed, raw_cables, {})
        return record

    bom_items = {}
    try:
        bom_result = run_bom_agent(pdf_path, requested_bom_type=gtp_type, use_sheets=False,
                                    output_dir=output_dir, write_local=False)
        bom_items = {str(i.get("item_no")): i for i in bom_result.get("_all_items", [])}
        record["status"] = "OK"
    except Exception:
        record["error"] = "run_bom_agent (BOM/costing) failed:\n" + traceback.format_exc()
        record["status"] = "WARN"

    record["cables"] = len(raw_cables)
    _write_report(pdf_path, output_dir, record, parsed, raw_cables, bom_items, gtp_type)
    return record


def _write_report(pdf_path, output_dir, record, parsed, raw_cables, bom_items, gtp_type="A"):
    filename = os.path.basename(pdf_path)
    stem = os.path.splitext(filename)[0]
    report_path = os.path.join(output_dir, f"{stem}.md")

    lines = [
        f"# GTP Review: {filename}", "",
        f"**Status:** {record['status']}  |  **Format detected:** {record['format']}  |  "
        f"**Cables found:** {record['cables']}",
    ]
    if parsed:
        lines.append(f"**Parser:** {parsed.get('_parser', '?')}  |  **GTP Ref:** {parsed.get('gtp_ref', '—')}  |  "
                      f"**Customer:** {parsed.get('customer', '—')}")
    lines.append("")

    if record["error"]:
        lines.append("## Error")
        lines.append("```")
        lines.append(record["error"])
        lines.append("```")
        lines.append("")

    if raw_cables:
        for item_no, cable in raw_cables.items():
            lines.append("---")
            lines.append("")
            lines.append(f"## Item {item_no} — {cable.get('config', '')} {cable.get('designation', '')}")
            lines.append("")
            lines.append(_cable_input_section(cable))
            lines.append("")
            item = bom_items.get(item_no)
            if item:
                lines.append(_costing_section(item, gtp_type))
            else:
                lines.append("_No BOM/costing result for this item (see Error section above)._")
            lines.append("")

    os.makedirs(output_dir, exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    record["report_path"] = os.path.relpath(report_path, output_dir)


# ── Batch driver ──────────────────────────────────────────────────────────

def run_batch(input_folder: str, output_dir: str, gtp_type: str):
    pdfs = sorted(f for f in os.listdir(input_folder) if f.lower().endswith(".pdf"))
    if not pdfs:
        print(f"No PDFs found in: {input_folder}")
        sys.exit(1)

    print(f"Found {len(pdfs)} PDF(s) in {input_folder}\n")
    records = []
    for i, filename in enumerate(pdfs, 1):
        path = os.path.join(input_folder, filename)
        print(f"  [{i:2d}/{len(pdfs)}] {filename}", end="  ")
        try:
            record = process_file(path, gtp_type, output_dir)
        except Exception:
            record = {"file": filename, "status": "ERROR", "format": "?", "cables": 0,
                       "error": "Unhandled exception:\n" + traceback.format_exc(), "report_path": None}
            _write_report(path, output_dir, record, None, {}, {})
        print(f"→ {record['status']} | format={record['format']} | cables={record['cables']}")
        records.append(record)

    _write_index(output_dir, input_folder, records)


def _write_index(output_dir: str, input_folder: str, records: list):
    ok = sum(1 for r in records if r["status"] == "OK")
    warn = sum(1 for r in records if r["status"] == "WARN")
    err = sum(1 for r in records if r["status"] == "ERROR")
    total_cables = sum(r["cables"] for r in records)

    lines = [
        "# GTP Batch Review — Index", "",
        f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  |  Folder: `{os.path.abspath(input_folder)}`",
        "",
        f"**{len(records)} files** — {ok} OK, {warn} warnings, {err} errors  |  **{total_cables} cables total**",
        "",
        _md_table(
            ["#", "File", "Status", "Format", "Cables", "Report"],
            [
                [i, r["file"], r["status"], r["format"], r["cables"],
                 f"[{r['report_path']}]({r['report_path']})" if r["report_path"] else "—"]
                for i, r in enumerate(records, 1)
            ],
        ),
        "",
    ]

    if warn or err:
        lines.append("## Needs attention")
        lines.append("")
        for r in records:
            if r["status"] != "OK":
                lines.append(f"- **{r['file']}** ({r['status']}): "
                              f"{(r['error'] or '').splitlines()[0] if r['error'] else 'see report'}")
        lines.append("")

    index_path = os.path.join(output_dir, "_INDEX.md")
    os.makedirs(output_dir, exist_ok=True)
    with open(index_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\n{'='*60}")
    print(f"{len(records)} files → {ok} OK, {warn} warnings, {err} errors, {total_cables} cables total")
    print(f"Index: {index_path}")
    print(f"{'='*60}")


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch GTP review — parse + BOM + costing → Markdown reports")
    parser.add_argument("folder", help="Folder containing GTP PDFs")
    parser.add_argument("--output-dir", default=None, help="Where to write reports (default: <folder>/reports)")
    parser.add_argument("--gtp-type", default="A", help="GTP type A/B/C to price (default: A)")
    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        print(f"ERROR: Not a directory: {args.folder}")
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(args.folder, "reports")
    run_batch(args.folder, output_dir, args.gtp_type)
