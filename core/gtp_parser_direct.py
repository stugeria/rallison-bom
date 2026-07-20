"""
Direct GTP Parser — no API needed.
Parses the fixed-format GTP template using regex on extracted PDF text.
Works because every GTP follows the same numbered-section format.
"""

import json
import logging
import os
import re
import pdfplumber
from typing import Optional

logger = logging.getLogger(__name__)


# ── Section number mappings ─────────────────────────────────────────────────
# LT cable (1.1kV):     2.3=Conductor, 2.4=Insulation, 2.5=InnerSheath,
#                       2.6=Armour, 2.7=OuterSheath
# HT/MV cable (11kV+): 2.3=Conductor, 2.4=ConductorScreen, 2.5=Insulation,
#                       2.6=InsulationScreen, 2.7=MetallicScreen, 2.8=InnerSheath,
#                       2.9=Armour, 2.10=OuterSheath
# Control cable (IS 1554): 2.3=Conductor, 2.4=Insulation, 2.5=BinderTape,
#                          2.6=InnerSheath, 2.7=Armour, 2.8=OuterSheath
# Instrumentation cable:   2.3=Conductor, 2.4=Insulation, 2.5=IndivScreen,
#                          2.6=BindingTape, 2.7=InnerSheath, 2.8=Armour, 2.9=OuterSheath
# Flexible cable (IS 694 Class 5/6): 2.3=Conductor, 2.4=Insulation, 2.5=OuterSheath

LT_SECTIONS = {
    "2.3": "conductor",
    "2.4": "insulation",
    "2.5": "inner_sheath",
    "2.6": "armour",
    "2.7": "outer_sheath",
}
HT_SECTIONS = {
    "2.3": "conductor",
    "2.4": "conductor_screen",
    "2.5": "insulation",
    "2.6": "insulation_screen",
    "2.7": "metallic_screen",
    "2.8": "inner_sheath",
    "2.9": "armour",
    "2.10": "outer_sheath",
}
CTRL_SECTIONS = {
    "2.3": "conductor",
    "2.4": "insulation",
    "2.5": "binder_tape",
    "2.6": "inner_sheath",
    "2.7": "armour",
    "2.8": "outer_sheath",
}
INSTR_SECTIONS = {
    "2.3": "conductor",
    "2.4": "insulation",
    "2.5": "indiv_screen",
    "2.6": "binding_tape",
    "2.7": "inner_sheath",
    "2.8": "armour",
    "2.9": "outer_sheath",
}
FLEX_SECTIONS = {
    "2.3": "conductor",
    "2.4": "insulation",
    "2.5": "outer_sheath",
}

MATERIAL_KEY_MAP = {
    "conductor":        "conductor",
    "insulation":       "xlpe_insulation",    # overridden to pvc_insulation if PVC
    "conductor_screen": "semicon_screen",
    "insulation_screen":"semicon_screen",
    "metallic_screen":  "copper_tape_screen",
    "inner_sheath":     "pvc_armoured_sheath",
    "armour":           "gs_flat_strip_armour",
    "outer_sheath":     "frlsh_sheath",
    "binder_tape":      "binder_tape",
    "indiv_screen":     "petp_tape_screen",
    "binding_tape":     "binding_tape",
}

# Cable type identifiers
_CTRL_PATTERNS = re.compile(r'IS\s*1554|control\s+cable|CTRL|CVV|CVVS|CY\b', re.I)
_INSTR_PATTERNS = re.compile(r'instrumentation|ISC\b|ISCYSY|triad|pairs\s+\&|screened\s+pair', re.I)
_FLEX_PATTERNS = re.compile(r'IS\s*694.*class\s*[56]|H0[57][VR][VN]-F|class\s*[56]\s*flex|flexible\s+cable', re.I)
_MV_22_PATTERNS = re.compile(r'22\s*/\s*22|22\s*kV|IS\s*7098-2.*22|33\s*/\s*33|33\s*kV|IS\s*7098-3', re.I)


def extract_pdf_text(pdf_path: str) -> str:
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _first_float(text: str) -> Optional[float]:
    """Extract the first decimal number from a string."""
    m = re.search(r'(\d+(?:\.\d+)?)', text)
    return float(m.group(1)) if m else None


def _parse_header(raw_text: str) -> dict:
    """Extract document-level metadata."""
    meta = {
        "gtp_ref": None,
        "customer": None,
        "project": None,
        "date": None,
    }
    for line in raw_text.split("\n")[:30]:
        if "Customer:" in line:
            meta["customer"] = line.split("Customer:")[-1].split("TECHNICAL")[0].strip()
        elif "Project:" in line:
            meta["project"] = line.split("Project:")[-1].strip()
        elif "Date :" in line or "Date:" in line:
            meta["date"] = re.sub(r"Date\s*:", "", line).strip()
        elif "Ref No:" in line:
            m = re.search(r'Ref No:\s*(\S+)', line)
            if m:
                meta["gtp_ref"] = m.group(1)
        elif "Doc Ref:" in line:
            m = re.search(r'Doc Ref:\s*(\S+)', line)
            if m and not meta.get("gtp_ref"):
                meta["gtp_ref"] = m.group(1)
    return meta


def _split_cable_sections(raw_text: str) -> list:
    """
    Split the raw text into per-cable sections.
    The GTP text has each cable appear twice (data + template).
    We only want the data blocks (the ones WITH values on the same line).
    """
    # Split on lines that start a cable description
    # Pattern: "DESCRIPTION Unit NcxNmm² , CODE" (first occurrence has values)
    # or just "NcxNmm²" as a standalone header line
    sections = []
    # Split on DESCRIPTION markers
    parts = re.split(r'\nDESCRIPTION\b', raw_text)
    for part in parts[1:]:  # skip header
        # Skip template blocks (they have "DESCRIPTION | Unit |" format, values separated by "|")
        if part.strip().startswith("|") or "| Unit |" in part[:50]:
            continue
        # Skip if it's just the footer note block
        if "Note:This is system generated" in part[:200] and len(part) < 400:
            continue
        sections.append(part)
    return sections


def _detect_cable_type(section_text: str) -> str:
    """
    Detect cable type from section text.
    Returns one of: 'lt', 'ht_11kv', 'mv_22_33kv', 'control', 'instrumentation', 'flexible'
    """
    if _MV_22_PATTERNS.search(section_text):
        return "mv_22_33kv"
    if re.search(r'11\s*/\s*11|11KV|11\s*kV|IS\s*7098-2|Conductor\s+Screen', section_text, re.I):
        return "ht_11kv"
    if _INSTR_PATTERNS.search(section_text):
        return "instrumentation"
    if _CTRL_PATTERNS.search(section_text):
        return "control"
    if _FLEX_PATTERNS.search(section_text):
        return "flexible"
    return "lt"


def _detect_is_ht(section_text: str) -> bool:
    """Return True if this cable section is an 11kV or MV HT cable (has screened layers)."""
    t = _detect_cable_type(section_text)
    return t in ("ht_11kv", "mv_22_33kv")


def _extract_cable_header(section_text: str, item_no: int) -> dict:
    """Extract item config and designation from the first line of a section."""
    first_line = section_text.strip().split("\n")[0]
    # Strip leading "Unit" keyword and pipes
    first_line = re.sub(r'^[\|\s]*Unit[\|\s]*', '', first_line).strip()

    # e.g. "3.5C X 70mm² , A2XY-FRLSH"  or  "1C X 400mm² , A2XY-FRLSH"
    m = re.match(r'(\d+(?:\.\d+)?C)\s+[Xx]\s+(\d+(?:\.\d+)?)mm[²2]\s*[,\s]+(\S+)', first_line)
    if not m:
        return {"item_no": item_no, "config": first_line.strip(), "designation": ""}

    num_cores_str = m.group(1)          # "3.5C"
    area_str = m.group(2)               # "70"
    designation = m.group(3).strip()    # "A2XY-FRLSH"
    num_cores = float(num_cores_str.replace("C", "").replace("c", ""))
    config = f"{num_cores_str} x {area_str}mm²"

    # Conductor material from designation
    conductor_material = "aluminium" if designation.upper().startswith("A") else "copper"

    return {
        "item_no": item_no,
        "designation": designation,
        "config": config,
        "num_cores": num_cores,
        "conductor_area_mm2": float(area_str),
        "conductor_material": conductor_material,
    }


def _split_into_major_sections(text: str) -> dict:
    """
    Split cable section text into a dict of major section blocks.
    Keys like '2.3', '2.4', '2.10' map to the full text of that section
    including all sub-sections (2.4.1, 2.4.2 ...).
    """
    # Find all major section boundaries: lines starting with "2.N " or "2.NN "
    # Major = exactly one dot in the section number (2.3, 2.10 — not 2.3.1)
    section_starts = []
    for m in re.finditer(r'^(2\.\d+)\s', text, re.MULTILINE):
        # Must be a major section (no third number component)
        sec_id = m.group(1)
        section_starts.append((m.start(), sec_id))

    if not section_starts:
        return {}

    blocks = {}
    for i, (start, sec_id) in enumerate(section_starts):
        end = section_starts[i + 1][0] if i + 1 < len(section_starts) else len(text)
        blocks[sec_id] = text[start:end]
    return blocks


def _extract_section_block(text: str, section_id: str) -> str:
    """Return the block for a major section like '2.4'."""
    blocks = _split_into_major_sections(text)
    return blocks.get(section_id, "")


def _extract_field(section_text: str, *keywords: str) -> Optional[float]:
    """
    Search for lines containing any of the keywords and extract the first number.
    """
    for kw in keywords:
        for line in section_text.split("\n"):
            if kw.lower() in line.lower():
                val = _first_float(re.sub(r'.*' + re.escape(kw), '', line, flags=re.I))
                if val is not None:
                    return val
    return None


def _parse_ph_n_pair(value_str: str) -> tuple:
    """
    Parse a Ph/N slash-separated value string like '0.89 / 0.71' or '1.10/0.90'.
    Returns (phase_value, neutral_value). If no slash, neutral is None.
    """
    parts = re.split(r'\s*/\s*', value_str.strip())
    phase = _first_float(parts[0]) if parts else None
    neutral = _first_float(parts[1]) if len(parts) > 1 else None
    return phase, neutral


