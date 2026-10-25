import pytest
from starlette.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.database import get_db, get_test_db, test_engine, assert_not_production_db
from app.config import get_settings

settings = get_settings()


@pytest.fixture(scope="session", autouse=True)
def verify_test_database_isolation():
    """Guarantee tests never connect to the development database."""
    with test_engine.connect() as conn:
        row = conn.execute(text("SELECT current_database(), PostGIS_Version()")).one()
        current_db = row[0]
        if current_db == settings.POSTGRES_DB:
            raise RuntimeError(
                f"SAFETY SHUTDOWN: Test session configured against production database {current_db}!"
            )
        assert current_db == settings.POSTGRES_TEST_DB, (
            f"Expected {settings.POSTGRES_TEST_DB}, got {current_db}"
        )


@pytest.fixture
def db_session():
    """Provides a transactional database session for tests with automatic rollback."""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        from sqlalchemy.orm import Session
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        assert_not_production_db(session)
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with overridden get_db pointing to isolated test DB."""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
