from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import User, RefreshSession
from app.schemas import UserRegisterRequest, UserLoginRequest, TokenResponse, RefreshTokenRequest
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    generate_refresh_token,
    hash_token,
    generate_anonymous_handle,
)
from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(data: UserRegisterRequest, db: Session = Depends(get_db)):
    clean_username = data.username.strip()
    clean_email = data.email.strip().lower()

    # Case-insensitive uniqueness check
    existing_user = db.query(User).filter(
        func.lower(User.username) == clean_username.lower()
    ).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already taken",
        )

    existing_email = db.query(User).filter(
        func.lower(User.email) == clean_email
    ).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email address is already registered",
        )

    # Argon2id password hashing
    pw_hash = hash_password(data.password)
    anon_handle = generate_anonymous_handle()

    new_user = User(
        username=clean_username,
        email=clean_email,
        password_hash=pw_hash,
        anonymous_handle=anon_handle,
        identity_mode="username",
    )
    db.add(new_user)
    db.flush()

    # Issue initial tokens and persist hashed refresh session
    access_token = create_access_token(str(new_user.id))
    raw_refresh = generate_refresh_token()
    token_digest = hash_token(raw_refresh)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    session_record = RefreshSession(
        user_id=new_user.id,
        token_hash=token_digest,
        expires_at=expires_at,
    )
    db.add(session_record)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/login", response_model=TokenResponse)
def login(data: UserLoginRequest, db: Session = Depends(get_db)):
    clean_username = data.username.strip().lower()
    user = db.query(User).filter(
        func.lower(User.username) == clean_username
    ).first()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    access_token = create_access_token(str(user.id))
    raw_refresh = generate_refresh_token()
    token_digest = hash_token(raw_refresh)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    session_record = RefreshSession(
        user_id=user.id,
        token_hash=token_digest,
        expires_at=expires_at,
    )
    db.add(session_record)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(data: RefreshTokenRequest, db: Session = Depends(get_db)):
    token_digest = hash_token(data.refresh_token)
    # Row-level lock to prevent concurrent token rotation race conditions & duplicate issuance
    session_record = db.query(RefreshSession).filter(
        RefreshSession.token_hash == token_digest
    ).with_for_update().first()

    now_utc = datetime.now(timezone.utc)
    if not session_record or session_record.revoked_at is not None or session_record.expires_at <= now_utc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, revoked, or expired refresh token",
        )

    # Refresh token rotation: revoke old session immediately
    session_record.revoked_at = now_utc

    # Create new refresh session
    user = session_record.user
    new_raw_refresh = generate_refresh_token()
    new_token_digest = hash_token(new_raw_refresh)
    new_expires_at = now_utc + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    new_session_record = RefreshSession(
        user_id=user.id,
        token_hash=new_token_digest,
        expires_at=new_expires_at,
    )
    db.add(new_session_record)
    db.commit()

    access_token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(data: RefreshTokenRequest, db: Session = Depends(get_db)):
    token_digest = hash_token(data.refresh_token)
    session_record = db.query(RefreshSession).filter(
        RefreshSession.token_hash == token_digest
    ).with_for_update().first()

    if session_record and session_record.revoked_at is None:
        session_record.revoked_at = datetime.now(timezone.utc)
        db.commit()

    return None
