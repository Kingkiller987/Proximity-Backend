from datetime import datetime, timezone, timedelta
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_

from app.database import get_db
from app.models import User, LandmarkVisit, Landmark, UserBlock
from app.schemas import LocationFixRequest, NearbyParticipantResponse
from app.dependencies import get_current_user
from app.location import (
    validate_location_fix,
    make_geography_point,
    format_coarse_distance_label,
)
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/nearby", tags=["nearby"])


@router.post("", response_model=List[NearbyParticipantResponse])
def discover_nearby_participants(
    fix: LocationFixRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    validate_location_fix(fix)

    # Invariant: Ghost mode users who did not enable ghost browsing cannot see nearby users
    if current_user.is_ghost_mode and not current_user.ghost_browse_nearby:
        return []

    requester_pt = make_geography_point(fix.longitude, fix.latitude)
    now_utc = datetime.now(timezone.utc)
    fresh_cutoff = now_utc - timedelta(seconds=settings.MAX_LOCATION_AGE_SECONDS)

    # Load bidirectional blocked user IDs
    blocked_ids = set(
        [r[0] for r in db.query(UserBlock.blocked_id).filter(UserBlock.blocker_id == current_user.id).all()]
        + [r[0] for r in db.query(UserBlock.blocker_id).filter(UserBlock.blocked_id == current_user.id).all()]
    )

    results: List[NearbyParticipantResponse] = []

    # Include self if visible in nearby and not in ghost mode
    if current_user.visible_in_nearby and not current_user.is_ghost_mode:
        self_display = (
            current_user.anonymous_handle
            if current_user.identity_mode == "anonymous"
            else f"@{current_user.username}"
        )
        results.append(
            NearbyParticipantResponse(
                user_id=current_user.id,
                display_name=f"{self_display} (You)",
                is_anonymous=(current_user.identity_mode == "anonymous"),
                area_name=fix.area_name or "Current Location",
                distance_label="< 100m away",
                visible_in_nearby=True,
            )
        )

    # Query active nearby participants inside configured radius
    query = (
        db.query(
            User,
            Landmark.area_name,
            func.ST_Distance(Landmark.location, requester_pt).label("dist_m"),
        )
        .join(LandmarkVisit, LandmarkVisit.user_id == User.id)
        .join(Landmark, Landmark.id == LandmarkVisit.landmark_id)
        .filter(
            LandmarkVisit.is_active == True,
            LandmarkVisit.last_heartbeat_at >= fresh_cutoff,
            User.visible_in_nearby == True,
            User.is_ghost_mode == False,
            User.id != current_user.id,
            func.ST_DWithin(Landmark.location, requester_pt, current_user.nearby_radius_meters),
        )
    )

    if blocked_ids:
        query = query.filter(User.id.notin_(blocked_ids))

    nearby_rows = query.order_by("dist_m").all()

    for user, area_name, dist_m in nearby_rows:
        display_name = (
            user.anonymous_handle
            if user.identity_mode == "anonymous"
            else f"@{user.username}"
        )
        dist_lbl = format_coarse_distance_label(float(dist_m))

        results.append(
            NearbyParticipantResponse(
                user_id=user.id,
                display_name=display_name,
                is_anonymous=(user.identity_mode == "anonymous"),
                area_name=area_name,
                distance_label=dist_lbl,
                visible_in_nearby=True,
            )
        )

    return results
