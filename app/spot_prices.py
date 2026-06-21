"""
Fleet Commander — Spot Price Integration (Feature 10)

Fetches real electricity spot prices when configured, falling back to
synthetic mock prices. Supports a pluggable provider model.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from app.config import settings
from app.v2g_optimizer import mock_spot_prices as _mock_prices

logger = logging.getLogger(__name__)


def fetch_spot_prices(hours: int = 24) -> list[float]:
    """Fetch spot prices for the next N hours.

    Tries the configured provider; falls back to mock prices on any error.
    Returns a list of USD/kWh prices (length == hours).
    """
    provider = settings.spot_price_provider.lower()
    if provider == "mock" or not settings.spot_price_url:
        return _mock_prices(hours=hours)

    try:
        if provider in ("iex", "entsoe", "api"):
            return _fetch_from_api(hours)
    except Exception:
        logger.exception("Spot price fetch failed — falling back to mock")

    return _mock_prices(hours=hours)


def _fetch_from_api(hours: int) -> list[float]:
    """Fetch spot prices from a generic API endpoint.

    Expects a JSON response with a 'prices' array of {timestamp, price} or
    a flat array of floats. Prices assumed to be USD/kWh (or USD/MWh with
    a 'unit' field indicating 'MWh').
    """
    headers = {}
    if settings.spot_price_api_key:
        headers["Authorization"] = f"Bearer {settings.spot_price_api_key}"

    resp = requests.get(settings.spot_price_url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    unit = data.get("unit", "kWh")
    divisor = 1000.0 if unit.upper() == "MWH" else 1.0

    raw_prices = data.get("prices", data) if isinstance(data, dict) else data
    if not isinstance(raw_prices, list):
        return _mock_prices(hours=hours)

    prices: list[float] = []
    for item in raw_prices[:hours]:
        if isinstance(item, dict):
            p = float(item.get("price", item.get("value", 0)))
        else:
            p = float(item)
        prices.append(round(p / divisor, 4))

    # Pad with mock if the API returned fewer than requested
    while len(prices) < hours:
        prices.extend(_mock_prices(hours=hours - len(prices)))

    return prices[:hours]
