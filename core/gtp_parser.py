"""
GTP Parser — two modes:
  'direct'  — regex-based, no API needed, uses fixed GTP template structure
  'claude'  — sends PDF to Claude API, handles format variations

Set PARSER_MODE = 'direct' or 'claude' (or pass mode= to parse_gtp()).
"""

import json
import os
from typing import Optional
import pdfplumber
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

# Default mode — change to 'claude' to use the API
PARSER_MODE = os.environ.get("PARSER_MODE", "direct")


def extract_pdf_text(pdf_path: str) -> str:
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
            for table in pdf.pages[0].extract_tables() if False else []:
                for row in table:
                    if row:
                        parts.append(" | ".join(str(c) for c in row if c))
    return "\n".join(parts)


# ── Direct parser (no API) ────────────────────────────────────────────────────
def parse_gtp_direct(pdf_path: str, gtp_type_override: Optional[str] = None) -> dict:
    from core.gtp_parser_direct import parse_gtp_direct as _direct
    return _direct(pdf_path, gtp_type_override)


# ── Claude API parser ─────────────────────────────────────────────────────────
PARSE_PROMPT = """You are a cable engineering expert. Extract ALL cable specifications from the GTP (General Technical Parameters) document.

Return a JSON object with this exact structure:
{
  "gtp_ref": "document reference number",
  "customer": "string",
  "project": "string",
  "date": "string",
  "gtp_type": null,
  "cables": [
    {
      "item_no": 1,
      "designation": "e.g. A2XY-FRLSH",
      "config": "e.g. 3.5C x 70mm²",
      "num_cores": 3,
      "conductor_area_mm2": 70.0,
      "voltage_kv": "1.1",
      "standard": "IS 7098-1",
      "conductor_material": "aluminium",
      "conductor_shape": "round",   // "round", "sector", or "compacted"
      "conductor_class": 2,
      "num_wires": 19,
      "wire_dia_mm": null,
      "conductor_od_mm": 10.5,
      "dc_resistance_ohm_per_km": 0.443,
      "layers": [
        {
          "layer_name": "XLPE Insulation",
          "material_key": "xlpe_insulation",
          "nominal_thickness_mm": 3.5,
          "thickness_type": "Minimum",
          "od_mm": null,
          "armour_strip_width_mm": null,
          "armour_strip_thickness_mm": null,
          "tape_overlap_pct": null,
          "tape_thickness_mm": null
        }
      ],
      "overall_od_mm": 32.0,
      "overall_od_tolerance_mm": 2.0,
      "current_rating_A": 175,
      "delivery_length_m": 1000,
      "drum_type": "wooden"
    }
  ]
}

material_key must be one of: xlpe_insulation, pvc_insulation, conductor_screen, insulation_screen, copper_tape_screen, pvc_armoured_sheath, gs_flat_strip_armour, frlsh_sheath
Extract EVERY cable. Use null for missing values. Return ONLY valid JSON."""


def parse_gtp_claude(pdf_path: str, gtp_type_override: Optional[str] = None) -> dict:
    import anthropic

    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    gtp_type = gtp_type_override
    if gtp_type is None:
        for suffix in ["A", "B", "C"]:
            if basename.upper().endswith(suffix):
                gtp_type = suffix
                break

    raw_text = extract_pdf_text(pdf_path)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8192,
        system="You are a cable engineering expert. Return only valid JSON.",
        messages=[{"role": "user", "content": PARSE_PROMPT + "\n\nGTP TEXT:\n" + raw_text}]
    )

    text = message.content[0].text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    gtp_data = json.loads(text)
    if gtp_type:
        gtp_data["gtp_type"] = gtp_type
    gtp_data["_parser"] = "claude"
    return gtp_data


# ── Unified entry point ───────────────────────────────────────────────────────
def parse_gtp(pdf_path: str, gtp_type_override: Optional[str] = None,
              mode: Optional[str] = None) -> dict:
    """
    Parse a GTP PDF.
    mode='direct'  → regex parser, no API (default)
    mode='claude'  → Claude API parser (requires ANTHROPIC_API_KEY)
    """
    selected = mode or PARSER_MODE
    if selected == "claude":
        if not ANTHROPIC_API_KEY:
            print("[Parser] ANTHROPIC_API_KEY not set, falling back to direct parser")
            selected = "direct"
    if selected == "direct":
        return parse_gtp_direct(pdf_path, gtp_type_override)
    else:
        return parse_gtp_claude(pdf_path, gtp_type_override)
