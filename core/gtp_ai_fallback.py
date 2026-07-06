"""
AI fallback for GTP parsing.

Called when the regex parser produces a low-confidence cable dict.
Sends the raw PDF section text to Claude Haiku and maps the
structured JSON response back into a cable dict.
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = (
    "You are an expert electrical cable specification parser. "
    "Extract precise numerical data from GTP (General Technical Parameters) "
    "documents used in the Indian cable manufacturing industry. "
    "Return only valid JSON — no markdown fences, no explanation."
)

_VALID_MATERIAL_KEYS = {
    "conductor", "fine_wire_conductor",
    "xlpe_insulation", "pvc_insulation", "rubber_insulation",
    "conductor_screen", "insulation_screen",
    "copper_tape_screen", "copper_wire_screen", "petp_tape_screen",
    "gs_flat_strip_armour", "gs_round_wire_armour",
    "swelling_tape", "binder_tape", "binding_tape",
    "drain_wire", "pp_filler", "glass_mica_tape",
    "bedding", "pvc_inner_sheath",
    "frlsh_outer_sheath", "pvc_outer_sheath", "pvc_frlsh_sheath", "lszh_outer_sheath",
}

_PROMPT_TEMPLATE = """\
Extract cable specification data from the GTP section below.
{col_hint}
Return a single JSON object — use null for any value not found:

{{
  "designation": "<cable designation string>",
  "num_cores": <integer — for 3.5C use 4>,
  "is_half_neutral": <true if 3.5C cable, else false>,
  "conductor_area_mm2": <float>,
  "conductor_material": "<copper or aluminium>",
  "conductor_shape": "<round or sector>",
  "dc_resistance_ohm_per_km": <float — phase DC resistance>,
  "neutral_dc_resistance_ohm_per_km": <float or null — only for 3.5C>,
  "neutral_area_mm2": <float or null — only for 3.5C>,
  "conductor_od_mm": <float or null>,
  "voltage_kv": "<e.g. 1.1 or 11/11 or 33/33>",
  "cable_type": "<one of: lt, ht_11kv, mv_22_33kv, control, instrumentation, flexible>",
  "standard": "<IS standard e.g. IS 7098-1>",
  "layers": [
    {{
      "layer_name": "<human-readable name>",
      "material_key": "<must be one of the valid keys listed below>",
      "nominal_thickness_mm": <float or null>,
      "thickness_type": "Nominal",
      "tape_overlap_pct": <float or null>,
      "tape_thickness_mm": <float or null>,
      "armour_strip_width_mm": <float or null>,
      "armour_strip_thickness_mm": <float or null>,
      "wire_diameter_mm": <float or null>,
      "od_mm": <float or null>
    }}
  ],
  "overall_od_mm": <float or null>,
  "current_rating_A": <int or null>,
  "delivery_length_m": <int — default 1000>,
  "drum_type": "<wooden or steel>"
}}

Valid material_key values (use exactly):
{valid_keys}

Important rules:
- Thickness priority: Nominal > Average > Minimum
- For 3.5C: neutral DC resistance is HIGHER than phase (smaller cross-section)
- Insulation layer comes before inner sheath, armour, outer sheath (in that order)

GTP Section Text:
---
{section_text}
---"""


def ai_parse_cable(
    raw_section_text: str,
    col_index: Optional[int] = None,
    fallback_hint: Optional[dict] = None,
) -> Optional[dict]:
    """
    Re-parse a cable section using Claude Haiku when the regex parser
    returned a low-confidence result.

    Args:
        raw_section_text: Raw PDF text for this cable's section.
        col_index:        0 or 1 for company-format (two cables per page);
                          None for RAVIN single-cable format.
        fallback_hint:    Low-confidence dict from the regex parser — used
                          to fill fields the AI doesn't return.

    Returns:
        Cable dict tagged _parser="ai_fallback", or None on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI GTP fallback")
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — run: pip install anthropic")
        return None

    col_hint = ""
    if col_index is not None:
        side = "LEFT" if col_index == 0 else "RIGHT"
        col_hint = (
            f"NOTE: This page has TWO cables side by side. "
            f"Extract the {side} column (cable #{col_index + 1}).\n"
        )

    prompt = _PROMPT_TEMPLATE.format(
        col_hint=col_hint,
        valid_keys=", ".join(sorted(_VALID_MATERIAL_KEYS)),
        section_text=raw_section_text[:8000],
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip any accidental markdown fences
        raw = re.sub(r"^```[a-z]*\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"AI fallback returned invalid JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"AI fallback API call failed: {e}")
        return None

    # Validate/repair material keys
    for layer in data.get("layers") or []:
        mk = layer.get("material_key", "")
        if mk not in _VALID_MATERIAL_KEYS:
            layer["material_key"] = _nearest_material_key(mk)

    # Fill gaps with the regex parser's result
    if fallback_hint:
        for key, val in fallback_hint.items():
            if key.startswith("_"):
                continue
            if data.get(key) is None and val is not None:
                data[key] = val

    # Ensure required defaults
    data.setdefault("delivery_length_m", 1000)
    data.setdefault("drum_type", "wooden")
    data.setdefault("conductor_class", 2)
    data.setdefault("fine_wire", False)
    data.setdefault("num_wires", 7)
    data.setdefault("wire_dia_mm", None)
    data.setdefault("n_pairs", 1)
    data.setdefault("overall_od_tolerance_mm", None)

    # Normalise layer dicts
    for layer in data.get("layers") or []:
        layer.setdefault("od_mm", None)
        layer.setdefault("armour_strip_width_mm", None)
        layer.setdefault("armour_strip_thickness_mm", None)
        layer.setdefault("tape_overlap_pct", None)
        layer.setdefault("tape_thickness_mm", None)
        layer.setdefault("wire_diameter_mm", None)
        layer.setdefault("thickness_type", "Nominal")

    data["_parser"] = "ai_fallback"

    logger.info(
        f"AI fallback succeeded for cable: {data.get('designation', '?')!r}"
    )
    return data


def _nearest_material_key(raw: str) -> str:
    """Map an unrecognised material_key string to the closest valid key."""
    r = raw.lower()
    if "pvc" in r and ("insul" in r or "ins" in r):
        return "pvc_insulation"
    if "xlpe" in r or "insul" in r:
        return "xlpe_insulation"
    if "conductor" in r and "screen" in r:
        return "conductor_screen"
    if "insul" in r and "screen" in r:
        return "insulation_screen"
    if "round" in r and ("wire" in r or "armour" in r or "armor" in r):
        return "gs_round_wire_armour"
    if "armour" in r or "armor" in r or "strip" in r:
        return "gs_flat_strip_armour"
    if "lszh" in r or "ls0h" in r:
        return "lszh_outer_sheath"
    if "frlsh" in r or "fr-lsh" in r:
        return "frlsh_outer_sheath"
    if "outer" in r or ("sheath" in r and "inner" not in r):
        return "frlsh_outer_sheath"
    if "inner" in r or "bedding" in r:
        return "pvc_inner_sheath"
    if "copper" in r and "tape" in r:
        return "copper_tape_screen"
    if "mica" in r or "glass" in r or "fire" in r:
        return "glass_mica_tape"
    if "binder" in r:
        return "binder_tape"
    if "petp" in r or "polyester" in r:
        return "petp_tape_screen"
    return "xlpe_insulation"