def _parse_thickness_and_type(section_text: str) -> tuple:
    """
    Returns (phase_thickness_mm, thickness_type, neutral_thickness_mm).

    Priority (highest first):
      1. Nominal Thickness  → type = "Nominal"
      2. Average Thickness  → type = "Nominal"  (IS standard: average = nominal design value)
      3. Approximate / Non-metallic → type = "Nominal"
      4. Minimum Thickness only → type = "Minimum", value = min + 0.2 mm tolerance

    When both Nominal and Minimum are present in the same section (common in IS 7098),
    Nominal is used and Minimum is ignored — per user requirement.
    When only Minimum is present, a fixed +0.2 mm tolerance is applied for calculation
    but the type tag remains "Minimum" to record its origin.

    For 3.5C cables the GTP uses Ph/N slash notation e.g. '1.10 / 0.90'.
    """
    nominal_ph = nominal_neu = None
    average_ph = average_neu = None
    approx_ph  = approx_neu  = None
    minimum_ph = minimum_neu = None

    for line in section_text.split("\n"):
        ll = line.lower()

        if "nominal thickness" in ll:
            remainder = re.sub(r'.*nominal\s+thickness', '', line, flags=re.I)
            ph, neu = _parse_ph_n_pair(remainder)
            if ph and ph > 0 and nominal_ph is None:
                nominal_ph, nominal_neu = ph, neu

        if "average thickness" in ll:
            remainder = re.sub(r'.*average\s+thickness', '', line, flags=re.I)
            ph, neu = _parse_ph_n_pair(remainder)
            if ph and ph > 0 and average_ph is None:
                average_ph, average_neu = ph, neu

        if "approximate thickness" in ll or "non-metallic" in ll:
            remainder = re.sub(r'.*(?:approximate|non-metallic)[^0-9]*', '', line, flags=re.I)
            ph, neu = _parse_ph_n_pair(remainder)
            if ph and ph > 0 and approx_ph is None:
                approx_ph, approx_neu = ph, neu

        if "minimum thickness" in ll or "min. thickness" in ll:
            remainder = re.sub(r'.*(?:minimum|min\.)\s+thickness', '', line, flags=re.I)
            ph, neu = _parse_ph_n_pair(remainder)
            if ph and ph > 0 and minimum_ph is None:
                minimum_ph, minimum_neu = ph, neu

    if nominal_ph is not None:
        return nominal_ph, "Nominal", nominal_neu
    if average_ph is not None:
        return average_ph, "Nominal", average_neu
    if approx_ph is not None:
        return approx_ph, "Nominal", approx_neu
    if minimum_ph is not None:
        adjusted_neu = round(minimum_neu + 0.2, 4) if minimum_neu is not None else None
        return round(minimum_ph + 0.2, 4), "Minimum", adjusted_neu
    return None, None, None


def _parse_conductor_block(section_text: str, cable_info: dict) -> dict:
    """Parse the conductor section, including Ph/N neutral area for 3.5C cables."""
    od = _extract_field(section_text, "Approximate Diameter", "Nominal Diameter")

    shape = "round"
    for line in section_text.split("\n"):
        if "sector" in line.lower():
            shape = "sector"
            break

    # Neutral area: "Conductor Cross-Sectional Area (Ph/N) mm² 70-Ph/35-N"
    neutral_area = None
    for line in section_text.split("\n"):
        if re.search(r'Cross.Sectional.*Ph.*N|Ph.*N.*mm', line, re.I):
            # Pattern: "70-Ph/35-N"  or  "70 / 35"
            m = re.search(r'(\d+(?:\.\d+)?)\s*[-–]?\s*Ph\s*/\s*(\d+(?:\.\d+)?)\s*[-–]?\s*N', line, re.I)
            if m:
                neutral_area = float(m.group(2))
                break
            # Fallback: plain slash "70 / 35"
            m2 = re.search(r'(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)', line)
            if m2:
                neutral_area = float(m2.group(2))
                break

    return {
        "conductor_od_mm": od,
        "conductor_shape": shape,
        "neutral_area_mm2": neutral_area,
    }


def _parse_armour_block(section_text: str) -> Optional[dict]:
    """
    Parse armour section.
    Returns None if no armour, or a dict with armour parameters.
    Detects flat strip (width × thickness) vs round wire (single diameter).
    """
    lower = section_text.lower()
    if "no armour" in lower or "not applicable" in lower or "n/a" in lower:
        return None

    # Round wire armour: look for "round wire" keyword or single wire diameter
    is_round_wire = bool(re.search(r'round\s+wire|SWA|AWA|galvanised\s+steel\s+wire', section_text, re.I))

    # Flat strip: "width × thickness" pattern (e.g. "4 X 0.8")
    strip_m = re.search(r'(\d+(?:\.\d+)?)\s*[Xx×]\s*(\d+(?:\.\d+)?)', section_text)
    if strip_m and not is_round_wire:
        return {
            "type": "flat_strip",
            "armour_strip_width_mm": float(strip_m.group(1)),
            "armour_strip_thickness_mm": float(strip_m.group(2)),
        }

    # Round wire: single diameter value
    dia_m = re.search(r'(?:wire|nominal)\s+diameter.*?(\d+(?:\.\d+)?)', section_text, re.I)
    if dia_m or is_round_wire:
        dia = float(dia_m.group(1)) if dia_m else 1.6
        return {
            "type": "round_wire",
            "wire_diameter_mm": dia,
        }

    return None


def _parse_metallic_screen(section_text: str) -> Optional[dict]:
    """Parse copper/metallic tape screen."""
    thickness = _extract_field(section_text, "Nominal Thickness", "Metallic")
    if thickness:
        return {
            "tape_thickness_mm": thickness,
            "tape_overlap_pct": 15.0,   # standard overlap, not usually in GTP
        }
    return None


def _parse_cable_section(section_text: str, item_no: int, is_ht: bool) -> Optional[dict]:
    """Parse a complete cable section into a structured dict."""
    cable = _extract_cable_header(section_text, item_no)
    if not cable.get("designation"):
        return None

    cable_type = _detect_cable_type(section_text)

    # Determine voltage string
    _voltage_map = {
        "ht_11kv":        "11/11",
        "mv_22_33kv":     "22/22",
        "control":        "1.1",
        "instrumentation":"0.25",
        "flexible":       "0.3",
        "lt":             "1.1",
    }
    kv = _voltage_map.get(cable_type, "1.1")
    # Override from text if explicitly stated
    for line in section_text.split("\n")[:5]:
        if "33/33" in line or "33KV" in line.upper():
            kv = "33/33"
        elif "22/22" in line or "22KV" in line.upper():
            kv = "22/22"
        elif "11/11" in line or "11KV" in line.upper():
            kv = "11/11"
        elif "1.1" in line:
            kv = "1.1"

    # DC resistance — Ph/N format: "0.4430/0.8680"  or single value
    rdc_match = re.search(r'DC Resistance.*?(\d+\.\d+)\s*/\s*(\d+\.\d+)', section_text, re.I)
    if rdc_match:
        rdc = float(rdc_match.group(1))           # phase
        neutral_rdc = float(rdc_match.group(2))   # neutral (3.5C)
    else:
        rdc_match_single = re.search(r'DC Resistance.*?(\d+\.\d+)', section_text, re.I)
        rdc = float(rdc_match_single.group(1)) if rdc_match_single else None
        neutral_rdc = None

    # Delivery length
    del_m = re.search(r'Standard Delivery Length.*?(\d+)', section_text, re.I)
    delivery_length = int(del_m.group(1)) if del_m else 1000

    # Number of wires (look in full section)
    wire_m = re.search(r'Number of (?:Wires|Strands).*?(\d+)', section_text, re.I)
    num_wires = int(wire_m.group(1)) if wire_m else 7

    # Wire diameter
    wdia_m = re.search(r'(?:Nominal|Wire)\s+Wire\s+Diameter.*?(\d+\.\d+)', section_text, re.I)
    wire_dia = float(wdia_m.group(1)) if wdia_m else None

    # Conductor class for flexible cables
    conductor_class = 5 if cable_type == "flexible" else 2
    fine_wire = cable_type == "flexible"

    # Number of pairs for instrumentation cables
    n_pairs = 1
    pairs_m = re.search(r'(\d+)\s*(?:Pair|Pairs|Triad)', section_text, re.I)
    if pairs_m:
        n_pairs = int(pairs_m.group(1))

    # Choose section map
    _section_maps = {
        "ht_11kv":        HT_SECTIONS,
        "mv_22_33kv":     HT_SECTIONS,   # same screened structure
        "control":        CTRL_SECTIONS,
        "instrumentation":INSTR_SECTIONS,
        "flexible":       FLEX_SECTIONS,
        "lt":             LT_SECTIONS,
    }
    sections = _section_maps.get(cable_type, LT_SECTIONS)

    # Build layer list
    layers = []
    conductor_od = None

    def _sec_sort_key(item):
        parts = item[0].split(".")
        return tuple(int(p) for p in parts)

    for sec_id, layer_type in sorted(sections.items(), key=_sec_sort_key):
        block = _extract_section_block(section_text, sec_id)
        if not block:
            continue

        if layer_type == "conductor":
            info = _parse_conductor_block(block, cable)
            conductor_od = info.get("conductor_od_mm")
            cable["conductor_shape"] = info.get("conductor_shape", "round")
            if conductor_od:
                cable["conductor_od_mm"] = conductor_od
            if info.get("neutral_area_mm2"):
                cable["neutral_area_mm2"] = info["neutral_area_mm2"]
            continue

        elif layer_type == "armour":
            arm = _parse_armour_block(block)
            if arm:
                if arm.get("type") == "round_wire":
                    layers.append({
                        "layer_name": "GS Round Wire Armour",
                        "material_key": "gs_round_wire_armour",
                        "wire_diameter_mm": arm["wire_diameter_mm"],
                        "gap_mm": 0.5,
                        "od_mm": None,
                    })
                else:
                    layers.append({
                        "layer_name": "GS Flat Strip Armour",
                        "material_key": "gs_flat_strip_armour",
                        "nominal_thickness_mm": arm["armour_strip_thickness_mm"],
                        "thickness_type": "Nominal",
                        "armour_strip_width_mm": arm["armour_strip_width_mm"],
                        "armour_strip_thickness_mm": arm["armour_strip_thickness_mm"],
                        "od_mm": None,
                    })
            continue

        elif layer_type == "metallic_screen":
            scr = _parse_metallic_screen(block)
            if scr:
                # Detect copper wire screen vs copper tape screen
                is_wire_screen = bool(re.search(r'wire\s+screen|concentric\s+wire', block, re.I))
                if is_wire_screen:
                    n_wires_m = re.search(r'Number.*?(\d+)', block, re.I)
                    n_wires_scr = int(n_wires_m.group(1)) if n_wires_m else 16
                    layers.append({
                        "layer_name": "Copper Wire Screen",
                        "material_key": "copper_wire_screen",
                        "n_wires": n_wires_scr,
                        "wire_diameter_mm": scr["tape_thickness_mm"],  # reused field
                        "od_mm": None,
                    })
                else:
                    layers.append({
                        "layer_name": "Copper Tape Screen",
                        "material_key": "copper_tape_screen",
                        "nominal_thickness_mm": scr["tape_thickness_mm"],
                        "thickness_type": "Nominal",
                        "tape_thickness_mm": scr["tape_thickness_mm"],
                        "tape_overlap_pct": scr["tape_overlap_pct"],
                        "od_mm": None,
                    })
            continue

        elif layer_type == "binder_tape":
            thickness, _, _neu = _parse_thickness_and_type(block)
            layers.append({
                "layer_name": "Binder Tape",
                "material_key": "binder_tape",
                "tape_thickness_mm": thickness or 0.15,
                "tape_overlap_pct": 15.0,
                "od_mm": None,
            })
            continue

        elif layer_type == "binding_tape":
            thickness, _, _neu = _parse_thickness_and_type(block)
            layers.append({
                "layer_name": "Binding Tape",
                "material_key": "binding_tape",
                "tape_thickness_mm": thickness or 0.15,
                "tape_overlap_pct": 15.0,
                "od_mm": None,
            })
            continue

        elif layer_type == "indiv_screen":
            thickness, _, _neu = _parse_thickness_and_type(block)
            tape_t = thickness or 0.04  # default 40 microns per tape
            # Drain wire first — calculator bumps OD by 0.5mm before screen tapes
            layers.append({
                "layer_name": "Drain Wire",
                "material_key": "drain_wire",
                "n_pairs": n_pairs,
                "od_mm": None,
            })
            layers.append({
                "layer_name": "PE Tape Screen",
                "material_key": "pe_tape",
                "tape_thickness_mm": tape_t,
                "tape_overlap_pct": 15.0,
                "n_pairs": n_pairs,
                "od_mm": None,
            })
            layers.append({
                "layer_name": "Al Mylar PE Screen",
                "material_key": "al_mylar_pe_tape",
                "tape_thickness_mm": tape_t,
                "tape_overlap_pct": 15.0,
                "n_pairs": n_pairs,
                "od_mm": None,
            })
            continue

        else:
            # Generic thickness layer
            thickness, t_type, neutral_thickness = _parse_thickness_and_type(block)
            skip_keywords = ["not applicable", "not app", "n/a"]
            skip = any(kw in block.lower() for kw in skip_keywords)
            if skip or thickness is None:
                continue

            mat_key = MATERIAL_KEY_MAP.get(layer_type, layer_type)

            if layer_type == "insulation":
                if re.search(r'\bPVC\b', block, re.I) and not re.search(r'\bXLPE\b', block, re.I):
                    mat_key = "pvc_insulation"
                elif re.search(r'\bEPDM\b|\bEPR\b|\brubber\b', block, re.I):
                    mat_key = "rubber_insulation"
                else:
                    mat_key = "xlpe_insulation"

            if layer_type == "inner_sheath":
                mat_key = "pvc_inner_sheath" if cable_type in ("control", "instrumentation") else "pvc_armoured_sheath"

            if layer_type == "outer_sheath":
                if re.search(r'\bFR-LSH\b|\bFRLSH\b|\bFR LSH\b', block, re.I):
                    mat_key = "frlsh_sheath"
                else:
                    mat_key = "pvc_outer_sheath"

            layer_name_map = {
                "insulation":        "XLPE Insulation",
                "conductor_screen":  "Conductor Screen",
                "insulation_screen": "Insulation Screen",
                "inner_sheath":      "Inner Sheath",
                "outer_sheath":      "Outer Sheath",
            }
            layer_name = layer_name_map.get(layer_type, layer_type.replace("_", " ").title())

            if layer_type == "insulation":
                if mat_key == "pvc_insulation":
                    layer_name = "PVC Insulation"
                elif mat_key == "rubber_insulation":
                    layer_name = "EPDM/Rubber Insulation"

            layer_dict = {
                "layer_name": layer_name,
                "material_key": mat_key,
                "nominal_thickness_mm": thickness,
                "thickness_type": t_type or "Minimum",
                "od_mm": None,
                "armour_strip_width_mm": None,
                "armour_strip_thickness_mm": None,
                "tape_overlap_pct": None,
                "tape_thickness_mm": None,
            }
            # Store neutral thickness for 3.5C insulation layers
            if layer_type == "insulation" and neutral_thickness is not None:
                layer_dict["neutral_nominal_thickness_mm"] = neutral_thickness

            layers.append(layer_dict)

    # Overall OD
    od_m = re.search(r'Overall Diameter.*?(\d+(?:\.\d+)?)\s*[±\+\-]', section_text, re.I)
    overall_od = float(od_m.group(1)) if od_m else None

    # Current rating
    cr_m = re.search(r'Current Rating.*?(\d+)', section_text, re.I)
    current_rating = int(cr_m.group(1)) if cr_m else None

    # Standard
    std_m = re.search(r'Confirming to (IS \d+(?:-\d+)?)', section_text, re.I)
    _default_std = {
        "ht_11kv": "IS 7098-2", "mv_22_33kv": "IS 7098-3",
        "control": "IS 1554-1", "instrumentation": "IS 1554",
        "flexible": "IS 694", "lt": "IS 7098-1",
    }
    standard = std_m.group(1) if std_m else _default_std.get(cable_type, "IS 7098-1")

    drum_type = "steel" if cable_type in ("ht_11kv", "mv_22_33kv") else "wooden"

    cable.update({
        "voltage_kv": kv,
        "standard": standard,
        "cable_type": cable_type,
        "conductor_class": conductor_class,
        "fine_wire": fine_wire,
        "num_wires": num_wires,
        "wire_dia_mm": wire_dia,
        "conductor_od_mm": cable.get("conductor_od_mm"),
        "dc_resistance_ohm_per_km": rdc,
        "neutral_dc_resistance_ohm_per_km": neutral_rdc,
        "neutral_area_mm2": cable.get("neutral_area_mm2"),
        "layers": layers,
        "overall_od_mm": overall_od,
        "overall_od_tolerance_mm": None,
        "current_rating_A": current_rating,
        "delivery_length_m": delivery_length,
        "drum_type": drum_type,
        "n_pairs": n_pairs,
        "_raw_section_text": section_text,
        "_col_index": None,
    })
    return cable


def _validate_and_patch_cables(cables: list) -> list:
    """
    For each cable in the list:
      1. Run the validator.
      2. If confidence < 0.5, attempt an AI fallback parse.
      3. Strip internal _raw_section_text / _col_index fields before returning.
    """
    from .gtp_validator import validate_cable
    from .gtp_ai_fallback import ai_parse_cable

    patched = []
    for cable in cables:
        raw_text = cable.pop("_raw_section_text", None)
        col_index = cable.pop("_col_index", None)

        confidence, issues = validate_cable(cable)

        if confidence < 0.5 and raw_text:
            logger.warning(
                f"Low-confidence parse (score={confidence:.2f}) for "
                f"{cable.get('designation') or cable.get('config')!r}: "
                f"{issues} — attempting AI fallback"
            )
            ai_result = ai_parse_cable(
                raw_section_text=raw_text,
                col_index=col_index,
                fallback_hint=cable,
            )
            if ai_result:
                # Preserve item_no from the regex parse
                ai_result.setdefault("item_no", cable.get("item_no"))
                patched.append(ai_result)
                continue
            else:
                logger.warning("AI fallback also failed — keeping regex result as-is")

        patched.append(cable)

    return patched


def parse_gtp_direct(pdf_path: str, gtp_type_override: Optional[str] = None) -> dict:
    """
    Parse a GTP PDF without any API calls.
    Auto-detects format: 'company' (IS 17505 / numbered-row style) or 'ravin'.
    """
    import os
    raw_text = extract_pdf_text(pdf_path)

    fmt = _detect_format(raw_text)
    if fmt == "company":
        result = _parse_company_gtp(pdf_path, raw_text)
        result["cables"] = _validate_and_patch_cables(result["cables"])
        return result
    if fmt == "company_single":
        result = _parse_company_single_gtp(pdf_path, raw_text)
        result["cables"] = _validate_and_patch_cables(result["cables"])
        return result
    if fmt == "datasheet":
        result = _parse_datasheet_gtp(pdf_path, raw_text)
        result["cables"] = _validate_and_patch_cables(result["cables"])
        return result
    if fmt == "wire_datasheet":
        result = _parse_wire_datasheet_gtp(pdf_path, raw_text)
        result["cables"] = _validate_and_patch_cables(result["cables"])
        return result
    if fmt == "srno_datasheet":
        result = _parse_srno_gtp(pdf_path, raw_text)
        result["cables"] = _validate_and_patch_cables(result["cables"])
        return result

    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    gtp_type = gtp_type_override
    if gtp_type is None:
        for suffix in ["A", "B", "C"]:
            if basename.upper().endswith(suffix):
                gtp_type = suffix
                break

    meta = _parse_header(raw_text)
    sections = _split_cable_sections(raw_text)

    cables = []
    for i, section in enumerate(sections, start=1):
        is_ht = _detect_is_ht(section)
        cable = _parse_cable_section(section, i, is_ht)
        if cable and cable.get("dc_resistance_ohm_per_km"):
            cables.append(cable)

    cables = _validate_and_patch_cables(cables)

    return {
        "gtp_ref": meta.get("gtp_ref"),
        "customer": meta.get("customer"),
        "project": meta.get("project"),
        "date": meta.get("date"),
        "gtp_type": gtp_type,
        "cables": cables,
        "_parser": "direct",
    }


# ─────────────────────────────────────────────────────────────────────────────
# COMPANY GTP FORMAT
# Format: Sr. No. / letter sub-rows, sections 1.0–11.0, two cables per 2-page
# sheet laid out side-by-side. Each numeric row ends with val_cable1 val_cable2.
# ─────────────────────────────────────────────────────────────────────────────

# Section number → layer role in company GTP format
_COMPANY_SECTION_MAP = {
    "3.0": "conductor",
    "4.0": "fire_barrier_tape",
    "5.0": "insulation",
    "6.0": "inner_sheath",
    "7.0": "armour",
    "8.0": "outer_sheath",
    "10.0": "packing",
}


