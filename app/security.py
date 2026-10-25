import secrets
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from typing import Optional
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from app.config import get_settings

settings = get_settings()
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash password using Argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against Argon2id hash."""
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create short-lived JWT access token."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Decode and validate JWT access token."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])


def generate_refresh_token() -> str:
    """Generate cryptographically secure random refresh token string."""
    return secrets.token_urlsafe(32)


def hash_token(raw_token: str) -> str:
    """Compute SHA-256 digest of refresh token for database storage."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def hash_join_code(code: str, salt: str) -> str:
    """Compute server-secret keyed HMAC-SHA256 digest for low-entropy join codes."""
    key = settings.SECRET_KEY.encode("utf-8")
    msg = f"{salt}:{code.strip()}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


# Deterministic anonymous name generator
_ADJECTIVES = [
    "Silent", "Swift", "Azure", "Amber", "Velvet", "Golden", "Quiet", "Bright",
    "Silver", "Wild", "Brave", "Calm", "Frost", "Mystic", "Solar", "Lunar"
]
_NOUNS = [
    "Echo", "Falcon", "Otter", "Shadow", "River", "Wanderer", "Sparrow", "Breeze",
    "Fox", "Pine", "Harbor", "Voyager", "Hawk", "Trail", "Beacon", "Pebble"
]


def generate_anonymous_handle(seed: Optional[int] = None) -> str:
    if seed is not None:
        adj = _ADJECTIVES[seed % len(_ADJECTIVES)]
        noun = _NOUNS[(seed // len(_ADJECTIVES)) % len(_NOUNS)]
        suffix = (seed % 900) + 100
    else:
        adj = secrets.choice(_ADJECTIVES)
        noun = secrets.choice(_NOUNS)
        suffix = secrets.randbelow(900) + 100
    return f"{adj}{noun}{suffix}"
