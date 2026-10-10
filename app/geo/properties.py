"""Make attribute values safe to store as JSON and return from the API."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return json_safe(float(value))
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


def json_safe_properties(props: dict[str, Any]) -> dict[str, Any]:
    return {str(k): json_safe(v) for k, v in props.items()}