def _detect_format(raw_text: str) -> str:
    """Return 'company', 'company_single', 'datasheet', 'wire_datasheet',
    'srno_datasheet', or 'ravin'."""
    head = raw_text[:800]
    # Multi-cable IS 17505 tabular format (company GTP) — "Sr. No." contiguous
    # on one line, two designations side by side after "PROJECT :"
    if "Sr. No." in head and "PROJECT :" in head:
        return "company"
    # Same company/IS-7098 template (N.0-numbered sections: 3.0 Conductor,
    # 4.0 Insulation, ...), but one cable per page-pair instead of two side
    # by side, and the "Sr. No." header cell wraps across lines so it never
    # appears contiguous (e.g. "Sr." / "Description Unit" / "No." on
    # separate lines).
    if ("PROJECT :" in head and re.search(r"^Sr\.\s", head, re.M)
            and re.search(r"^No\.\s", head, re.M)
            and re.search(r"\bDescription\b", head) and re.search(r"\d\.0\s", head)):
        return "company_single"
    # Single-cable technical data sheet (Sr No. without dot, Project :, Data Sheet no)
    if re.search(r"Sr\s*No\.?\s+Name", head) and re.search(r"Data Sheet no", head, re.I):
        return "datasheet"
    # Multi-column Rallison wire data sheet (IS 694 style — multiple sizes side by side).
    # Size unit is usually "SQMM" but some sheets use "mm2"/"mm²" instead.
    if re.search(r"DATA SHEET FOR", head, re.I) and (
        head.count("SQMM") >= 2 or len(re.findall(r"mm2|mm²", head, re.I)) >= 2
    ):
        return "wire_datasheet"
    # Third-party lab GTP (e.g. LKB Engineering / "GTP Ref No") — numbered rows (1..14)
    # with roman-numeral sub-items (i, ii, iii...), one or more cable variants stacked
    # in the same PDF (e.g. same construction repeated for Class-2 and Class-5).
    if re.search(r"Sr\s*No\.?\s+Description\s+Unit", head, re.I):
        return "srno_datasheet"
    return "ravin"


