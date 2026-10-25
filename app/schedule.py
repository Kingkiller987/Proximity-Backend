import zoneinfo
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException, status
from app.models import Event


def validate_timezone(tz_name: str) -> zoneinfo.ZoneInfo:
    """Validate and load venue IANA timezone."""
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid venue timezone: {tz_name}",
        )


def is_within_operating_window(event: Event, ref_dt_utc: datetime) -> bool:
    """
    Determine if ref_dt_utc is within the event venue operating window,
    correctly handling venue timezone, daily overrides, and overnight schedules.
    """
    tz = validate_timezone(event.venue_timezone)
    if ref_dt_utc.tzinfo is None:
        ref_dt_utc = ref_dt_utc.replace(tzinfo=timezone.utc)
    local_dt = ref_dt_utc.astimezone(tz)

    date_key = local_dt.strftime("%Y-%m-%d")
    curr_minutes = local_dt.hour * 60 + local_dt.minute

    overrides = event.daily_overrides or {}
    day_override = overrides.get(date_key)

    if day_override:
        if day_override.get("is_closed", False):
            return False
        open_min = day_override.get("open_minutes", event.default_open_minutes)
        close_min = day_override.get("close_minutes", event.default_close_minutes)
    else:
        open_min = event.default_open_minutes
        close_min = event.default_close_minutes

    if open_min is None or close_min is None:
        return True

    if close_min > open_min:
        # Standard daytime schedule (e.g., 09:00 - 17:00)
        return open_min <= curr_minutes < close_min
    elif close_min < open_min:
        # Overnight schedule spanning midnight (e.g., 20:00 - 02:00)
        return curr_minutes >= open_min or curr_minutes < close_min
    else:
        # open_min == close_min means 24-hour operation
        return True
