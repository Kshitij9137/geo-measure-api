"""Make feature attributes JSON-safe (NaN, NaT, numpy scalars, timestamps, bytes...)."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd


def sanitize_value(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp | dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list | tuple):
        return [sanitize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): sanitize_value(v) for k, v in value.items()}
    return str(value)


def sanitize_properties(record: dict[str, Any]) -> dict[str, Any]:
    return {str(key): sanitize_value(val) for key, val in record.items()}
