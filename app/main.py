from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import check_database
from app.routers import auth, users, landmarks, media, events, nearby, dm

settings = get_settings()

app = FastAPI(title="Proximity API", version="0.1.0")

# Optional CORS middleware only mounted when CORS_ALLOWED_ORIGINS is explicitly configured
if settings.CORS_ALLOWED_ORIGINS:
    origins = [orig.strip() for orig in settings.CORS_ALLOWED_ORIGINS.split(",") if orig.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            allow_headers=["*"],
        )

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(landmarks.router)
app.include_router(media.router)
app.include_router(events.router)
app.include_router(nearby.router)
app.include_router(dm.router)


@app.get("/health")
def health():
    """Liveness probe: fast, independent of database or storage, safe for platform uptime monitors."""
    return {"status": "ok", "service": "proximity-api"}


@app.get("/health/ready")
def readiness():
    """Readiness probe: verifies database connectivity and PostGIS without leaking internals."""
    try:
        check_database(is_test=False)
        return {"status": "ready", "database": "connected", "postgis": "available"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database service unavailable",
        )


@app.get("/health/database")
def database_health():
    """Compatibility endpoint for database health check."""
    try:
        result = check_database(is_test=False)
        return {"status": "ok", **result}
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )
