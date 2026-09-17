"""OBD-II DTC decode table (P0-A).

Maps diagnostic trouble codes to human meaning for evidence enrichment.
Families follow SAE J2012: P=powertrain, C=chassis, B=body, U=network.
Second char: 0=SAE generic, 1=manufacturer specific.
Unknown codes still decode to family + generic/manufacturer wording.
"""

from __future__ import annotations

# Curated meanings for codes this fleet actually emits (simulator + gateway).
KNOWN = {
    "P0128": "Coolant thermostat: engine not reaching operating temperature (cooling fault)",
    "P0300": "Random/multiple cylinder misfire detected",
    "P0420": "Catalyst efficiency below threshold",
    "C0745": "Tire pressure monitoring: pressure loss detected (slow puncture pattern)",
    "C0035": "Wheel speed sensor circuit fault",
    "U0100": "Lost communication with ECM/PCM (radio/link degradation pattern)",
    "U0121": "Lost communication with ABS module",
}

_FAMILY = {"P": "powertrain", "C": "chassis", "B": "body", "U": "network"}


def describe_dtc(code: str) -> str:
    """One-line human meaning for a DTC string (robust to junk input)."""
    c = (code or "").strip().upper()
    if c in KNOWN:
        return KNOWN[c]
    if len(c) >= 5 and c[0] in _FAMILY and c[1] in "01":
        scope = "generic" if c[1] == "0" else "manufacturer-specific"
        return f"{_FAMILY[c[0]]} {scope} fault {c} (no curated entry)"
    return f"unrecognized code {c or '?'!r}"


def describe_list(codes) -> list[str]:
    if not codes:
        return []
    if isinstance(codes, str):
        try:
            import json

            parsed = json.loads(codes)
            codes = parsed if isinstance(parsed, list) else [codes]
        except Exception:
            codes = [codes]
    return [f"{c}: {describe_dtc(str(c))}" for c in codes]