def _split_company_sections(page_text: str) -> dict:
    """
    Split text into blocks keyed by N.0 section numbers (3.0, 4.0 … 11.0).
    Does NOT match sub-sections like 8.1.
    """
    starts = []
    for m in re.finditer(r'^(\d+)\.0\s', page_text, re.MULTILINE):
        starts.append((m.start(), m.group(1) + ".0"))
    if not starts:
        return {}
    blocks = {}
    for i, (start, sid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(page_text)
        blocks[sid] = page_text[start:end]
    return blocks


def _col_vals(line: str) -> tuple:
    """
    Extract (val1, val2) from a data line with two cable values at the end.
    Handles:
      - Plain floats:   'Description mm 1.8 1.7'
      - Tolerance:      'Size of armour mm 3.15 ± 0.080 2.5 ± 0.065'
      - Integer pairs:  'Overall diameter mm 68 61'
      - Single value:   'Cross Sectional Area Phase sqmm 300' → (300, None)
    Returns (float|None, float|None).
    """
    # Strip parenthetical annotations first — some sheets put an inline
    # tolerance note *before* the actual value, e.g.
    # "Size of armour ( ± 0.04 ) mm 1.4", which would otherwise be
    # misread as the value itself (0.04 instead of 1.4).
    line = re.sub(r'\([^)]*\)', '', line)

    # Tolerance format: value immediately before ±
    tol_vals = re.findall(r'(\d+(?:\.\d+)?)\s*±', line)
    if len(tol_vals) >= 2:
        return float(tol_vals[0]), float(tol_vals[1])
    if len(tol_vals) == 1:
        return float(tol_vals[0]), None

    # Float pairs/singles (skip integers split out of decimals like "0.100" → 0, 100)
    floats = re.findall(r'\b(\d+\.\d+)\b', line)
    if len(floats) >= 2:
        return float(floats[-2]), float(floats[-1])
    if len(floats) == 1:
        return float(floats[0]), None

    # Integer pairs/singles (filter out large standard/year codes ≥ 5000)
    ints = [int(n) for n in re.findall(r'\b(\d+)\b', line) if int(n) < 5000]
    if len(ints) >= 2:
        return float(ints[-2]), float(ints[-1])
    if len(ints) == 1:
        return float(ints[0]), None

    return None, None


def _find_strand_counts(block: str, col: int) -> tuple:
    """
    Parse 'No. of strands' row which uses phase/neutral slash format per column.
    e.g. 'dNo. of strands Nos. 37/19 19/19'
    Returns (phase_wires, neutral_wires) for the given cable column.
    """
    for line in block.split("\n"):
        if "strand" in line.lower() or "no. of wire" in line.lower():
            # Find all slash-pairs or plain integers
            pairs = re.findall(r'(\d+)\s*/\s*(\d+)', line)
            singles = re.findall(r'\b(\d+)\b', line)
            if pairs:
                entry = pairs[col] if col < len(pairs) else pairs[0]
                return int(entry[0]), int(entry[1])
            if singles:
                v = int(singles[col]) if col < len(singles) else int(singles[0])
                return v, v
    return None, None


def _find_vals(block: str, keyword: str) -> tuple:
    """
    Search block for a line containing keyword and return (val1, val2).
    When the row description wraps to the next line (values on the continuation
    line), also checks the next 2 lines.
    Returns (None, None) if the field is marked N/A or no numbers are found.
    """
    lines = block.split("\n")
    for i, line in enumerate(lines):
        if keyword.lower() in line.lower():
            # N/A / NA means the field is explicitly not applicable for this cable
            if re.search(r'\bN/?A\b', line, re.I):
                return None, None
            v1, v2 = _col_vals(line)
            if v1 is not None:
                return v1, v2
            for j in range(i + 1, min(i + 3, len(lines))):
                v1, v2 = _col_vals(lines[j])
                if v1 is not None:
                    return v1, v2
    return None, None


def _delivery_length_company(pack_block: str, col: int) -> int:
    """
    Extract delivery length (metres) for cable column col (0=left, 1=right).
    Handles '250 ± 5%' and '1000/500 ± 5%' formats.
    """
    for line in pack_block.split("\n"):
        if "packing length" not in line.lower() and "metres" not in line.lower():
            continue
        # Slash format: '1000/500 ± 5%' — take the first (larger) value
        slash_m = re.findall(r'\b(\d+)\s*/\s*\d+\s*±', line)
        if len(slash_m) >= 2:
            return int(slash_m[col])
        if len(slash_m) == 1:
            return int(slash_m[0])
        # Standard: '250 ± 5% 250 ± 5%'
        tol_vals = re.findall(r'(\d+(?:\.\d+)?)\s*±', line)
        if len(tol_vals) >= 2:
            return int(float(tol_vals[col]))
        if len(tol_vals) == 1:
            return int(float(tol_vals[0]))
        # Plain integers
        large = [int(n) for n in re.findall(r'\b(\d+)\b', line) if int(n) >= 50]
        if len(large) >= 2:
            return large[col if col < len(large) else 0]
        if large:
            return large[0]
    return 1000


def _parse_company_cable(pair_text: str, col: int, item_no: int) -> Optional[dict]:
    """
    Parse one cable (col 0=left, 1=right) from a combined 2-page sheet text.
    Returns a cable dict compatible with the existing BOM calculator.
    """
    # ── Header ────────────────────────────────────────────────────────────────
    proj_m = re.search(r'PROJECT\s*:\s*(\S+)\s+(\S+)', pair_text)
    if not proj_m:
        return None
    codes = [proj_m.group(1), proj_m.group(2)]
    designation = codes[col] if col < len(codes) else codes[0]

    # Config from description line — try two-cable format first, then single-cable
    desc_m = re.search(
        r'(\d+(?:\.\d+)?C\s+X\s+\d+\s+SQMM)\s+(\d+(?:\.\d+)?C\s+X\s+\d+\s+SQMM)',
        pair_text,
    )
    if desc_m:
        config = desc_m.group(col + 1).strip()
    else:
        single_m = re.search(r'(\d+(?:\.\d+)?C\s+X\s+\d+\s+SQMM)', pair_text)
        config = single_m.group(1).strip() if single_m else designation

    # num_cores from config '3.5C X ...' or from code '3.5cx...'
    cores_m = re.match(r'(\d+(?:\.\d+)?)C', config, re.I)
    if not cores_m:
        cores_m = re.match(r'(\d+(?:\.\d+)?)c', designation, re.I)
    num_cores = float(cores_m.group(1)) if cores_m else 3.0
    is_half_neutral = abs(num_cores - 3.5) < 0.01

    # Standard
    standard = "IS 17505-1" if "17505" in pair_text else "IS 7098-1"
    voltage_kv = "1.1"

    # ── Split into sections ───────────────────────────────────────────────────
    sections = _split_company_sections(pair_text)

    # ── Conductor (3.0) ───────────────────────────────────────────────────────
    cb = sections.get("3.0", "")

    phase_area_v1, phase_area_v2 = _find_vals(cb, "Cross Sectional Area Phase")
    phase_area = phase_area_v1 if col == 0 else phase_area_v2

    neu_area_v1, neu_area_v2 = _find_vals(cb, "Cross Sectional Area Neutral")
    neutral_area = (neu_area_v1 if col == 0 else neu_area_v2) if neu_area_v1 else None

    rdc_v1, rdc_v2 = _find_vals(cb, "Resistance Phase")
    phase_rdc = rdc_v1 if col == 0 else rdc_v2

    neu_rdc_v1, neu_rdc_v2 = _find_vals(cb, "Resistance Neutral")
    neutral_rdc = (neu_rdc_v1 if col == 0 else neu_rdc_v2) if neu_rdc_v1 else None

    phase_wires, neutral_wires = _find_strand_counts(cb, col)

    if re.search(r"sector", cb, re.I):
        conductor_shape = "sector"
    elif re.search(r"compact", cb, re.I):
        conductor_shape = "compacted"
    else:
        conductor_shape = "round"
    conductor_material = "aluminium" if re.search(r"\bAl\b|aluminium", cb, re.I) else "copper"

    # ── Detect section layout ──────────────────────────────────────────────────
    # IS 17505 format: 4.0=FireBarrier, 5.0=Insulation, 6.0=InnerSheath,
    #                  7.0=Armour, 8.0=OuterSheath
    # Rallison/basic format: 4.0=Insulation, 5.0=InnerSheath,
    #                        6.0=Armour, 7.0=OuterSheath
    # Detect by checking if section 4.0 contains insulation (not fire barrier)
    _sec4 = sections.get("4.0", "")
    _has_fire_barrier = bool(re.search(r"[Ff]ire\s+[Bb]arrier|[Gg]lass\s+[Mm]ica|mica\s+tape", _sec4))
    _has_insulation_in_4 = bool(re.search(r"\bXLPE\b|\bPVC\b|\binsulat", _sec4, re.I))

    if _has_insulation_in_4 and not _has_fire_barrier:
        # Rallison layout: sec4=Ins, sec5=InnerSheath, sec6=Armour, sec7=OuterSheath
        _ins_sec, _sheath_sec, _arm_sec, _os_sec = "4.0", "5.0", "6.0", "7.0"
        _has_fire_barrier_layer = False
    else:
        # IS 17505 layout: sec4=FireBarrier, sec5=Ins, sec6=InnerSheath, sec7=Armour, sec8=OuterSheath
        _ins_sec, _sheath_sec, _arm_sec, _os_sec = "5.0", "6.0", "7.0", "8.0"
        _has_fire_barrier_layer = _has_fire_barrier and bool(sections.get("4.0"))

    # ── Insulation ────────────────────────────────────────────────────────────
    ib = sections.get(_ins_sec, "")

    ins_ph_v1, ins_ph_v2 = _find_vals(ib, "Nominal Thickness (Phase)")
    if ins_ph_v1 is None:
        ins_ph_v1, ins_ph_v2 = _find_vals(ib, "Nominal Thickness")
    phase_ins_t = ins_ph_v1 if col == 0 else ins_ph_v2

    ins_neu_v1, ins_neu_v2 = _find_vals(ib, "Nominal Thickness (Neutral)")
    neutral_ins_t = (ins_neu_v1 if col == 0 else ins_neu_v2) if ins_neu_v1 else None

    ins_mat = "xlpe_insulation"
    if re.search(r"\bPVC\b", ib, re.I) and not re.search(r"\bXLPE\b", ib, re.I):
        ins_mat = "pvc_insulation"

    # ── Inner sheath ──────────────────────────────────────────────────────────
    sb = sections.get(_sheath_sec, "")
    sh_v1, sh_v2 = _find_vals(sb, "Nominal Thickness")
    inner_sheath_thickness_type = "Nominal"
    if sh_v1 is None:
        sh_v1, sh_v2 = _find_vals(sb, "Minimum Thickness")
        if sh_v1 is not None:
            sh_v1 = round(sh_v1 + 0.2, 4)
        if sh_v2 is not None:
            sh_v2 = round(sh_v2 + 0.2, 4)
        inner_sheath_thickness_type = "Minimum"
    inner_sheath_t = sh_v1 if col == 0 else sh_v2

    # ── Armour ────────────────────────────────────────────────────────────────
    ab = sections.get(_arm_sec, "")
    is_round_wire = bool(re.search(r"round\s+wire", ab, re.I))
    # Detect flat-strip "WxT" format (e.g. "4.0 x 0.8") — val1=width, val2=thickness
    strip_wt_m = re.search(r'[Ss]ize of armour.*?(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)', ab)
    if strip_wt_m and not is_round_wire:
        armour_strip_width = float(strip_wt_m.group(1))
        wire_dia = float(strip_wt_m.group(2))   # thickness
    else:
        arm_v1, arm_v2 = _find_vals(ab, "Size of armour")
        wire_dia = arm_v1 if col == 0 else arm_v2
        armour_strip_width = None

    # ── Outer sheath ──────────────────────────────────────────────────────────
    ob = sections.get(_os_sec, "")
    os_nom_v1, os_nom_v2 = _find_vals(ob, "Nominal Thickness")
    outer_sheath_thickness_type = "Nominal"
    if os_nom_v1 is None:
        os_nom_v1, os_nom_v2 = _find_vals(ob, "Minimum Thickness")
        if os_nom_v1 is not None:
            os_nom_v1 = round(os_nom_v1 + 0.2, 4)
        if os_nom_v2 is not None:
            os_nom_v2 = round(os_nom_v2 + 0.2, 4)
        outer_sheath_thickness_type = "Minimum"
    outer_sheath_t = os_nom_v1 if col == 0 else os_nom_v2

    od_v1, od_v2 = _find_vals(ob, "Overall diameter")
    overall_od = od_v1 if col == 0 else od_v2

    if re.search(r"\bLSZH\b|\bLSOH\b|\bLS0H\b", ob, re.I):
        os_mat = "lszh_outer_sheath"
    elif re.search(r"\bFR-LSH\b|\bFRLSH\b|\bFR LSH\b", ob, re.I):
        os_mat = "frlsh_sheath"
    else:
        os_mat = "pvc_outer_sheath"

    # ── Packing / delivery length (9.0 in Rallison format, 10.0 in others) ──
    pack_block = sections.get("9.0") or sections.get("10.0") or pair_text
    delivery_length = _delivery_length_company(pack_block, col)
    drum_type = "steel" if re.search(r'\bsteel\b', pack_block, re.I) else "wooden"

    # ── Build layer list ──────────────────────────────────────────────────────
    layers = []

    # Fire barrier tape (4.0) — only in IS 17505 layout
    if _has_fire_barrier_layer:
        layers.append({
            "layer_name": "Glass Mica Fire Barrier Tape",
            "material_key": "glass_mica_tape",
            "n_tapes": 2,
            "od_mm": None,
        })

    # Insulation
    if phase_ins_t:
        ins_layer = {
            "layer_name": "XLPE Insulation" if ins_mat == "xlpe_insulation" else "PVC Insulation",
            "material_key": ins_mat,
            "nominal_thickness_mm": phase_ins_t,
            "thickness_type": "Nominal",
            "od_mm": None,
            "armour_strip_width_mm": None,
            "armour_strip_thickness_mm": None,
            "tape_overlap_pct": None,
            "tape_thickness_mm": None,
        }
        if neutral_ins_t is not None:
            ins_layer["neutral_nominal_thickness_mm"] = neutral_ins_t
        layers.append(ins_layer)

    # Inner sheath
    if inner_sheath_t:
        layers.append({
            "layer_name": "Inner Sheath",
            "material_key": "pvc_inner_sheath",
            "nominal_thickness_mm": inner_sheath_t,
            "thickness_type": inner_sheath_thickness_type,
            "od_mm": None,
            "armour_strip_width_mm": None,
            "armour_strip_thickness_mm": None,
            "tape_overlap_pct": None,
            "tape_thickness_mm": None,
        })

    # Armour
    if wire_dia:
        if is_round_wire:
            layers.append({
                "layer_name": "GS Round Wire Armour",
                "material_key": "gs_round_wire_armour",
                "wire_diameter_mm": wire_dia,
                "gap_mm": 0.5,
                "od_mm": None,
            })
        else:
            layers.append({
                "layer_name": "GS Flat Strip Armour",
                "material_key": "gs_flat_strip_armour",
                "nominal_thickness_mm": wire_dia,
                "thickness_type": "Nominal",
                "armour_strip_width_mm": armour_strip_width,
                "armour_strip_thickness_mm": wire_dia,
                "od_mm": None,
            })

    # Outer sheath
    if outer_sheath_t:
        layers.append({
            "layer_name": {"lszh_outer_sheath": "LSZH Outer Sheath", "frlsh_sheath": "FR-LSH Outer Sheath"}.get(os_mat, "Outer Sheath"),
            "material_key": os_mat,
            "nominal_thickness_mm": outer_sheath_t,
            "thickness_type": outer_sheath_thickness_type,
            "od_mm": None,
            "armour_strip_width_mm": None,
            "armour_strip_thickness_mm": None,
            "tape_overlap_pct": None,
            "tape_thickness_mm": None,
        })

    return {
        "item_no": item_no,
        "designation": designation,
        "config": config,
        "num_cores": num_cores,
        "conductor_area_mm2": phase_area or 0.0,
        "conductor_material": conductor_material,
        "conductor_shape": conductor_shape,
        "conductor_class": 2,
        "fine_wire": False,
        "voltage_kv": voltage_kv,
        "standard": standard,
        "cable_type": "lt",
        "dc_resistance_ohm_per_km": phase_rdc,
        "neutral_dc_resistance_ohm_per_km": neutral_rdc,
        "neutral_area_mm2": neutral_area,
        "conductor_od_mm": None,
        "num_wires": phase_wires,
        "num_wires_neutral": neutral_wires,
        "wire_dia_mm": None,
        "layers": layers,
        "overall_od_mm": overall_od,
        "overall_od_tolerance_mm": 4.0,
        "current_rating_A": None,
        "delivery_length_m": delivery_length,
        "drum_type": drum_type,
        "n_pairs": 1,
        "_raw_section_text": pair_text,
        "_col_index": col,
    }


def _parse_company_gtp(pdf_path: str, raw_text: str) -> dict:
    """
    Parse a company-format GTP (IS 17505 style).
    Pages are grouped in pairs; each pair contains two cables side by side.
    """
    import os

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            pages.append(t)

    cables = []
    item_no = 1
    for i in range(0, len(pages), 2):
        page1 = pages[i]
        page2 = pages[i + 1] if i + 1 < len(pages) else ""
        pair_text = page1 + "\n" + page2

        # Skip pages with no PROJECT line (e.g. cover sheets)
        if "PROJECT :" not in pair_text:
            continue

        for col in range(2):
            cable = _parse_company_cable(pair_text, col, item_no)
            if cable and cable.get("dc_resistance_ohm_per_km"):
                cables.append(cable)
                item_no += 1

    first_page = pages[0] if pages else ""
    customer_m = re.search(r"CUSTOMER\s*:\s*(.+)", first_page)
    project_m = re.search(r"PROJECT\s*:\s*(.+)", first_page)

    return {
        "gtp_ref": os.path.splitext(os.path.basename(pdf_path))[0],
        "customer": customer_m.group(1).strip() if customer_m else None,
        "project": project_m.group(1).strip() if project_m else None,
        "date": None,
        "gtp_type": None,
        "cables": cables,
        "_parser": "direct_company",
    }


def _parse_company_single_gtp(pdf_path: str, raw_text: str) -> dict:
    """
    Parse a company/IS-7098 format GTP where each page-pair holds ONE cable
    (not two side by side). Reuses _parse_company_cable's field-extraction
    logic entirely — it already handles single-value data rows correctly —
    just always with col=0, since there's no second column to read.
    """
    import os

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            pages.append(t)

    cables = []
    item_no = 1
    for i in range(0, len(pages), 2):
        page1 = pages[i]
        page2 = pages[i + 1] if i + 1 < len(pages) else ""
        pair_text = page1 + "\n" + page2

        if "PROJECT :" not in pair_text:
            continue

        cable = _parse_company_cable(pair_text, 0, item_no)
        if cable and cable.get("dc_resistance_ohm_per_km"):
            cables.append(cable)
            item_no += 1

    first_page = pages[0] if pages else ""
    customer_m = re.search(r"CUSTOMER\s*:\s*(.+)", first_page)

    return {
        "gtp_ref": os.path.splitext(os.path.basename(pdf_path))[0],
        "customer": customer_m.group(1).strip() if customer_m else None,
        "project": None,
        "date": None,
        "gtp_type": None,
        "cables": cables,
        "_parser": "direct_company_single",
    }


# ─────────────────────────────────────────────────────────────────────────────
# DATASHEET FORMAT  (single cable per document, Sr No. numbered rows)
# e.g. "TECHNICAL PARTICULARS FOR FIRE SURVIVAL CABLE"
#      "Data Sheet no : 211125 A Rev 0"
# ─────────────────────────────────────────────────────────────────────────────

# Resistivity constants — kept for fallback only
_RESISTIVITY = {"copper": 1 / 58.0, "aluminium": 1 / 35.0}

_is8130_resistance: dict = {}

def _get_is8130_resistance(material: str, area_mm2: float, conductor_class: int = 2) -> Optional[float]:
    """Return IS 8130 / BS EN 60228 max dc resistance (Ω/km) for given area and conductor class."""
    global _is8130_resistance
    if not _is8130_resistance:
        _path = os.path.join(os.path.dirname(__file__), "..", "data", "is8130_conductor_resistance.json")
        with open(_path) as f:
            _is8130_resistance = json.load(f)
    # Build table key from material + class; default to class2
    cls_str = str(conductor_class).lower().replace(" ", "").replace("-", "")
    if "class" not in cls_str:
        cls_str = f"class{cls_str}"
    table_key = f"{material}_{cls_str}"
    if table_key not in _is8130_resistance:
        table_key = f"{material}_class2"          # fallback
    table = _is8130_resistance.get(table_key, {})
    # Try exact match first, then nearest area key
    area_str = str(int(area_mm2)) if area_mm2 == int(area_mm2) else str(area_mm2)
    if area_str in table:
        return float(table[area_str])
    # Find closest key
    keys = [(abs(float(k) - area_mm2), float(table[k])) for k in table if not k.startswith("_")]
    if keys:
        return min(keys, key=lambda x: x[0])[1]
    return None


def _parse_datasheet_gtp(pdf_path: str, raw_text: str) -> dict:
    """
    Parse a single-cable technical data sheet.
    Extracts conductor, glass mica tape, insulation, inner sheath, armour,
    outer sheath from numbered rows.
    """
    text = raw_text

    # ── GTP ref ──────────────────────────────────────────────────────────────
    gtp_ref = None
    m = re.search(r"Data Sheet no\s*:\s*([A-Za-z0-9/ _-]+?)(?:\s+(?:Rev|Dated|$))", text, re.I)
    if m:
        gtp_ref = m.group(1).strip()

    # ── Customer / project ───────────────────────────────────────────────────
    customer, project = None, None
    cm = re.search(r"Customer\s*:\s*(.+?)(?:Project|$)", text, re.I)
    if cm:
        customer = cm.group(1).strip().rstrip(",")
    pm = re.search(r"Project\s*:\s*(.+)", text, re.I)
    if pm:
        project = pm.group(1).strip()

    # ── Description → config, num_cores, voltage ─────────────────────────────
    config, num_cores_val, voltage_kv = None, 1, None
    dm = re.search(r"Description\s+(\d+\.?\d*C?\s*[Xx×]\s*[\d.]+\s*SQMM\S*)", text, re.I)
    if dm:
        config = dm.group(1).strip()
        nc_m = re.search(r"([\d.]+)\s*C", config, re.I)
        if nc_m:
            num_cores_val = float(nc_m.group(1))
    vm = re.search(r"Rated Voltage\s+Volts?\s+([\d,/]+)", text, re.I)
    if vm:
        raw_v = vm.group(1).replace(",", "").split("/")[0]
        try:
            voltage_kv = float(raw_v) / 1000
        except ValueError:
            pass

    # ── Conductor ─────────────────────────────────────────────────────────────
    area_mm2 = None
    am = re.search(r"Conductor size\s+mm2?\s+([\d.]+)", text, re.I)
    if am:
        area_mm2 = float(am.group(1))

    conductor_material = "copper"
    if re.search(r"\bAluminium\b|\bAl\b|\bAluminum\b", text, re.I):
        conductor_material = "aluminium"
    if re.search(r"\bCopper\b", text, re.I):
        conductor_material = "copper"

    num_wires = None
    nwm = re.search(r"No\.?\s*Of?\s*Strands?\s+No\.?\s+([\d]+)", text, re.I)
    if nwm:
        num_wires = int(nwm.group(1))

    wire_dia = None
    wdm = re.search(r"Dia\s+of\s+each\s+strand\s+(?:Before\s+Stranding\s+)?mm\s+([\d.]+)", text, re.I)
    if wdm:
        wire_dia = float(wdm.group(1))

    conductor_shape = "round"
    if re.search(r"sector|shaped", text, re.I):
        conductor_shape = "sector"
    elif re.search(r"compact", text, re.I):
        conductor_shape = "compacted"

    # Parse conductor class (e.g. "Class-2", "Class 5", "Class-6")
    conductor_class = 2  # default
    cm = re.search(r"[Cc]lass[-\s]*(\d)", text)
    if cm:
        conductor_class = int(cm.group(1))

    # Try to read dc_resistance directly from the GTP (row 14 or any labelled DC resistance row)
    # Pattern: look for the value that follows the unit "ohm/km" or "Ω/km" on the same row
    dc_resistance = None
    dc_m = re.search(
        r"DC\s*Resistance[^\n]*?(?:ohm|Ω|omega)[^\n]*?/\s*km[^\d]*([\d.]+)",
        text, re.I
    )
    if not dc_m:
        # Row number anywhere (2, 3, 13, 14, etc.) followed by DC Resistance on same line
        dc_m = re.search(
            r"(?:^|\b)\d{1,2}\b[^\n]*DC\s*Res[^\n]*([\d]+\.[\d]+)",
            text, re.I | re.M
        )
    if not dc_m:
        # "Max. DC Resistance" or "Conductor DC Resistance" with value at end of line
        dc_m = re.search(
            r"(?:Max\.?\s+DC\s+Resistance|Conductor\s+DC\s+Resistance)[^\n]*([\d]+\.[\d]+)\s*$",
            text, re.I | re.M
        )
    if dc_m:
        try:
            dc_resistance = float(dc_m.group(1))
        except ValueError:
            pass

    # Fallback: IS 8130 / BS EN 60228 standard max by area + class
    if dc_resistance is None and area_mm2:
        dc_resistance = _get_is8130_resistance(conductor_material, area_mm2, conductor_class)

    # ── Layers ────────────────────────────────────────────────────────────────
    layers = []

    # Glass mica tape
    if re.search(r"Glass\s+Mica\s+Tape|Fire\s+Barrier", text, re.I):
        n_tapes = 2 if re.search(r"[Dd]ouble\s+[Ll]ayer", text) else 1
        layers.append({
            "layer_name":    "Glass Mica Fire Barrier Tape",
            "material_key":  "glass_mica_tape",
            "n_tapes":       n_tapes,
            "nominal_thickness_mm": None,
        })

    # Insulation
    ins_mat = "xlpe_insulation"
    if re.search(r"\bPVC\b", text, re.I) and not re.search(r"\bXLPE\b", text, re.I):
        ins_mat = "pvc_insulation"
    ins_t = None
    itm = re.search(r"Thickness\s*\(\s*Nominal\s*\)\s+mm\s+([\d.]+)", text, re.I)
    if itm:
        ins_t = float(itm.group(1))
    if ins_t:
        layers.append({
            "layer_name":           "XLPE Insulation" if ins_mat == "xlpe_insulation" else "PVC Insulation",
            "material_key":         ins_mat,
            "nominal_thickness_mm": ins_t,
            "thickness_type":       "Nominal",
        })

    # Inner sheath
    is_mat = "lszh_inner_sheath"
    if re.search(r"LSZH|LSOH|LS0H", text, re.I):
        is_mat = "lszh_inner_sheath"
    elif re.search(r"PVC", text, re.I):
        is_mat = "pvc_inner_sheath"
    is_t = None
    # "Thickness (Min.)" for inner sheath
    ism = re.search(
        r"(?:Inner\s+Sheath|Filler).*?Thickness\s*\(Min\.?\)\s*([\d.]+)",
        text, re.I | re.DOTALL
    )
    if not ism:
        # Grab first "Thickness (Min.)" occurrence
        ism = re.search(r"Thickness\s*\(Min\.?\)\s+([\d.]+)", text, re.I)
    if ism:
        is_t = round(float(ism.group(1)) + 0.2, 4)
    if is_t:
        layers.append({
            "layer_name":           "Inner Sheath",
            "material_key":         is_mat,
            "nominal_thickness_mm": is_t,
            "thickness_type":       "Minimum",
        })

    # Armour
    armour_type = "gs_round_wire_armour"
    if re.search(r"[Ff]lat\s+[Ss]trip|strip", text, re.I):
        armour_type = "gs_flat_strip_armour"
    wire_size = None
    wsm = re.search(r"Size\s+(?:of\s+armour\s+)?(?:mm\s+)?([\d.]+)\s*[±±]", text, re.I)
    if not wsm:
        wsm = re.search(r"(?:Armouring|Armour).*?Size.*?([\d.]+)\s*[±±]", text, re.I | re.DOTALL)
    if not wsm:
        wsm = re.search(r"(\d+\.\d+)\s*[±±]\s*0\.\d+", text)
    if wsm:
        wire_size = float(wsm.group(1))
    if wire_size:
        key = "wire_diameter_mm" if armour_type == "gs_round_wire_armour" else "armour_strip_thickness_mm"
        layers.append({
            "layer_name":    "GS Round Wire Armour" if armour_type == "gs_round_wire_armour" else "GS Flat Strip Armour",
            "material_key":  armour_type,
            key:             wire_size,
            "gap_mm":        0.5,
        })

    # Outer sheath
    os_mat = "lszh_outer_sheath"
    # Find "Outer Sheath" section and get Min thickness
    osm = re.search(
        r"Outer\s+Sheath.*?Thickness.*?\(Min\.?\).*?mm\s+([\d.]+)",
        text, re.I | re.DOTALL
    )
    if not osm:
        # Last "Thickness (Min.)" occurrence
        all_min = list(re.finditer(r"Thickness\s*\(Min\.?\)\s+([\d.]+)", text, re.I))
        if len(all_min) >= 2:
            osm = all_min[-1]
    os_t = round(float(osm.group(1)) + 0.2, 4) if osm else None
    if os_t:
        layers.append({
            "layer_name":           "LSZH Outer Sheath",
            "material_key":         os_mat,
            "nominal_thickness_mm": os_t,
            "thickness_type":       "Minimum",
        })

    cable = {
        "item_no":                  1,
        "designation":              config or "UNKNOWN",
        "config":                   config or "UNKNOWN",
        "conductor_material":       conductor_material,
        "conductor_shape":          conductor_shape,
        "conductor_area_mm2":       area_mm2,
        "num_cores":                num_cores_val,
        "num_wires":                num_wires,
        "wire_diameter_before_stranding_mm": wire_dia,
        "voltage_kv":               voltage_kv,
        "dc_resistance_ohm_per_km": dc_resistance,
        "conductor_class":          conductor_class,
        "layers":                   layers,
    }

    return {
        "gtp_ref":  gtp_ref,
        "customer": customer,
        "project":  project,
        "date":     None,
        "gtp_type": None,
        "cables":   [cable] if dc_resistance else [],
        "_parser":  "direct_datasheet",
    }


# ─────────────────────────────────────────────────────────────────────────────
# WIRE DATA SHEET FORMAT (Rallison multi-column, IS 694 style)
# Multiple cable sizes arranged in side-by-side columns:
#   Sr No. | Description | Unit | 1C x 1 SQMM | 1C x 1.5 SQMM | ...
# ─────────────────────────────────────────────────────────────────────────────

def _parse_wire_datasheet_gtp(pdf_path: str, raw_text: str) -> dict:
    """
    Parse a Rallison multi-column wire data sheet (IS 694 FR-LSH wires).
    Returns one cable dict per size column.
    """
    import os
    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    gtp_ref = re.sub(r'\s+', '-', basename.strip())

    # Count columns from SQMM occurrences in header — fall back to mm2/mm² if the
    # sheet uses that unit instead
    n_cols = len(re.findall(r'\bSQMM\b', raw_text[:600], re.I))
    if n_cols == 0:
        n_cols = len(re.findall(r'mm2|mm²', raw_text[:600], re.I))
    if n_cols == 0:
        return {"gtp_ref": gtp_ref, "customer": None, "project": None,
                "date": None, "gtp_type": None, "cables": [], "_parser": "direct_wire_datasheet"}

    # Extract cable size configs from the header line (e.g. "1C x 1", "1 C x 1.5")
    lines = raw_text[:600].split('\n')
    header_configs = []
    for line in lines:
        matches = re.findall(r'\d+(?:\.\d+)?\s*C\s+[Xx]\s+[\d.]+', line)
        if matches:
            header_configs = matches
            break

    def last_n_floats(line: str, n: int) -> list:
        """Return the last n float-valued tokens from a line."""
        nums = re.findall(r'\b\d+(?:\.\d+)?\b', line)
        floats = [float(x) for x in nums]
        return floats[-n:] if len(floats) >= n else []

    def find_row_vals(keyword_pattern: str) -> list:
        """Find first line matching keyword and return last n_cols floats."""
        for line in raw_text.split('\n'):
            if re.search(keyword_pattern, line, re.I):
                vals = last_n_floats(line, n_cols)
                if len(vals) == n_cols:
                    return vals
        return []

    # Conductor areas (row 4: "4 Conductor size mm2 1 1.5 2.5 4")
    areas = find_row_vals(r'Conductor\s+size\s+mm')

    # DC resistance — first Ohm/Km line (DC, not AC)
    rdc_vals = find_row_vals(r'Ohm/Km')

    # Insulation nominal thickness (row 8c: "cThickness (Nominal) as per IS mm 0.6 0.6 0.7 0.8")
    ins_t_vals = find_row_vals(r'Thickness.*Nominal')

    # Overall OD max (row 9: "9 Overall dimeter of cable (Maximum) mm 3.0 3.4 4.1 4.8")
    od_vals = find_row_vals(r'Overall.*di[am]')

    # Voltage
    voltage_kv = "1.1"
    vm = re.search(r'Rated\s+Voltage\s+Volts?\s+([\d]+)', raw_text, re.I)
    if vm:
        try:
            voltage_kv = str(float(vm.group(1)) / 1000)
        except ValueError:
            pass

    # Conductor material
    conductor_material = "copper"
    if re.search(r'\bAluminium\b|\bAluminum\b', raw_text, re.I) and not re.search(r'\bCopper\b', raw_text, re.I):
        conductor_material = "aluminium"

    # Conductor class (default 5 for IS 694 bunched circular wires)
    conductor_class = 5
    cm = re.search(r'[Cc]lass\s*[-–]?\s*(\d)', raw_text)
    if cm:
        conductor_class = int(cm.group(1))

    # Insulation material
    ins_mat_key = "pvc_insulation"
    ins_name = "PVC Insulation"
    if re.search(r'\bXLPE\b', raw_text, re.I) and not re.search(r'\bPVC\b', raw_text, re.I):
        ins_mat_key = "xlpe_insulation"
        ins_name = "XLPE Insulation"

    cables = []
    for i in range(n_cols):
        area = areas[i] if i < len(areas) else None
        rdc  = rdc_vals[i] if i < len(rdc_vals) else None
        ins_t = ins_t_vals[i] if i < len(ins_t_vals) else None
        od   = od_vals[i] if i < len(od_vals) else None

        # Build config string from header or from area fallback — always normalised
        # to "mm²" regardless of whether the source sheet used SQMM or mm2/mm²
        if i < len(header_configs):
            raw_cfg = re.sub(r'\s+', ' ', header_configs[i].strip())
            raw_cfg = re.sub(r'(\d)\s+C\s+[Xx]\s+', r'\1C x ', raw_cfg)
            config = f"{raw_cfg}mm²"
        else:
            config = f"1C x {area}mm²" if area else "1C x ?mm²"

        nc_m = re.match(r'(\d+(?:\.\d+)?)C', config, re.I)
        num_cores = float(nc_m.group(1)) if nc_m else 1.0

        layers = []
        if ins_t:
            layers.append({
                "layer_name":           ins_name,
                "material_key":         ins_mat_key,
                "nominal_thickness_mm": ins_t,
                "thickness_type":       "Nominal",
            })

        cables.append({
            "item_no":                  i + 1,
            "designation":              "FR-LSH",
            "config":                   config,
            "num_cores":                num_cores,
            "conductor_area_mm2":       area,
            "conductor_material":       conductor_material,
            "conductor_shape":          "round",
            "conductor_class":          conductor_class,
            "voltage_kv":               voltage_kv,
            "dc_resistance_ohm_per_km": rdc,
            "overall_od_mm":            od,
            "layers":                   layers,
            "delivery_length_m":        100,
            "drum_type":                "roll",
        })

    return {
        "gtp_ref":  gtp_ref,
        "customer": None,
        "project":  None,
        "date":     None,
        "gtp_type": None,
        "cables":   cables,
        "_parser":  "direct_wire_datasheet",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SR-NO DATASHEET FORMAT (third-party lab GTPs, e.g. LKB Engineering)
# Single-column, plain-numbered rows (1..14) with roman-numeral sub-items
# (i, ii, iii...). One PDF may stack several cable variants of the same
# construction back to back (e.g. the same size repeated once per conductor
# class) — each repetition is split out as its own "TECHNICAL PARTICULARS FOR"
# block and returned as a separate cable.
#   Sr No. Description Unit 2C X 1.5 mm2 (YWY HR-FR )
#   1 Name of Manufacturer ...
#   6 Conductor
#     i Material Annealed Plain Copper Class-2 ...
# ─────────────────────────────────────────────────────────────────────────────

def _split_srno_blocks(raw_text: str) -> list:
    """Split into one block per cable variant, keyed on the repeating
    'TECHNICAL PARTICULARS FOR ...' banner that starts each block."""
    starts = [m.start() for m in re.finditer(r'TECHNICAL PARTICULARS FOR', raw_text, re.I)]
    if not starts:
        return [raw_text]
    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(raw_text)
        blocks.append(raw_text[start:end])
    return blocks


def _split_srno_numbered_sections(block: str) -> dict:
    """
    Split a block into major sections keyed by their leading plain number
    (e.g. '6' for Conductor, '9' for Armour). Only matches "N " at the start
    of a line (whitespace right after the digits) so sub-items like '10.1'
    (dot right after the digits, no space) are correctly excluded and stay
    part of the parent section's text.
    """
    starts = []
    for m in re.finditer(r'^(\d{1,2})\s+\S', block, re.MULTILINE):
        starts.append((m.start(), m.group(1)))
    if not starts:
        return {}
    sections = {}
    for i, (start, sec_id) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(block)
        if sec_id not in sections:
            # Keep the first occurrence — some sheets accidentally reuse a
            # section number further down (e.g. a duplicate "9 Packing
            # Detail" after "9 Sheath"), which would otherwise silently
            # overwrite the real section's content.
            sections[sec_id] = block[start:end]
    return sections


def _find_srno_section(sections: dict, heading_pattern: str, exclude_pattern: Optional[str] = None) -> str:
    """
    Find a section by its heading text rather than a fixed section number —
    section numbering shifts between cable sub-families (armoured vs
    screened vs bare wire), so content-based lookup is more robust than
    sections.get("9") style fixed lookups.
    """
    for text in sections.values():
        first_line = text.split("\n", 1)[0]
        if re.search(heading_pattern, first_line, re.I):
            if exclude_pattern and re.search(exclude_pattern, first_line, re.I):
                continue
            return text
    return ""


def _srno_thickness_nominal_or_minimum(text: str) -> tuple:
    """
    Returns (thickness_mm, thickness_type), preferring Nominal over Minimum
    per the same convention used elsewhere in this file. Tolerant of:
      - full words ("Nominal"/"Minimum") and abbreviations ("Nom"/"Min")
      - "Thickness (Nominal)" and "Thickness of Sheath (Nominal)" label styles
      - an inline "#" marker some sheets add after the unit tag
    Minimum-only values get the standard +0.2mm manufacturing tolerance.
    """
    nom_m = re.search(r'Thickness(?:\s+of\s+\w+)?\s*\(Nom(?:inal)?\)\s*#?\s*mm\s+([\d.]+)', text, re.I)
    if nom_m:
        return float(nom_m.group(1)), "Nominal"
    min_m = re.search(r'Thickness(?:\s+of\s+\w+)?\s*\(Min(?:imum)?\)\s*#?\s*mm\s+([\d.]+)', text, re.I)
    if min_m:
        return round(float(min_m.group(1)) + 0.2, 4), "Minimum"
    return None, None


def _parse_srno_cable(block: str, item_no: int) -> Optional[dict]:
    """Parse one cable variant block into a cable dict."""
    # ── GTP ref (per block — each repeated variant carries its own line) ────────
    ref_m = re.search(r'GTP Ref No\s*:?\s*([^\n]+?)\s*Dt\b', block, re.I)
    if not ref_m:
        ref_m = re.search(r'GTP Ref No\s*:?\s*(\S+)', block, re.I)
    gtp_ref = re.sub(r'\s+', '-', ref_m.group(1).strip()) if ref_m else None

    # ── Config / num_cores / area from the "No of Cores X Size" row ─────────────
    cores_m = re.search(r'No of Cores X Size\s+mm2\s+(\d+(?:\.\d+)?)\s*C\s*[Xx]\s*(\d+(?:\.\d+)?)', block, re.I)
    if not cores_m:
        return None
    num_cores = float(cores_m.group(1))
    area = float(cores_m.group(2))
    config = f"{cores_m.group(1)}C x {cores_m.group(2)}mm²"

    desig_m = re.search(r'Sr\s*No\.?\s+Description\s+Unit[^\n(]*\(([^)]+)\)', block, re.I)
    designation = re.sub(r'\s+', '-', desig_m.group(1).strip()) if desig_m else config

    sections = _split_srno_numbered_sections(block)

    # ── Conductor (section 6) ────────────────────────────────────────────────
    cond = sections.get("6", "")
    conductor_material = "aluminium" if (
        re.search(r'\bAluminium\b|\bAluminum\b', cond, re.I) and not re.search(r'\bCopper\b', cond, re.I)
    ) else "copper"

    class_m = re.search(r'Class[-\s]*(\d)', cond, re.I)
    conductor_class = int(class_m.group(1)) if class_m else 2
    fine_wire = conductor_class in (5, 6)

    conductor_shape = "round"
    if re.search(r'\bsector\b', cond, re.I):
        conductor_shape = "sector"
    elif re.search(r'\bcompact', cond, re.I) and not re.search(r'non[\s-]*compact', cond, re.I):
        conductor_shape = "compacted"

    rdc_m = re.search(r'Max\.?\s*D\.?C\.?\s*Conductor\s*Resistance[^\n]*?km\s+([\d.]+)', cond, re.I)
    dc_resistance = float(rdc_m.group(1)) if rdc_m else None
    if dc_resistance is None:
        return None

    # ── Insulation (section 7) ───────────────────────────────────────────────
    ins = sections.get("7", "")
    ins_mat = "pvc_insulation"
    ins_name = "PVC Insulation"
    if re.search(r'\bXLPE\b', ins, re.I) and not re.search(r'\bPVC\b', ins, re.I):
        ins_mat, ins_name = "xlpe_insulation", "XLPE Insulation"
    elif re.search(r'\bEPDM\b|\bEPR\b|\brubber\b', ins, re.I):
        ins_mat, ins_name = "rubber_insulation", "EPDM/Rubber Insulation"
    ins_t_m = re.search(r'Thickness of insulation\s*\(Nominal\)\s+([\d.]+)', ins, re.I)
    ins_t = float(ins_t_m.group(1)) if ins_t_m else None

    layers = []
    if ins_t:
        layers.append({
            "layer_name": ins_name, "material_key": ins_mat,
            "nominal_thickness_mm": ins_t, "thickness_type": "Nominal",
            "od_mm": None, "armour_strip_width_mm": None,
            "armour_strip_thickness_mm": None, "tape_overlap_pct": None,
            "tape_thickness_mm": None,
        })

    # ── Inner sheath — armoured constructions only ("Inner Sheath" heading) ──
    inner = _find_srno_section(sections, r'Inner\s+Sheath')
    inner_t, inner_t_type = _srno_thickness_nominal_or_minimum(inner)
    if inner_t:
        inner_mat = "lszh_inner_sheath" if re.search(r'LSZH|LSOH|LS0H', inner, re.I) else "pvc_inner_sheath"
        layers.append({
            "layer_name": "Inner Sheath", "material_key": inner_mat,
            "nominal_thickness_mm": inner_t, "thickness_type": inner_t_type,
            "od_mm": None, "armour_strip_width_mm": None,
            "armour_strip_thickness_mm": None, "tape_overlap_pct": None,
            "tape_thickness_mm": None,
        })

    # ── Armour — found by heading, not a fixed section number ────────────────
    armour = _find_srno_section(sections, r'Armour')
    if armour:
        if re.search(r'\bstrip\b|\bflat\b', armour, re.I):
            is_round_wire = False
        elif re.search(r'\bwire\b', armour, re.I):
            # "Round Wire", "Steel Wire Armoured", "GS Wire" etc. all mean round wire
            # unless explicitly a flat strip — most BS/IS armoured cables at these
            # sizes use round wire, and the word "round" isn't always spelled out.
            is_round_wire = True
        else:
            is_round_wire = False

        if is_round_wire:
            dia_m = re.search(r'Thickness\s+mm\s+([\d.]+)', armour, re.I)
            if dia_m:
                layers.append({
                    "layer_name": "GS Round Wire Armour", "material_key": "gs_round_wire_armour",
                    "wire_diameter_mm": float(dia_m.group(1)), "gap_mm": 0.5, "od_mm": None,
                })
        else:
            strip_m = re.search(r'(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)', armour)
            thick_m = re.search(r'Thickness\s+mm\s+([\d.]+)', armour, re.I)
            if strip_m:
                layers.append({
                    "layer_name": "GS Flat Strip Armour", "material_key": "gs_flat_strip_armour",
                    "nominal_thickness_mm": float(strip_m.group(2)), "thickness_type": "Nominal",
                    "armour_strip_width_mm": float(strip_m.group(1)),
                    "armour_strip_thickness_mm": float(strip_m.group(2)), "od_mm": None,
                })
            elif thick_m:
                layers.append({
                    "layer_name": "GS Flat Strip Armour", "material_key": "gs_flat_strip_armour",
                    "nominal_thickness_mm": float(thick_m.group(1)), "thickness_type": "Nominal",
                    "armour_strip_width_mm": None,
                    "armour_strip_thickness_mm": float(thick_m.group(1)), "od_mm": None,
                })

    # ── Outer/only sheath — "Outer Sheath" (armoured) or a standalone
    # "Sheath" section (unarmoured/screened cables have just one) ────────────
    outer = _find_srno_section(sections, r'Outer\s+Sheath')
    layer_label = "Outer Sheath"
    if not outer:
        outer = _find_srno_section(sections, r'\bSheath\b', exclude_pattern=r'Inner\s+Sheath')
        layer_label = "Sheath"
    overall_od = None
    outer_t, outer_t_type = _srno_thickness_nominal_or_minimum(outer)
    if outer_t:
        if re.search(r'FR-LSH|FRLSH|FR LSH', outer, re.I):
            outer_mat = "frlsh_sheath"
        elif re.search(r'LSZH|LSOH|LS0H', outer, re.I):
            outer_mat = "lszh_outer_sheath"
        else:
            outer_mat = "pvc_outer_sheath"
        layers.append({
            "layer_name": layer_label, "material_key": outer_mat,
            "nominal_thickness_mm": outer_t, "thickness_type": outer_t_type,
            "od_mm": None, "armour_strip_width_mm": None,
            "armour_strip_thickness_mm": None, "tape_overlap_pct": None,
            "tape_thickness_mm": None,
        })
    od_m = re.search(r'Diameter[^\n]*?mm\s+([\d.]+)', outer, re.I)
    if od_m:
        overall_od = float(od_m.group(1))

    if len(layers) < 1:
        return None

    # ── Drum / delivery — found by heading ("Packing Detail") ────────────────
    drum = _find_srno_section(sections, r'Packing')
    delivery_m = 1000
    del_m = re.search(r'Standard Packing length\s+mtr\s+(\d+)', drum, re.I)
    if del_m:
        delivery_m = int(del_m.group(1))
    drum_type = "steel" if re.search(r'\bsteel\b', drum, re.I) else "wooden"

    # ── Voltage (section 3) — "1100" or phase/line pair "600/1000" ───────────
    volt_m = re.search(r'Voltage Grade\s+Volts\s+(\d+)(?:\s*/\s*(\d+))?', block, re.I)
    if volt_m:
        # For a phase/line pair, the line (second) voltage is the standard rating
        volts = float(volt_m.group(2) or volt_m.group(1))
        voltage_kv = round(volts / 1000, 3)
    else:
        voltage_kv = 1.1

    return {
        "item_no": item_no,
        "gtp_ref": gtp_ref,
        "designation": designation,
        "config": config,
        "num_cores": num_cores,
        "conductor_area_mm2": area,
        "conductor_material": conductor_material,
        "conductor_shape": conductor_shape,
        "conductor_class": conductor_class,
        "fine_wire": fine_wire,
        "num_wires": 7,
        "wire_dia_mm": None,
        "voltage_kv": voltage_kv,
        "standard": "IS 1554-1",
        "cable_type": "lt",
        "dc_resistance_ohm_per_km": dc_resistance,
        "neutral_dc_resistance_ohm_per_km": None,
        "layers": layers,
        "overall_od_mm": overall_od,
        "overall_od_tolerance_mm": None,
        "current_rating_A": None,
        "delivery_length_m": delivery_m,
        "drum_type": drum_type,
        "n_pairs": 1,
    }


def _parse_srno_gtp(pdf_path: str, raw_text: str) -> dict:
    """Parse a 'Sr No. Description Unit' lab-format GTP, possibly containing
    several stacked cable variants of the same construction."""
    header = raw_text[:400]
    customer_m = re.search(r'(?:Company Name|Customer)\s*:\s*([^\n]+)', header, re.I)
    project_m = re.search(r'Project\s*:\s*([^\n]+)', header, re.I)
    date_m = re.search(r'Date\s*:\s*([^\n]+)', header, re.I)

    blocks = _split_srno_blocks(raw_text)
    cables = []
    for i, block in enumerate(blocks, start=1):
        cable = _parse_srno_cable(block, i)
        if cable:
            cables.append(cable)

    gtp_ref = cables[0]["gtp_ref"] if cables and cables[0].get("gtp_ref") else None
    for c in cables:
        c.pop("gtp_ref", None)   # document-level field, not per-cable

    return {
        "gtp_ref": gtp_ref,
        "customer": customer_m.group(1).strip() if customer_m else None,
        "project": project_m.group(1).strip() if project_m else None,
        "date": date_m.group(1).strip() if date_m else None,
        "gtp_type": None,
        "cables": cables,
        "_parser": "direct_srno_datasheet",
    }
