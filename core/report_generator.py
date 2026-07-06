"""
PDF report generator using reportlab.
Produces two types: 'production' (BOM with control parameters) and 'pricing' (costing report).
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from datetime import datetime


_W, _H = A4
MARGIN = 15 * mm

RAVIN_BLUE = colors.HexColor("#003366")
HEADER_BG  = colors.HexColor("#003366")
ALT_ROW    = colors.HexColor("#EBF0F8")


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("Title2", parent=s["Title"], fontSize=14, textColor=RAVIN_BLUE, spaceAfter=4))
    s.add(ParagraphStyle("SubHead", parent=s["Normal"], fontSize=9, textColor=RAVIN_BLUE, fontName="Helvetica-Bold"))
    s.add(ParagraphStyle("Small", parent=s["Normal"], fontSize=7))
    return s


def _table_style(col_widths, has_header=True, num_rows=200):
    cmds = [
        ("FONTNAME",  (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",  (0, 0), (-1, -1), 7),
        ("GRID",      (0, 0), (-1, -1), 0.3, colors.grey),
        ("VALIGN",    (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
    ]
    if has_header:
        cmds += [
            ("BACKGROUND",  (0, 0), (-1, 0), HEADER_BG),
            ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, 0), 7.5),
        ]
    for i in range(2, num_rows, 2):
        cmds.append(("BACKGROUND", (0, i), (-1, i), ALT_ROW))
    return TableStyle(cmds)


def generate_bom_pdf(gtp_data: dict, cable_results: list[dict], output_path: str, report_type: str = "production"):
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN
    )
    styles = _styles()
    story = []

    # Header
    story.append(Paragraph("RAVIN CABLES LTD", styles["Title2"]))
    story.append(Paragraph(
        f"{'Production BOM Report' if report_type == 'production' else 'Bill of Materials'}  |  "
        f"GTP Ref: {gtp_data.get('gtp_ref', '')}  |  "
        f"Type: {gtp_data.get('gtp_type', '')}  |  "
        f"Date: {datetime.utcnow().strftime('%d-%b-%Y')}",
        styles["SubHead"]
    ))
    story.append(Paragraph(
        f"Customer: {gtp_data.get('customer', '')}  |  Project: {gtp_data.get('project', '')}",
        styles["Small"]
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=RAVIN_BLUE, spaceAfter=8))

    bom_key = "bom_production" if report_type == "production" else "bom_costing"

    for cable in cable_results:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(
            f"Item {cable.get('item_no', '')} — {cable.get('config', '')} {cable.get('designation', '')} | "
            f"{cable.get('voltage_kv', '')} kV | Delivery: {cable.get('delivery_length_m', 1000)} m",
            styles["SubHead"]
        ))

        bom_rows = cable.get(bom_key, [])
        if not bom_rows:
            story.append(Paragraph("No BOM data.", styles["Small"]))
            continue

        header = ["Layer", "Material", "OD (mm)", "Thickness (mm)", "Weight (kg/km)"]
        table_data = [header]
        for row in bom_rows:
            table_data.append([
                row.get("layer", ""),
                row.get("material", "").replace("_", " ").title(),
                str(round(row.get("od_mm", 0), 2)) if row.get("od_mm") else "-",
                str(round(row.get("effective_thickness_mm", 0), 3)) if row.get("effective_thickness_mm") else "-",
                f"{row.get('weight_kg_per_km', 0):,.3f}",
            ])

        total_weight = sum(r.get("weight_kg_per_km", 0) for r in bom_rows)
        table_data.append(["", "TOTAL", "", "", f"{total_weight:,.3f}"])

        col_w = [55*mm, 50*mm, 25*mm, 30*mm, 35*mm]
        t = Table(table_data, colWidths=col_w, repeatRows=1)
        t.setStyle(_table_style(col_w, num_rows=len(table_data)))
        # Bold total row
        t.setStyle(TableStyle([
            ("FONTNAME", (0, len(table_data)-1), (-1, len(table_data)-1), "Helvetica-Bold"),
            ("BACKGROUND", (0, len(table_data)-1), (-1, len(table_data)-1), colors.HexColor("#D0DCF0")),
        ]))
        story.append(t)

    doc.build(story)


def generate_costing_pdf(gtp_ref: str, gtp_type: str, results: list[dict], output_path: str):
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN
    )
    styles = _styles()
    story = []

    story.append(Paragraph("RAVIN CABLES LTD", styles["Title2"]))
    story.append(Paragraph(
        f"PRICING REPORT  |  GTP Ref: {gtp_ref}  |  Type: {gtp_type}  |  "
        f"Date: {datetime.utcnow().strftime('%d-%b-%Y')}  |  CONFIDENTIAL",
        styles["SubHead"]
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=RAVIN_BLUE, spaceAfter=8))

    # Summary table
    summary_header = ["#", "Cable", "Config", "Del. (m)", "Floor Price/km (₹)", "Margin %", "Selling Price/km (₹)", "Per Drum (₹)"]
    summary_data = [summary_header]
    for r in results:
        summary_data.append([
            str(r.get("item_no", "")),
            r.get("designation", ""),
            r.get("config", ""),
            str(r.get("delivery_length_m", "")),
            f"{r['floor_price_per_km']:,.2f}",
            f"{r['margin_pct']:.1f}%",
            f"{r['selling_price_per_km']:,.2f}",
            f"{r['selling_price_per_drum_length']:,.2f}",
        ])

    col_w = [8*mm, 35*mm, 30*mm, 15*mm, 30*mm, 18*mm, 30*mm, 27*mm]
    t = Table(summary_data, colWidths=col_w, repeatRows=1)
    t.setStyle(_table_style(col_w, num_rows=len(summary_data)))
    story.append(t)
    story.append(Spacer(1, 6 * mm))

    # Detailed material breakdown per cable
    for r in results:
        story.append(Paragraph(
            f"Material Breakdown — {r['config']} {r['designation']}",
            styles["SubHead"]
        ))
        detail_header = ["Layer", "Material", "Wt (kg/km)", "Price (₹/kg)", "Cost (₹/km)"]
        detail_data = [detail_header]
        for row in r.get("material_breakdown", []):
            detail_data.append([
                row.get("layer", ""),
                row.get("material", "").replace("_", " ").title(),
                f"{row['weight_kg_per_km']:,.3f}",
                f"{row['price_per_kg']:,.2f}" if row['price_per_kg'] else "—",
                f"{row['cost_per_km']:,.2f}" if row['cost_per_km'] else "—",
            ])

        # Summary rows
        detail_data.append(["", "Material Cost/km", "", "", f"₹ {r['material_cost_per_km']:,.2f}"])
        detail_data.append(["", "Drum Cost/km", "", "", f"₹ {r['drum_cost_per_km']:,.2f}"])
        detail_data.append(["", "Conversion Cost/km", "", "", f"₹ {r['conversion_cost_per_km']:,.2f}"])
        detail_data.append(["", "TOTAL COST/km", "", "", f"₹ {r['total_cost_per_km']:,.2f}"])

        col_w2 = [45*mm, 55*mm, 25*mm, 25*mm, 30*mm]
        t2 = Table(detail_data, colWidths=col_w2, repeatRows=1)
        t2.setStyle(_table_style(col_w2, num_rows=len(detail_data)))
        last = len(detail_data) - 1
        t2.setStyle(TableStyle([
            ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
            ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#D0DCF0")),
        ]))
        story.append(t2)
        story.append(Spacer(1, 5 * mm))

    doc.build(story)
