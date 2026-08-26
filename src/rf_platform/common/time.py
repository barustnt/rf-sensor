from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_DISPLAY_TIMEZONE = "Asia/Dubai"


@dataclass(frozen=True)
class InterpretedInterval:
    start_utc: datetime
    end_utc: datetime
    display_timezone: str
    assumptions: list[str]


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return ensure_utc(datetime.fromisoformat(normalized))


def to_local(value: datetime, timezone_name: str = DEFAULT_DISPLAY_TIMEZONE) -> datetime:
    return ensure_utc(value).astimezone(ZoneInfo(timezone_name))


_CLOCK_RE = re.compile(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\b", re.I)
_ISO_DT_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)


def _clock_from_text(text: str) -> tuple[int, int] | None:
    matches = list(_CLOCK_RE.finditer(text))
    for match in matches:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        ampm = (match.group("ampm") or "").lower()
        if hour > 23 or minute > 59:
            continue
        if ampm:
            if hour < 1 or hour > 12:
                continue
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
        return hour, minute
    return None


def resolve_historical_interval(
    question: str,
    timezone_name: str = DEFAULT_DISPLAY_TIMEZONE,
    now: datetime | None = None,
) -> InterpretedInterval:
    """Resolve a small deterministic phrase set into a UTC interval.

    Supported Milestone 1 forms: ISO dates/datetimes, `today`, `yesterday`, and optional clock
    times such as `11 PM`. Ambiguous day-only queries return one local day; clocked queries return
    a one-hour interval.
    """

    local_tz = ZoneInfo(timezone_name)
    now_local = ensure_utc(now or utc_now()).astimezone(local_tz)
    text = question.strip().lower()
    assumptions: list[str] = []

    iso_match = _ISO_DT_RE.search(question)
    if iso_match:
        raw = iso_match.group(0)
        if "T" in raw or " " in raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
                assumptions.append(f"Interpreted naive ISO datetime in {timezone_name}.")
            start_local = parsed.astimezone(local_tz)
            end_local = start_local + timedelta(hours=1)
        else:
            date_value = datetime.fromisoformat(raw).date()
            start_local = datetime.combine(date_value, time.min, tzinfo=local_tz)
            end_local = start_local + timedelta(days=1)
            assumptions.append("Interpreted date-only query as the whole local day.")
        return InterpretedInterval(
            start_utc=start_local.astimezone(UTC),
            end_utc=end_local.astimezone(UTC),
            display_timezone=timezone_name,
            assumptions=assumptions,
        )

    if "yesterday" in text:
        base_date = (now_local - timedelta(days=1)).date()
    elif "today" in text:
        base_date = now_local.date()
    else:
        base_date = now_local.date()
        assumptions.append("No explicit day found; assumed today in the display timezone.")

    clock = _clock_from_text(question)
    if "morning" in text and clock is None:
        start_local = datetime.combine(base_date, time(6, 0), tzinfo=local_tz)
        end_local = datetime.combine(base_date, time(12, 0), tzinfo=local_tz)
        assumptions.append("Interpreted morning as 6:00 AM to noon in the display timezone.")
    elif clock:
        hour, minute = clock
        start_local = datetime.combine(base_date, time(hour, minute), tzinfo=local_tz)
        end_local = start_local + timedelta(hours=1)
        assumptions.append("Interpreted clock time as a one-hour interval.")
    else:
        start_local = datetime.combine(base_date, time.min, tzinfo=local_tz)
        end_local = start_local + timedelta(days=1)
        assumptions.append("No clock time found; interpreted query as the whole local day.")

    return InterpretedInterval(
        start_utc=start_local.astimezone(UTC),
        end_utc=end_local.astimezone(UTC),
        display_timezone=timezone_name,
        assumptions=assumptions,
    )
