from fastapi.testclient import TestClient
from app.main import app
from app.config import Settings


def test_health_ready_probe(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"
    assert data["postgis"] == "available"


def test_database_url_sslmode_handling():
    s = Settings(DATABASE_URL="postgres://user:pass@db.example.com:5432/postgres?sslmode=require")
    url = s.get_database_url()
    assert url.startswith("postgresql+psycopg://")
    assert "sslmode=require" in url


def test_settings_safe_repr_redacts_s3():
    s = Settings(
        S3_ACCESS_KEY_ID="my_key",
        S3_SECRET_ACCESS_KEY="super_secret",
        DATABASE_URL="postgres://u:p@h:5432/d",
    )
    safe = s.safe_repr()
    assert safe["S3_SECRET_ACCESS_KEY"] == "[REDACTED]"
    assert safe["DATABASE_URL"] == "[REDACTED]"
