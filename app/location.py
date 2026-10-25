import math
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy import func
from app.config import get_settings
from app.schemas import LocationFixRequest

settings = get_settings()


def validate_location_fix(fix: LocationFixRequest) -> None:
    """Server-authoritative location evidence check.
    
    Threat Model Disclosure: Client GPS fixes can be spoofed by rooted devices.
    Server validation mitigates replay, stale fixes, out-of-range bounds, and inaccurate fixes.
    """
    if fix.accuracy_meters > settings.MAX_GPS_ACCURACY_METERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"GPS accuracy ({fix.accuracy_meters:.1f}m) exceeds permitted maximum ({settings.MAX_GPS_ACCURACY_METERS:.1f}m)",
        )

    now_utc = datetime.now(timezone.utc)
    recorded_utc = (
        fix.recorded_at.replace(tzinfo=timezone.utc)
        if fix.recorded_at.tzinfo is None
        else fix.recorded_at.astimezone(timezone.utc)
    )
    age_seconds = (now_utc - recorded_utc).total_seconds()

    if age_seconds < -5.0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Location fix timestamp is in the future",
        )

    if age_seconds > settings.MAX_LOCATION_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Location fix is stale ({age_seconds:.1f}s old, max permitted is {settings.MAX_LOCATION_AGE_SECONDS}s)",
        )


def make_geography_point(longitude: float, latitude: float):
    """Return PostGIS geography point expression."""
    return func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)


def format_coarse_distance_label(distance_meters: float) -> str:
    """Bucket distance to protect exact user coordinates."""
    if math.isnan(distance_meters) or distance_meters < 0:
        distance_meters = 0.0

    if distance_meters < 100:
        return "< 100m away"
    elif distance_meters < 1000:
        rounded = round(distance_meters / 50.0) * 50
        return f"~{rounded}m away"
    else:
        km = distance_meters / 1000.0
        return f"~{km:.1f} km away"
