from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from app.database import get_db
from app.models import User, UserBlock, LandmarkVisit
from app.schemas import UserProfileResponse, UserUpdateRequest, PublicIdentityProjection
from app.dependencies import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserProfileResponse)
def get_my_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserProfileResponse)
def update_my_profile(
    data: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.bio is not None:
        current_user.bio = data.bio
    if data.identity_mode is not None:
        current_user.identity_mode = data.identity_mode
    if data.visible_in_nearby is not None:
        current_user.visible_in_nearby = data.visible_in_nearby
    if data.nearby_radius_meters is not None:
        current_user.nearby_radius_meters = data.nearby_radius_meters

    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/ghost-mode", response_model=UserProfileResponse)
def toggle_ghost_mode(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Toggle ghost mode. Turning ghost mode ON immediately terminates all active landmark visits."""
    new_state = not current_user.is_ghost_mode
    current_user.is_ghost_mode = new_state

    if new_state:
        # Invariant: Ghost mode immediately revokes active landmark visit sessions
        db.query(LandmarkVisit).filter(
            LandmarkVisit.user_id == current_user.id,
            LandmarkVisit.is_active == True,
        ).update(
            {"is_active": False, "exit_reason": "ghost_mode"},
            synchronize_session="fetch",
        )

    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/{user_id}", response_model=PublicIdentityProjection)
def get_public_profile(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Invariant: Bidirectional block suppression
    is_blocked = db.query(UserBlock).filter(
        or_(
            and_(UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == target.id),
            and_(UserBlock.blocker_id == target.id, UserBlock.blocked_id == current_user.id),
        )
    ).first()
    if is_blocked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Invariant: Anonymous identity privacy projection
    if target.identity_mode == "anonymous":
        return PublicIdentityProjection(
            user_id=target.id,
            display_name=target.anonymous_handle,
            bio="Anonymous participant",
            is_anonymous=True,
        )

    return PublicIdentityProjection(
        user_id=target.id,
        display_name=f"@{target.username}",
        bio=target.bio,
        is_anonymous=False,
    )


@router.post("/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
def block_user(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot block yourself",
        )

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    existing = db.query(UserBlock).filter(
        UserBlock.blocker_id == current_user.id,
        UserBlock.blocked_id == user_id,
    ).first()

    if not existing:
        db.add(UserBlock(blocker_id=current_user.id, blocked_id=user_id))
        db.commit()

    return None


@router.delete("/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
def unblock_user(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(UserBlock).filter(
        UserBlock.blocker_id == current_user.id,
        UserBlock.blocked_id == user_id,
    ).delete()
    db.commit()
    return None
