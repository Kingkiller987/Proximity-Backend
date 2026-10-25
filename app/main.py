from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from app.database import check_database
from app.routers import auth, users, landmarks, media, events

app = FastAPI(title="Proximity API", version="0.1.0")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(landmarks.router)
app.include_router(media.router)
app.include_router(events.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "proximity-api"}


@app.get("/health/database")
def database_health():
    try:
        result = check_database(is_test=False)
        return {"status": "ok", **result}
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )
