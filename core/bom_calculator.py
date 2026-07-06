"""
BOM Calculator — layer-by-layer material quantity calculation.
Supports two modes: 'costing' (safer, higher estimates) and 'production' (accurate).
All formulas are driven by config data from Google Sheets (or local JSON fallback).
"""

import math
import json
import os
from typing import Literal, Optional

BOM_TYPE = Literal["costing", "production"]

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_densities: dict = {}
_lay_factors: dict = {}
_is10462_table: list = []
_conductor_config: dict = {}
_shattuc: dict = {}

def _load_data():
    global _densities, _lay_factors, _is10462_table, _conductor_config, _shattuc
    if not _densities:
        with open(os.path.join(DATA_DIR, "material_densities.json")) as f:
            _densities = json.load(f)
    if not _lay_factors:
        with open(os.path.join(DATA_DIR, "lay_factors.json")) as f:
            _lay_factors = json.load(f)
    if not _is10462_table:
        with open(os.path.join(DATA_DIR, "is10462_conductor_diameters.json")) as f:
            _is10462_table = json.load(f)["table"]
    if not _conductor_config:
        with open(os.path.join(DATA_DIR, "conductor_config.json")) as f:
            _conductor_config = json.load(f)
    if not _shattuc:
        with open(os.path.join(DATA_DIR, "shattuc_laidup_factors.json")) as f:
            _shattuc = json.load(f)


def get_shattuc_concentric_factor(num_cores: float) -> float:
    """Return Shattuc concentric M-factor for the given core count."""
    _load_data()
    # 3.5C → use 4-core factor (neutral counts as a full core for geometry)
    key = str(int(math.ceil(num_cores)))
    return float(_shattuc["concentric"].get(key, 1.0))


# ── Resistivity constants ────────────────────────────────────────────────────
RESISTIVITY = {
    "copper":    1 / 58,
    "aluminium": 1 / 35,
}


# ── GTP Type (A / B / C) factors ────────────────────────────────────────────

_GTP_TYPE_FACTORS = {
    "A": {"conductor_resistance_factor": 1.00, "armour_coverage": 0.9},
    "B": {"conductor_resistance_factor": 0.92, "armour_coverage": 0.8},
    "C": {"conductor_resistance_factor": 0.90, "armour_coverage": 0.8},
}

# IS 3975 round wire diameter: {nominal_mm: (cat_A_dia, cat_BC_dia)}
_IS3975_WIRE_DIA = {
    0.30: (0.300, 0.280),
    0.45: (0.450, 0.430),
    0.70: (0.700, 0.675),
    0.80: (0.760, 0.680),   # special case
    0.90: (0.900, 0.870),
    1.25: (1.250, 1.215),
    1.40: (1.400, 1.360),
    1.60: (1.600, 1.555),
    2.00: (2.000, 1.950),
    2.50: (2.500, 2.435),
    3.15: (3.150, 3.070),
    4.00: (4.000, 3.900),
}


def get_effective_wire_diameter(nominal_mm: float, gtp_type: str) -> float:
    """Return IS 3975 effective wire diameter for the given BOM category."""
    entry = _IS3975_WIRE_DIA.get(round(nominal_mm, 2))
    if entry is None:
        # Nearest size — find closest key
        closest = min(_IS3975_WIRE_DIA.keys(), key=lambda k: abs(k - nominal_mm))
        entry = _IS3975_WIRE_DIA[closest]
    return entry[0] if gtp_type == "A" else entry[1]


def get_effective_strip_thickness(nominal_mm: float, gtp_type: str) -> float:
    """IS 3975 formed wire: A=nominal, B&C=nominal×0.9."""
    return nominal_mm if gtp_type == "A" else round(nominal_mm * 0.9, 4)


def get_gtp_type_factors(gtp_type: str) -> dict:
    return _GTP_TYPE_FACTORS.get(gtp_type.upper(), _GTP_TYPE_FACTORS["A"])


def get_density(material_key: str, bom_type: BOM_TYPE, overrides: Optional[dict] = None) -> float:
    _load_data()
    if overrides and material_key in overrides:
        return overrides[material_key].get(bom_type, overrides[material_key].get("costing"))
    entry = _densities.get(material_key)
    if not entry:
        raise ValueError(f"Unknown material key: {material_key}")
    return entry[bom_type]


def get_conductor_lay_factor(num_wires: int, bom_type: BOM_TYPE, overrides: Optional[dict] = None) -> float:
    _load_data()
    if overrides and "conductor" in overrides:
        return overrides["conductor"].get(bom_type, 1.005)
    if num_wires is None:
        return _lay_factors["conductor"][-1][bom_type]
    for band in _lay_factors["conductor"]:
        if band["min_wires"] <= num_wires <= band["max_wires"]:
            return band[bom_type]
    return _lay_factors["conductor"][-1][bom_type]


def get_cabling_lay_factor(bom_type: BOM_TYPE, overrides: Optional[dict] = None) -> float:
    _load_data()
    if overrides and "cabling" in overrides:
        return overrides["cabling"].get(bom_type, 1.007)
    return _lay_factors["cabling"][bom_type]


def get_armour_lay_factor(bom_type: BOM_TYPE, overrides: Optional[dict] = None) -> float:
    _load_data()
    if overrides and "armour" in overrides:
        return overrides["armour"].get(bom_type, 1.007)
    return _lay_factors["armour"][bom_type]


def get_round_wire_armour_lay_factor(bom_type: BOM_TYPE, overrides: Optional[dict] = None) -> float:
    _load_data()
    if overrides and "round_wire_armour" in overrides:
        return overrides["round_wire_armour"].get(bom_type, 1.007)
    rwa = _lay_factors.get("round_wire_armour")
    return rwa[bom_type] if rwa else _lay_factors["armour"][bom_type]


def get_fine_wire_conductor_lay_factor(num_wires: int, bom_type: BOM_TYPE, overrides: Optional[dict] = None) -> float:
    _load_data()
    if overrides and "fine_wire_conductor" in overrides:
        return overrides["fine_wire_conductor"].get(bom_type, 1.030)
    table = _lay_factors.get("fine_wire_conductor", [])
    for band in table:
        if band["min_wires"] <= num_wires <= band["max_wires"]:
            return band[bom_type]
    return table[-1][bom_type] if table else 1.030


# ── Conductor OD calculation ─────────────────────────────────────────────────

def get_is10462_conductor_od(nominal_area_mm2: float) -> float:
    """
    Look up IS 10462 fictitious diameter for the nearest standard area,
    then add the configured safety margin (default 0.2 mm).
    Used only for compacted circular conductors.
    """
    _load_data()
    margin = _conductor_config.get("compacted_circular_safety_margin_mm", 0.2)
    table = _is10462_table
    # Find exact match first
    for row in table:
        if row["area_mm2"] == nominal_area_mm2:
            return round(row["fictitious_dia_mm"] + margin, 2)
    # Nearest standard size (round to closest entry)
    nearest = min(table, key=lambda r: abs(r["area_mm2"] - nominal_area_mm2))
    return round(nearest["fictitious_dia_mm"] + margin, 2)


def calc_sector_conductor_geometry(
    phase_effective_area_mm2: float,
    num_cores: float,                        # 2, 3, 4, or 3.5
    neutral_effective_area_mm2: float = 0.0,
) -> dict:
    """
    Calculate sector conductor radius and angles.

    All sectors share the same radius r so the laid-up bundle is circular.
    Phase angles are fixed for standard configs (2C=180°, 3C=120°, 4C=90°).
    For 3.5C the angles are solved analytically to minimise cable diameter:
        phase_angle × (3 + neutral_area/phase_area) = 360°

    Returns:
        r_mm          — sector radius = conductor height
        phase_angle   — degrees
        neutral_angle — degrees (0 for non-3.5C)
        conductor_od_mm — 2 × r (equivalent OD for insulation ID)
    """
    _load_data()
    compaction = _conductor_config.get("sector_compaction_factor", 0.93)

    # Geometric area needed to achieve the electrical area after compaction
    phase_geo_area = phase_effective_area_mm2 / compaction

    if num_cores == 3.5 and neutral_effective_area_mm2 > 0:
        neutral_geo_area = neutral_effective_area_mm2 / compaction
        # Solve: phase_angle × (3 + neutral_geo/phase_geo) = 360
        phase_angle = 360.0 / (3.0 + neutral_geo_area / phase_geo_area)
        neutral_angle = 360.0 - 3.0 * phase_angle
    else:
        fixed_angles = {2: 180.0, 3: 120.0, 4: 90.0}
        n = int(num_cores)
        phase_angle = fixed_angles.get(n, 360.0 / n)
        neutral_angle = 0.0

    # r² = phase_geo_area / ((phase_angle/360) × π)
    r_mm = math.sqrt(phase_geo_area / ((phase_angle / 360.0) * math.pi))

    return {
        "r_mm": round(r_mm, 3),
        "phase_angle_deg": round(phase_angle, 2),
        "neutral_angle_deg": round(neutral_angle, 2),
        "sector_r2_mm": round(2 * r_mm, 3),   # 2r — used as ID for first per-core layer (insulation)
        "compaction_factor": compaction,
    }


def get_laying_up_safety_factor(bom_type: BOM_TYPE) -> float:
    """Safety factor applied to laid-up cable OD (after cores are cabled together)."""
    _load_data()
    factors = _conductor_config.get("cable_od_safety_factor", {"production": 1.03, "costing": 1.05})
    return factors.get(bom_type, 1.03)


def calc_conductor_od(
    effective_area_mm2: float,
    conductor_shape: str,
    nominal_area_mm2: float = 0.0,
    num_wires: Optional[int] = None,
) -> float:
    """
    Always-calculated conductor OD. Never taken from GTP. No safety factor here —
    the safety factor is applied later at the laid-up cable OD stage.

    - compacted:            IS 10462 fictitious dia + 0.2 mm margin
    - sector:               2r (caller should use calc_sector_conductor_geometry)
    - round (N known):      √N × wire_dia × 1.13, wire_dia from effective area
    - round (N unknown):    1.13 × √area
    """
    _load_data()
    factor = _conductor_config.get("round_stranded_od_factor", 1.13)
    if conductor_shape == "compacted":
        area = nominal_area_mm2 if nominal_area_mm2 > 0 else effective_area_mm2
        return get_is10462_conductor_od(area)
    elif conductor_shape == "sector":
        compaction = _conductor_config.get("sector_compaction_factor", 0.93)
        r = math.sqrt((effective_area_mm2 / compaction) / ((120.0 / 360.0) * math.pi))
        return round(2 * r, 2)
    elif num_wires:
        # Derive wire dia from effective cross-section so OD is consistent with DC resistance
        wire_dia = math.sqrt(4 * effective_area_mm2 / (math.pi * num_wires))
        return round(factor * math.sqrt(num_wires) * wire_dia, 3)
    else:
        return round(factor * math.sqrt(effective_area_mm2), 2)


# ── Extrusion thickness tolerance ────────────────────────────────────────────

# Default factors — will be overridden by Google Sheet values when available
_DEFAULT_EXTRUSION_TOLERANCES = [
    # (thickness_type, band,     min_mm, max_mm, costing_factor, production_factor)
    ("Nominal", "thin",   0.0,  1.0,  1.10, 1.00),
    ("Nominal", "medium", 1.0,  2.0,  1.08, 1.00),
    ("Nominal", "thick",  2.0, 99.0,  1.05, 1.00),
    ("Minimum", "thin",   0.0,  1.0,  1.15, 1.05),
    ("Minimum", "medium", 1.0,  2.0,  1.12, 1.05),
    ("Minimum", "thick",  2.0, 99.0,  1.08, 1.05),
]

