from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.config import get_settings

settings = get_settings()

DATABASE_URL = settings.get_database_url(is_test=False)
TEST_DATABASE_URL = settings.get_database_url(is_test=True)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_test_db() -> Generator[Session, None, None]:
    db = TestSessionLocal()
    try:
        # Hard assertion that test session can never touch proximity production db
        assert_not_production_db(db)
        yield db
    finally:
        db.close()


def assert_not_production_db(session: Session) -> None:
    row = session.execute(text("SELECT current_database()")).one()
    current_db = row[0]
    if current_db == settings.POSTGRES_DB:
        raise RuntimeError(
            f"FATAL SAFETY VIOLATION: Test connection targeted production database {current_db}!"
        )


def check_database(is_test: bool = False) -> dict:
    target_engine = test_engine if is_test else engine
    with target_engine.connect() as connection:
        row = connection.execute(
            text("SELECT current_database(), PostGIS_Version()")
        ).one()

    return {
        "database": row[0],
        "postgis_version": row[1],
    }
