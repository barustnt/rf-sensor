from __future__ import annotations

from datetime import datetime
from typing import Any

from rf_platform.common.time import parse_datetime

MAX_LIMIT = 500
DEFAULT_LIMIT = 50


def clamp_limit_offset(limit: int = DEFAULT_LIMIT, offset: int = 0) -> tuple[int, int]:
    return min(max(limit, 1), MAX_LIMIT), max(offset, 0)


def paged_response(
    items: list[dict[str, Any]], total: int, limit: int, offset: int
) -> dict[str, Any]:
    return {"items": items, "count": len(items), "total": total, "limit": limit, "offset": offset}


def parse_optional_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return parse_datetime(value)