def get_thickness_tolerance_factor(
    nominal_thickness_mm: float,
    thickness_type: str,   # "Nominal" or "Minimum"
    bom_type: BOM_TYPE,
    tolerance_table: Optional[list] = None
) -> float:
    table = _DEFAULT_EXTRUSION_TOLERANCES if tolerance_table is None else tolerance_table
    for row in table:
        t_type, _band, min_mm, max_mm, costing_f, prod_f = row
        if t_type == thickness_type and min_mm <= nominal_thickness_mm < max_mm:
            return costing_f if bom_type == "costing" else prod_f
    return 1.0


# ── Core formulas ────────────────────────────────────────────────────────────

def calc_conductor_weight(
    dc_resistance_ohm_per_km: float,
    conductor_material: str,      # "copper" or "aluminium"
    num_wires: int,
    bom_type: BOM_TYPE,
    conductor_resistance_factor: float = 1.0,   # GTP type A/B/C multiplier
    density_overrides: Optional[dict] = None,
    lay_factor_overrides: Optional[dict] = None,
) -> dict:
    """
    Returns weight kg/km and effective cross-section area mm².
    Formula: area = (ρ / R_DC) × 1000 × resistance_factor
             weight = area × density × lay_factor
    """
    rho = RESISTIVITY[conductor_material]
    r_dc_per_m = dc_resistance_ohm_per_km / 1000.0
    area_mm2 = (rho / r_dc_per_m) * conductor_resistance_factor

    density = get_density(
        "copper_conductor" if conductor_material == "copper" else "aluminium_conductor",
        bom_type, density_overrides
    )
    lay_factor = get_conductor_lay_factor(num_wires, bom_type, lay_factor_overrides)
    weight_kg_per_km = area_mm2 * density * lay_factor

    wire_dia_mm = None
    if num_wires:
        import math as _math
        wire_dia_mm = round(_math.sqrt(4 * area_mm2 / (_math.pi * num_wires)), 4)

    return {
        "material": conductor_material + "_conductor",
        "effective_area_mm2": round(area_mm2, 4),
        "num_wires": num_wires,
        "wire_dia_mm": wire_dia_mm,
        "lay_factor": lay_factor,
        "density_g_cm3": density,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_sector_insulation_weight(
    sector_r_mm: float,
    sector_angle_rad: float,
    nominal_thickness_mm: float,
    thickness_type: str,
    material_key: str,
    bom_type: BOM_TYPE,
    n_cores: int = 1,
    apply_cabling_lay: bool = True,
    density_overrides: Optional[dict] = None,
    lay_factor_overrides: Optional[dict] = None,
    tolerance_table: Optional[list] = None,
) -> dict:
    """
    Insulation over a sector conductor.

    Area = (θ/2) × (r + 2t_eff)² − (θ/2) × r²
    Costing adds 7.5% excess over calculated weight.
    """
    tol_factor   = get_thickness_tolerance_factor(nominal_thickness_mm, thickness_type, bom_type, tolerance_table)
    t_eff        = nominal_thickness_mm * tol_factor
    r_outer      = sector_r_mm + 2 * t_eff

    area_outer   = 0.5 * sector_angle_rad * r_outer ** 2
    area_inner   = 0.5 * sector_angle_rad * sector_r_mm ** 2
    ins_area_mm2 = area_outer - area_inner

    density      = get_density(material_key, bom_type, density_overrides)
    lay_factor   = get_cabling_lay_factor(bom_type, lay_factor_overrides) if apply_cabling_lay else 1.0
    excess       = 1.075 if bom_type == "costing" else 1.0

    weight_kg_per_km = ins_area_mm2 * density * lay_factor * excess * n_cores

    return {
        "material":              material_key,
        "sector_r_mm":           sector_r_mm,
        "sector_angle_deg":      round(math.degrees(sector_angle_rad), 3),
        "t_eff_mm":              round(t_eff, 4),
        "r_outer_mm":            round(r_outer, 4),
        "ins_area_mm2":          round(ins_area_mm2, 4),
        "tolerance_factor":      tol_factor,
        "excess_factor":         excess,
        "density_g_cm3":         density,
        "lay_factor":            lay_factor,
        "n_cores":               n_cores,
        "weight_kg_per_km":      round(weight_kg_per_km, 3),
    }


def calc_annular_layer_weight(
    id_mm: float,                  # inner diameter
    nominal_thickness_mm: float,
    thickness_type: str,           # "Nominal" or "Minimum"
    material_key: str,
    bom_type: BOM_TYPE,
    apply_cabling_lay: bool = False,
    density_overrides: Optional[dict] = None,
    lay_factor_overrides: Optional[dict] = None,
    tolerance_table: Optional[list] = None,
    num_cores: int = 1,            # for multi-core: weight covers all cores
) -> dict:
    """
    Annular volume formula: weight = (π/4) × (OD² - ID²) × density × lay_factor
    OD = ID + 2 × effective_thickness
    """
    tol_factor = get_thickness_tolerance_factor(nominal_thickness_mm, thickness_type, bom_type, tolerance_table)
    effective_thickness = nominal_thickness_mm * tol_factor
    od_mm = id_mm + 2 * effective_thickness

    density = get_density(material_key, bom_type, density_overrides)
    lay_factor = get_cabling_lay_factor(bom_type, lay_factor_overrides) if apply_cabling_lay else 1.0

    # annular area in mm², density in g/cm³  → weight in kg/km = area * density
    annular_area_mm2 = (math.pi / 4) * (od_mm**2 - id_mm**2)
    weight_kg_per_km = annular_area_mm2 * density * lay_factor * num_cores

    return {
        "material": material_key,
        "id_mm": round(id_mm, 3),
        "effective_thickness_mm": round(effective_thickness, 3),
        "od_mm": round(od_mm, 3),
        "tolerance_factor": tol_factor,
        "density_g_cm3": density,
        "lay_factor": lay_factor,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_armour_weight(
    cable_od_under_armour_mm: float,
    strip_width_mm: float,
    strip_thickness_mm: float,
    bom_type: BOM_TYPE,
    armour_coverage_factor: float = 1.0,   # GTP type A/B/C
    gap_mm: float = 1.0,
    density_overrides: Optional[dict] = None,
    lay_factor_overrides: Optional[dict] = None,
) -> dict:
    """
    Flat strip armour — simplified weight formula (lay factor cancels with cos α):
    weight = coverage × π × D × strip_thickness × density
    N strips still calculated from lay geometry for reference only.
    """
    num_strips_raw = (math.pi * (cable_od_under_armour_mm + strip_thickness_mm)) / (strip_width_mm + gap_mm)
    num_strips = round(num_strips_raw)  # nearest whole number, reference only

    density = get_density("gs_flat_strip_armour", bom_type, density_overrides)

    weight_kg_per_km = armour_coverage_factor * math.pi * cable_od_under_armour_mm * strip_thickness_mm * density

    return {
        "material": "gs_flat_strip_armour",
        "num_strips_calculated": num_strips_raw,
        "num_strips": num_strips,
        "strip_width_mm": strip_width_mm,
        "strip_thickness_mm": strip_thickness_mm,
        "armour_coverage_factor": armour_coverage_factor,
        "density_g_cm3": density,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_copper_tape_screen_weight(
    mean_od_mm: float,
    tape_thickness_mm: float,
    overlap_pct: float,
    bom_type: BOM_TYPE,
    density_overrides: Optional[dict] = None,
    num_cores: int = 1,
) -> dict:
    """
    Copper tape screen formula (pending IS 7098-2 confirmation):
    weight = π × mean_OD × (1 + overlap/100) × tape_thickness × density × num_cores
    """
    density = get_density("copper_tape_screen", bom_type, density_overrides)
    # Area per metre = π × mean_OD × (1+overlap) × thickness  (all in mm → mm² → cm² → g/m)
    # Weight kg/km = π × mean_OD(mm) × (1+overlap) × thickness(mm) × density(g/cm³) × (1 km / 1) × unit_conv
    # 1 mm² × 1 m = 10⁻² cm² × 100 cm = 1 cm³/m → ×density → g/m → /1000 kg/m × 1000 = kg/km
    tape_cross_section_mm2 = math.pi * mean_od_mm * (1 + overlap_pct / 100) * tape_thickness_mm
    weight_kg_per_km = tape_cross_section_mm2 * density * num_cores

    return {
        "material": "copper_tape_screen",
        "mean_od_mm": mean_od_mm,
        "tape_thickness_mm": tape_thickness_mm,
        "overlap_pct": overlap_pct,
        "density_g_cm3": density,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_round_wire_armour_weight(
    cable_od_under_armour_mm: float,
    wire_diameter_mm: float,
    bom_type: BOM_TYPE,
    armour_coverage_factor: float = 1.0,
    gap_mm: float = 0.5,
    density_overrides: Optional[dict] = None,
    lay_factor_overrides: Optional[dict] = None,
) -> dict:
    """
    Round wire armour — simplified weight formula (lay factor cancels with cos α):
    weight = coverage × π × D × (π/4 × d_wire) × density
    N wires still calculated from lay geometry for reference only.
    """
    d = wire_diameter_mm
    n_wires_raw = (math.pi * (cable_od_under_armour_mm + d)) / (d + gap_mm)
    n_wires = round(n_wires_raw)  # reference only

    density = get_density("gs_round_wire_armour", bom_type, density_overrides)

    weight_kg_per_km = armour_coverage_factor * math.pi * cable_od_under_armour_mm * (math.pi / 4 * d) * density

    return {
        "material": "gs_round_wire_armour",
        "n_wires_calculated": round(n_wires_raw, 2),
        "n_wires": n_wires,
        "wire_diameter_mm": d,
        "armour_coverage_factor": armour_coverage_factor,
        "density_g_cm3": density,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_sector_tape_weight(
    sector_r_mm: float,
    sector_angle_rad: float,
    tape_thickness_mm: float,
    overlap_pct: float,
    material_key: str,
    bom_type: BOM_TYPE,
    n_layers: int = 1,
    n_cores: int = 1,
    density_overrides: Optional[dict] = None,
) -> dict:
    """
    Tape weight over a sector-shaped conductor.

    Sector perimeter = r × (θ + 2)  [arc r×θ + two radial sides 2×r]
    Cross-section of tape per layer = perimeter × tape_thickness × (1 + overlap/100)
    Weight = cross_section × density × n_layers × n_cores
    """
    perimeter_mm = sector_r_mm * (sector_angle_rad + 2)
    density = get_density(material_key, bom_type, density_overrides)
    tape_area_mm2 = perimeter_mm * tape_thickness_mm * (1 + overlap_pct / 100)
    weight_kg_per_km = tape_area_mm2 * density * n_layers * n_cores

    return {
        "material": material_key,
        "sector_r_mm": sector_r_mm,
        "sector_angle_deg": round(math.degrees(sector_angle_rad), 3),
        "perimeter_mm": round(perimeter_mm, 4),
        "tape_thickness_mm": tape_thickness_mm,
        "overlap_pct": overlap_pct,
        "density_g_cm3": density,
        "n_layers": n_layers,
        "n_cores": n_cores,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_tape_wrap_weight(
    inner_od_mm: float,
    tape_thickness_mm: float,
    overlap_pct: float,
    material_key: str,
    bom_type: BOM_TYPE,
    n_layers: int = 1,
    density_overrides: Optional[dict] = None,
) -> dict:
    """
    Generic tape-wrap formula (swelling tape, binder tape, binding tape, PETP screen).
    weight = π × mean_OD × (1 + overlap/100) × thickness × density × n_layers
    mean_OD = inner_OD + tape_thickness
    """
    mean_od = inner_od_mm + tape_thickness_mm
    density = get_density(material_key, bom_type, density_overrides)
    tape_area_mm2 = math.pi * mean_od * (1 + overlap_pct / 100) * tape_thickness_mm
    weight_kg_per_km = tape_area_mm2 * density * n_layers

    return {
        "material": material_key,
        "mean_od_mm": round(mean_od, 3),
        "tape_thickness_mm": tape_thickness_mm,
        "overlap_pct": overlap_pct,
        "density_g_cm3": density,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_copper_wire_screen_weight(
    cable_od_mm: float,
    n_wires: int,
    wire_diameter_mm: float,
    bom_type: BOM_TYPE,
    density_overrides: Optional[dict] = None,
    lay_factor_overrides: Optional[dict] = None,
    num_cores: int = 1,
) -> dict:
    """
    Copper wire concentric screen (MV cables).
    weight = n_wires × (π/4 × d²) × density × lay_factor × num_cores
    """
    density = get_density("copper_wire_screen", bom_type, density_overrides)
    lay_factor = get_armour_lay_factor(bom_type, lay_factor_overrides)

    wire_area_mm2 = (math.pi / 4) * wire_diameter_mm ** 2
    weight_kg_per_km = n_wires * wire_area_mm2 * density * lay_factor * num_cores

    return {
        "material": "copper_wire_screen",
        "n_wires": n_wires,
        "wire_diameter_mm": wire_diameter_mm,
        "density_g_cm3": density,
        "lay_factor": lay_factor,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_drain_wire_weight(
    wire_diameter_mm: float,
    bom_type: BOM_TYPE,
    n_pairs: int = 1,
    density_overrides: Optional[dict] = None,
) -> dict:
    """
    Bare copper drain wire alongside individual pair screens.
    weight = (π/4 × d²) × density × n_pairs
    """
    density = get_density("copper_conductor", bom_type, density_overrides)
    wire_area_mm2 = (math.pi / 4) * wire_diameter_mm ** 2
    weight_kg_per_km = wire_area_mm2 * density * n_pairs

    return {
        "material": "drain_wire",
        "wire_diameter_mm": wire_diameter_mm,
        "n_pairs": n_pairs,
        "density_g_cm3": density,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


def calc_pp_filler_weight(
    od_cabled_mm: float,
    od_core_mm: float,
    n_cores: int,
    bom_type: BOM_TYPE,
    fill_factor: float = 0.87,
    density_overrides: Optional[dict] = None,
) -> dict:
    """
    PP rope filler occupying interstices between cabled cores.
    weight = ((π/4 × OD_cab²) - n_cores × (π/4 × OD_core²)) × fill_factor × density
    fill_factor accounts for imperfect packing (typically 0.85–0.90).
    """
    density = get_density("pp_filler", bom_type, density_overrides)
    cabled_area_mm2 = (math.pi / 4) * od_cabled_mm ** 2
    core_area_total_mm2 = n_cores * (math.pi / 4) * od_core_mm ** 2
    filler_area_mm2 = max(0.0, cabled_area_mm2 - core_area_total_mm2) * fill_factor
    weight_kg_per_km = filler_area_mm2 * density

    return {
        "material": "pp_filler",
        "od_cabled_mm": od_cabled_mm,
        "od_core_mm": od_core_mm,
        "n_cores": n_cores,
        "fill_factor": fill_factor,
        "density_g_cm3": density,
        "weight_kg_per_km": round(weight_kg_per_km, 3),
    }


# ── Main BOM builder ─────────────────────────────────────────────────────────

def build_bom_for_cable(
    cable: dict,
    bom_type: BOM_TYPE,
    gtp_type: str = "A",
    gtp_type_factors: Optional[dict] = None,
    density_overrides: Optional[dict] = None,
    lay_factor_overrides: Optional[dict] = None,
    tolerance_table: Optional[list] = None,
) -> list[dict]:
    """
    Given a parsed cable dict (from gtp_parser), calculate BOM for each layer.
    Returns list of dicts, one per material/layer.
    """
    # Merge built-in A/B/C defaults with any caller-supplied overrides
    _defaults = get_gtp_type_factors(gtp_type)
    gtp_type_factors = {**_defaults, **(gtp_type_factors or {})}
    bom_rows = []

    conductor_resistance_factor = gtp_type_factors.get("conductor_resistance_factor", 1.0)
    armour_coverage_factor = gtp_type_factors.get("armour_coverage", 1.0)

    num_cores = cable.get("num_cores", 1)
    effective_cores = math.ceil(num_cores)
    conductor_shape = cable.get("conductor_shape", "round")

    # ── 1. Conductor (standard or fine-wire) ─────────────────────────────────
    is_fine_wire = cable.get("conductor_class") in (5, 6) or cable.get("fine_wire", False)
    is_compacted = conductor_shape == "compacted"
    is_sector = conductor_shape == "sector"
    num_wires = cable.get("num_wires", 7)

    if is_fine_wire:
        fine_wire_lf = get_fine_wire_conductor_lay_factor(num_wires, bom_type, lay_factor_overrides)
        merged_lay_overrides = dict(lay_factor_overrides or {})
        merged_lay_overrides["conductor"] = {bom_type: fine_wire_lf}
        lay_factor_overrides_for_cond = merged_lay_overrides
    elif is_compacted:
        # Compacted circular: wires pressed together after stranding — no lay factor premium
        merged_lay_overrides = dict(lay_factor_overrides or {})
        merged_lay_overrides["conductor"] = {bom_type: 1.0}
        lay_factor_overrides_for_cond = merged_lay_overrides
    else:
        lay_factor_overrides_for_cond = lay_factor_overrides

    neutral_rdc = cable.get("neutral_dc_resistance_ohm_per_km")
    is_half_neutral = (num_cores == 3.5) and neutral_rdc is not None

    if is_half_neutral:
        phase_cores = 3
        cond_result = calc_conductor_weight(
            dc_resistance_ohm_per_km=cable["dc_resistance_ohm_per_km"],
            conductor_material=cable["conductor_material"],
            num_wires=num_wires,
            bom_type=bom_type,
            conductor_resistance_factor=conductor_resistance_factor,
            density_overrides=density_overrides,
            lay_factor_overrides=lay_factor_overrides_for_cond,
        )
        cond_result["layer"] = "Conductor (Phase)"
        cond_result["num_cores"] = phase_cores
        cond_result["weight_kg_per_km"] = round(cond_result["weight_kg_per_km"] * phase_cores, 3)
        bom_rows.append(cond_result)

        neutral_result = calc_conductor_weight(
            dc_resistance_ohm_per_km=neutral_rdc,
            conductor_material=cable["conductor_material"],
            num_wires=num_wires,
            bom_type=bom_type,
            conductor_resistance_factor=conductor_resistance_factor,
            density_overrides=density_overrides,
            lay_factor_overrides=lay_factor_overrides_for_cond,
        )
        neutral_result["layer"] = "Conductor (Neutral)"
        neutral_result["num_cores"] = 1
        bom_rows.append(neutral_result)

        effective_cores = phase_cores
    else:
        cond_result = calc_conductor_weight(
            dc_resistance_ohm_per_km=cable["dc_resistance_ohm_per_km"],
            conductor_material=cable["conductor_material"],
            num_wires=num_wires,
            bom_type=bom_type,
            conductor_resistance_factor=conductor_resistance_factor,
            density_overrides=density_overrides,
            lay_factor_overrides=lay_factor_overrides_for_cond,
        )
        cond_result["layer"] = "Conductor"
        cond_result["num_cores"] = effective_cores
        cond_result["weight_kg_per_km"] = round(cond_result["weight_kg_per_km"] * effective_cores, 3)
        bom_rows.append(cond_result)

    # ── Conductor OD — always calculated, never taken from GTP ───────────────
    phase_area_mm2 = cond_result.get("effective_area_mm2", cable.get("conductor_area_mm2") or 0)

    if is_sector and phase_area_mm2 > 0:
        neutral_area_mm2 = (
            neutral_result.get("effective_area_mm2", 0) if is_half_neutral else 0.0
        )
        sector_geo = calc_sector_conductor_geometry(
            phase_effective_area_mm2=phase_area_mm2,
            num_cores=num_cores,
            neutral_effective_area_mm2=neutral_area_mm2,
        )
        cond_result["sector_geometry"] = sector_geo
        if is_half_neutral:
            neutral_result["sector_geometry"] = sector_geo
        current_od_mm = sector_geo["sector_r2_mm"]   # 2r — ID for insulation layer
    elif phase_area_mm2 > 0:
        nominal_area = cable.get("conductor_area_mm2") or phase_area_mm2
        current_od_mm = calc_conductor_od(
            phase_area_mm2, conductor_shape, nominal_area,
            num_wires=num_wires,
        )
    else:
        current_od_mm = 0.0

    # ── 2. Each subsequent layer from GTP ─────────────────────────────────────
    # For sector conductors: track the per-core OD as it grows through insulation/screens,
    # then at the transition to outer layers report design_laidup_od_mm = core_od × factor.
    # This is a dimensional reference only — current_od_mm (used for all weights) is never
    # multiplied by this factor. Circular conductors have no laying-up safety factor.
    _per_core_keys = {
        "xlpe_insulation", "pvc_insulation", "rubber_insulation",
        "conductor_screen", "insulation_screen",
        "glass_mica_tape",
    }
    _outer_cable_keys = {
        "frlsh_outer_sheath", "pvc_outer_sheath", "pvc_frlsh_sheath",
        "lszh_outer_sheath", "lszh_inner_sheath", "hffr_sheath", "frlsh_sheath",
        "bedding", "pvc_armoured_sheath", "pvc_inner_sheath",
        "gs_flat_strip_armour", "gs_round_wire_armour", "pp_filler",
    }

    _laidup_od_reported = False
    # For sector cables: track accumulated per-core radius separately.
    # Each per-core layer adds 2×t to the sector radius (both sides).
    # At the first outer layer, bundle OD = 2 × sector_r_accum × laying-up factor.
    sector_r_accum = sector_geo["r_mm"] if (is_sector and "sector_geo" in dir()) else 0.0
    if is_sector and "sector_geometry" in cond_result:
        sector_r_accum = cond_result["sector_geometry"]["r_mm"]

    for layer in cable.get("layers", []):
        material_key = layer.get("material_key", "")
        layer_name = layer.get("layer_name", material_key)

        # At the first outer-cable layer, compute the laid-up cable OD.
        if not _laidup_od_reported and material_key in _outer_cable_keys:
            if is_sector:
                laidup_od = 2 * sector_r_accum * get_laying_up_safety_factor(bom_type)
            else:
                assemble_f = get_shattuc_concentric_factor(num_cores)
                laidup_od = assemble_f * current_od_mm
            cond_result["design_laidup_od_mm"] = round(laidup_od, 3)
            current_od_mm = laidup_od
            _laidup_od_reported = True

        if material_key in ("conductor", "fine_wire_conductor"):
            continue  # already handled above

        elif material_key == "gs_flat_strip_armour":
            strip_width = layer.get("armour_strip_width_mm") or 0
            nominal_strip_t = layer.get("armour_strip_thickness_mm") or 0
            strip_thickness = get_effective_strip_thickness(nominal_strip_t, gtp_type) if nominal_strip_t else 0
            if strip_width and strip_thickness:
                row = calc_armour_weight(
                    cable_od_under_armour_mm=current_od_mm,
                    strip_width_mm=strip_width,
                    strip_thickness_mm=strip_thickness,
                    bom_type=bom_type,
                    armour_coverage_factor=armour_coverage_factor,
                    density_overrides=density_overrides,
                    lay_factor_overrides=lay_factor_overrides,
                )
                row["layer"] = layer_name
                bom_rows.append(row)
                current_od_mm += 2 * strip_thickness

        elif material_key == "gs_round_wire_armour":
            nominal_wire_dia = layer.get("wire_diameter_mm") or 1.6
            wire_dia = get_effective_wire_diameter(nominal_wire_dia, gtp_type)
            gap = layer.get("gap_mm") or 0.5
            row = calc_round_wire_armour_weight(
                cable_od_under_armour_mm=current_od_mm,
                wire_diameter_mm=wire_dia,
                bom_type=bom_type,
                armour_coverage_factor=armour_coverage_factor,
                gap_mm=gap,
                density_overrides=density_overrides,
                lay_factor_overrides=lay_factor_overrides,
            )
            row["layer"] = layer_name
            bom_rows.append(row)
            current_od_mm += 2 * wire_dia

        elif material_key == "copper_tape_screen":
            tape_thickness = layer.get("tape_thickness_mm") or 0.1
            overlap_pct = layer.get("tape_overlap_pct") or 15.0
            mean_od = current_od_mm + tape_thickness
            row = calc_copper_tape_screen_weight(
                mean_od_mm=mean_od,
                tape_thickness_mm=tape_thickness,
                overlap_pct=overlap_pct,
                bom_type=bom_type,
                density_overrides=density_overrides,
                num_cores=effective_cores,
            )
            row["layer"] = layer_name
            bom_rows.append(row)
            current_od_mm += 2 * tape_thickness

        elif material_key == "glass_mica_tape":
            # 2 layers of mica tape treated as a single annular extrusion of 0.3 mm
            # (≈3× single-tape thickness, no overlap). Applies to both sector and round.
            _gm_extrusion_t = 0.30
            gm_cores = phase_cores if is_half_neutral else effective_cores

            if is_sector and "sector_geometry" in cond_result:
                geo = cond_result["sector_geometry"]
                r               = geo["r_mm"]
                phase_angle_r   = math.radians(geo["phase_angle_deg"])
                neutral_angle_r = math.radians(geo["neutral_angle_deg"])

                row = calc_sector_insulation_weight(
                    sector_r_mm=r,
                    sector_angle_rad=phase_angle_r,
                    nominal_thickness_mm=_gm_extrusion_t,
                    thickness_type="Nominal",
                    material_key="glass_mica_tape",
                    bom_type=bom_type,
                    n_cores=phase_cores if is_half_neutral else effective_cores,
                    apply_cabling_lay=False,
                    density_overrides=density_overrides,
                    tolerance_table=[],   # no tolerance on mica tape thickness
                )
                total_weight = row["weight_kg_per_km"]

                if is_half_neutral and neutral_angle_r > 0:
                    row_neu = calc_sector_insulation_weight(
                        sector_r_mm=r,
                        sector_angle_rad=neutral_angle_r,
                        nominal_thickness_mm=_gm_extrusion_t,
                        thickness_type="Nominal",
                        material_key="glass_mica_tape",
                        bom_type=bom_type,
                        n_cores=1,
                        apply_cabling_lay=False,
                        density_overrides=density_overrides,
                        tolerance_table=[],
                    )
                    total_weight = round(total_weight + row_neu["weight_kg_per_km"], 3)

                row["weight_kg_per_km"] = total_weight
            else:
                # Round conductor — annular extrusion, no tolerance, no cabling lay
                row = calc_annular_layer_weight(
                    id_mm=current_od_mm,
                    nominal_thickness_mm=_gm_extrusion_t,
                    thickness_type="Nominal",
                    material_key="glass_mica_tape",
                    bom_type=bom_type,
                    apply_cabling_lay=False,
                    density_overrides=density_overrides,
                    tolerance_table=[],   # no tolerance on mica tape thickness
                    num_cores=gm_cores,
                )

            row["layer"] = layer_name
            bom_rows.append(row)
            current_od_mm += 2 * _gm_extrusion_t
            if is_sector:
                sector_r_accum += 2 * _gm_extrusion_t

        elif material_key == "copper_wire_screen":
            n_wires_scr = layer.get("n_wires") or 16
            wire_dia_scr = layer.get("wire_diameter_mm") or 0.8
            row = calc_copper_wire_screen_weight(
                cable_od_mm=current_od_mm,
                n_wires=n_wires_scr,
                wire_diameter_mm=wire_dia_scr,
                bom_type=bom_type,
                density_overrides=density_overrides,
                lay_factor_overrides=lay_factor_overrides,
                num_cores=effective_cores,
            )
            row["layer"] = layer_name
            bom_rows.append(row)
            current_od_mm += 2 * wire_dia_scr

        elif material_key in ("swelling_tape", "binder_tape", "binding_tape", "binding_tape_pp",
                              "pe_tape", "al_mylar_pe_tape"):
            tape_thickness = layer.get("tape_thickness_mm") or 0.15
            overlap_pct = layer.get("tape_overlap_pct") or 15.0
            n_layers_tape = layer.get("n_pairs") or 1
            row = calc_tape_wrap_weight(
                inner_od_mm=current_od_mm,
                tape_thickness_mm=tape_thickness,
                overlap_pct=overlap_pct,
                material_key=material_key,
                bom_type=bom_type,
                n_layers=n_layers_tape,
                density_overrides=density_overrides,
            )
            row["layer"] = layer_name
            bom_rows.append(row)
            current_od_mm += 2 * tape_thickness

        elif material_key == "drain_wire":
            n_pairs_dw = layer.get("n_pairs") or 1
            _dw_weight_per_pair = {"costing": 4.5, "production": 3.0}[bom_type]
            bom_rows.append({
                "layer": layer_name,
                "material": "drain_wire",
                "n_pairs": n_pairs_dw,
                "weight_kg_per_km": round(_dw_weight_per_pair * n_pairs_dw, 3),
            })
            current_od_mm += 0.5  # account for drain wire sitting alongside screen

        elif material_key == "pp_filler":
            od_core = layer.get("od_core_mm") or current_od_mm
            od_cabled = layer.get("od_cabled_mm") or (current_od_mm * 1.15)
            fill_factor = layer.get("fill_factor") or 0.87
            row = calc_pp_filler_weight(
                od_cabled_mm=od_cabled,
                od_core_mm=od_core,
                n_cores=effective_cores,
                bom_type=bom_type,
                fill_factor=fill_factor,
                density_overrides=density_overrides,
            )
            row["layer"] = layer_name
            bom_rows.append(row)
            current_od_mm = od_cabled

        else:
            # Generic annular extrusion layer (insulation, screens, sheaths)
            thickness = layer.get("nominal_thickness_mm")
            t_type = layer.get("thickness_type") or "Nominal"
            if thickness is None:
                continue
            # Cabling lay factor applies to per-core layers (they travel helically when
            # cores are laid together). Extruded layers applied after cabling — inner sheath,
            # bedding, armoured sheath, outer sheath — go straight along the cable axis
            # and do NOT attract the cabling lay factor.
            apply_lay = material_key in _per_core_keys
            neutral_ins_thickness = layer.get("neutral_nominal_thickness_mm")

            if is_sector and apply_lay and "sector_geometry" in cond_result:
                # Sector conductor insulation: use sector area method (r + 2t)
                geo = cond_result["sector_geometry"]
                r_sec = geo["r_mm"]
                phase_angle_r   = math.radians(geo["phase_angle_deg"])
                neutral_angle_r = math.radians(geo["neutral_angle_deg"])

                row_phase = calc_sector_insulation_weight(
                    sector_r_mm=r_sec,
                    sector_angle_rad=phase_angle_r,
                    nominal_thickness_mm=thickness,
                    thickness_type=t_type,
                    material_key=material_key,
                    bom_type=bom_type,
                    n_cores=phase_cores if is_half_neutral else effective_cores,
                    apply_cabling_lay=apply_lay,
                    density_overrides=density_overrides,
                    lay_factor_overrides=lay_factor_overrides,
                    tolerance_table=tolerance_table,
                )
                row_phase["layer"] = layer_name + " (Phase)"
                bom_rows.append(row_phase)

                if is_half_neutral and neutral_ins_thickness is not None and neutral_angle_r > 0:
                    row_neutral = calc_sector_insulation_weight(
                        sector_r_mm=r_sec,
                        sector_angle_rad=neutral_angle_r,
                        nominal_thickness_mm=neutral_ins_thickness,
                        thickness_type=t_type,
                        material_key=material_key,
                        bom_type=bom_type,
                        n_cores=1,
                        apply_cabling_lay=apply_lay,
                        density_overrides=density_overrides,
                        lay_factor_overrides=lay_factor_overrides,
                        tolerance_table=tolerance_table,
                    )
                    row_neutral["layer"] = layer_name + " (Neutral)"
                    bom_rows.append(row_neutral)

                tol_f = get_thickness_tolerance_factor(thickness, t_type, bom_type, tolerance_table)
                current_od_mm = r_sec + 2 * thickness * tol_f
                if is_sector:
                    sector_r_accum += 2 * thickness * tol_f

            elif is_half_neutral and material_key in _per_core_keys and neutral_ins_thickness is not None:
                # 3.5C round conductor: separate phase + neutral
                row_phase = calc_annular_layer_weight(
                    id_mm=current_od_mm,
                    nominal_thickness_mm=thickness,
                    thickness_type=t_type,
                    material_key=material_key,
                    bom_type=bom_type,
                    apply_cabling_lay=apply_lay,
                    density_overrides=density_overrides,
                    lay_factor_overrides=lay_factor_overrides,
                    tolerance_table=tolerance_table,
                    num_cores=3,
                )
                row_phase["layer"] = layer_name + " (Phase)"
                bom_rows.append(row_phase)

                row_neutral = calc_annular_layer_weight(
                    id_mm=current_od_mm,
                    nominal_thickness_mm=neutral_ins_thickness,
                    thickness_type=t_type,
                    material_key=material_key,
                    bom_type=bom_type,
                    apply_cabling_lay=apply_lay,
                    density_overrides=density_overrides,
                    lay_factor_overrides=lay_factor_overrides,
                    tolerance_table=tolerance_table,
                    num_cores=1,
                )
                row_neutral["layer"] = layer_name + " (Neutral)"
                bom_rows.append(row_neutral)

                od_after = layer.get("od_mm") or row_phase["od_mm"]
                current_od_mm = od_after
            else:
                row = calc_annular_layer_weight(
                    id_mm=current_od_mm,
                    nominal_thickness_mm=thickness,
                    thickness_type=t_type,
                    material_key=material_key,
                    bom_type=bom_type,
                    apply_cabling_lay=apply_lay,
                    density_overrides=density_overrides,
                    lay_factor_overrides=lay_factor_overrides,
                    tolerance_table=tolerance_table,
                    num_cores=(effective_cores if material_key in _per_core_keys else 1),
                )
                row["layer"] = layer_name
                bom_rows.append(row)
                od_after = layer.get("od_mm") or row["od_mm"]
                current_od_mm = od_after

    # Aggregate rows with the same material_key (e.g. copper_conductor phase + neutral)
    aggregated = {}
    order = []
    for row in bom_rows:
        key = row["material"]
        if key not in aggregated:
            aggregated[key] = dict(row)
            aggregated[key]["layer"] = key.replace("_", " ").title()
            order.append(key)
        else:
            aggregated[key]["weight_kg_per_km"] = round(
                aggregated[key]["weight_kg_per_km"] + row["weight_kg_per_km"], 3
            )
    return [aggregated[k] for k in order]
