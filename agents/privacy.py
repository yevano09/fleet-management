"""Copilot privacy layer (M2.1): PII redaction, data minimization, injection guards.

Design contract — NOTHING reaches an LLM (mock or real) without passing
through here:
1. User messages are scanned for prompt-injection patterns; hits are flagged,
   logged, and the turn is restricted to read-only summaries (all copilot
   tools are read-only anyway — there is deliberately no mutating tool).
2. Tool outputs are minimized (aggregates over raw rows; capped lists) and
   redacted (names→roles, emails/phones removed, GPS degraded, VINs masked)
   BEFORE being placed in LLM context.
3. Only redacted content is persisted (CopilotMessage.content); a SHA-256 of
   the raw user message is kept for abuse forensics, never the raw text.
"""

from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
# Common "forget your instructions" jailbreak shapes.
INJECTION_RES = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"reveal\s+(your\s+)?(system|initial)\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"disregard\s+.*(policy|safety|guardrail)", re.I),
    re.compile(r"\[system\]|\[SYSTEM\]|<<SYS>>"),
    re.compile(r"drop\s+table|delete\s+from|;\s*--", re.I),
]

# Fields that must never appear in LLM context, by source.
DROP_DEVICE_FIELDS = {"ip_address", "mqtt_client_id"}
DROP_TELEMETRY_FIELDS: set[str] = set()  # all telemetry fields are operational, kept


def raw_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def scan_injection(message: str) -> list[str]:
    """Return matched injection pattern descriptions (empty = clean)."""
    hits = []
    for rx in INJECTION_RES:
        m = rx.search(message or "")
        if m:
            hits.append(m.group(0)[:60])
    return hits


def redact_text(text: str) -> str:
    """Strip emails/phones from free text."""
    text = EMAIL_RE.sub("[email]", text or "")
    text = PHONE_RE.sub("[phone]", text or "")
    return text


def redact_name(name: str | None) -> str:
    """Replace personal names with stable role pseudonyms (no PII to LLM)."""
    if not name:
        return "unknown-device"
    digest = hashlib.sha256(name.encode()).hexdigest()[:6]
    return f"device-{digest}"


def mask_vin(vin: str | None) -> str | None:
    if not vin or len(vin) < 4:
        return vin
    return "***" + vin[-4:]


def degrade_gps(lat, lng):
    """Round coordinates to ~1km so LLM context never carries exact position."""
    if lat is None or lng is None:
        return None, None
    return round(float(lat), 2), round(float(lng), 2)


def minimize_device(device: dict) -> dict:
    """Project a device dict to the LLM-safe subset."""
    lat, lng = degrade_gps(device.get("latitude"), device.get("longitude"))
    return {
        "ref": redact_name(device.get("name")),
        "status": device.get("status"),
        "firmware": device.get("firmware_version"),
        "city": device.get("city"),
        "lat": lat,
        "lng": lng,
        "signal_dbm": device.get("signal_strength"),
        "uptime_pct": device.get("uptime_percentage"),
        "soc": device.get("soc"),
        "soh": device.get("soh"),
    }


def minimize_telemetry(points: list[dict], limit: int = 12) -> list[dict]:
    """Cap + project telemetry windows (aggregates preferred by callers)."""
    keep = {"signal_strength", "temperature", "soc", "soh", "uptime_percentage",
            "fuel_level_pct", "odometer_km", "timestamp"}
    return [
        {k: v for k, v in p.items() if k in keep}
        for p in points[-limit:]
    ]


def summarize_for_llm(title: str, payload: dict, cap: int = 2000) -> str:
    """Render a tool result as delimited DATA (never instructions)."""
    import json

    body = json.dumps(payload, default=str)[:cap]
    return f"<{title}-DATA>\n{body}\n</{title}-DATA>"
