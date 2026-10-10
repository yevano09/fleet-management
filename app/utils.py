from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a NAIVE datetime (no tzinfo).

    Contract: all DB timestamp columns in this project are naive, and every
    writer must use this helper. Never compare its result directly against a
    tz-aware datetime — normalize the other side with :func:`as_utcnaive`
    first, or use :func:`utcnow_aware` when an aware value is needed.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_aware() -> datetime:
    """Current UTC time as a tz-AWARE datetime (for crypto/X.509, wire use)."""
    return datetime.now(timezone.utc)


def as_utcnaive(value: datetime) -> datetime:
    """Normalize any datetime to the naive-UTC DB contract.

    Aware values are converted to UTC and stripped; naive values are assumed
    to already be UTC and returned unchanged.
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
