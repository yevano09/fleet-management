"""OBD-II helpers (P0-A): DTC decode table lives in app.obd.dtc."""

from app.obd.dtc import describe_dtc, describe_list

__all__ = ["describe_dtc", "describe_list"]
