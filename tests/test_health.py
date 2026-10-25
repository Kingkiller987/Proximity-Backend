import pytest
from unittest.mock import MagicMock
from app.database import assert_not_production_db
from app.config import get_settings

settings = get_settings()


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "proximity-api"


def test_database_health_endpoint(client):
    response = client.get("/health/database")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "postgis_version" in data
    assert "3.5" in data["postgis_version"]


def test_safety_guard_blocks_production_database():
    mock_session = MagicMock()
    mock_session.execute.return_value.one.return_value = (settings.POSTGRES_DB,)
    with pytest.raises(RuntimeError, match="FATAL SAFETY VIOLATION"):
        assert_not_production_db(mock_session)


def test_settings_safe_repr_redacts_secrets():
    safe = settings.safe_repr()
    assert safe["POSTGRES_PASSWORD"] == "[REDACTED]"
    assert safe["SECRET_KEY"] == "[REDACTED]"
