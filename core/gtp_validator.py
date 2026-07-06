"""
Validates a parsed cable dict for completeness and physical plausibility.
Returns (confidence, issues) so the caller can decide whether to retry via AI.
"""

_RDC_MIN = 0.001   # ~1200 mm² conductor
_RDC_MAX = 100.0   # ~0.1 mm² conductor


def validate_cable(cable: dict) -> tuple:
    """
    Returns (confidence: float [0.0–1.0], issues: list[str]).

    confidence < 0.5 means fewer than half the sanity checks passed →
    the caller should attempt an AI fallback.
    """
    issues = []
    passes = 0
    total = 6

    # 1. DC resistance present and physically plausible
    rdc = cable.get("dc_resistance_ohm_per_km")
    if rdc is not None and _RDC_MIN <= float(rdc) <= _RDC_MAX:
        passes += 1
    else:
        issues.append(f"dc_resistance_ohm_per_km missing or out of range: {rdc!r}")

    # 2. At least 2 layers parsed
    layers = cable.get("layers") or []
    if len(layers) >= 2:
        passes += 1
    else:
        issues.append(f"only {len(layers)} layer(s) parsed (expected ≥ 2)")

    # 3. At least one layer with a positive thickness
    has_thickness = any(
        (l.get("nominal_thickness_mm") or 0) > 0 for l in layers
    )
    if has_thickness:
        passes += 1
    else:
        issues.append("no layer has nominal_thickness_mm > 0")

    # 4. Cable type was identified
    ctype = cable.get("cable_type")
    if ctype and ctype != "unknown":
        passes += 1
    else:
        issues.append(f"cable_type not detected: {ctype!r}")

    # 5. num_cores is present and reasonable
    nc = cable.get("num_cores")
    if nc is not None and 1 <= int(nc) <= 61:
        passes += 1
    else:
        issues.append(f"num_cores missing or implausible: {nc!r}")

    # 6. Designation / config string is present
    desig = cable.get("designation") or cable.get("config") or ""
    if str(desig).strip():
        passes += 1
    else:
        issues.append("designation / config string missing")

    return passes / total, issues
