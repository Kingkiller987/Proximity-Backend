# Proximity Backend — Privacy-Preserving Geofenced API

Server-authoritative FastAPI backend for Proximity, enforcing physical location verification, monotonic visit message isolation, and temporary event coordination.

## Quick Start

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start PostgreSQL 17 with PostGIS 3.5
docker compose up -d db

# 4. Apply database migrations
alembic upgrade head

# 5. Run automated test suite
pytest -v

# 6. Start the API development server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

---

## Technology Stack

- **Runtime & Framework:** Python 3.13, FastAPI, Pydantic v2
- **Database Engine:** PostgreSQL 17 + PostGIS 3.5 (`postgis/postgis:17-3.5`)
- **ORM & Migrations:** SQLAlchemy 2.0 (with `psycopg` v3 binary driver), Alembic
- **Security & Cryptography:** Argon2id (`argon2-cffi`), PyJWT, HMAC-SHA256
- **Testing:** Pytest, `pytest-asyncio`, Starlette TestClient, HTTPX

---

## Environment Configuration

Copy the template to `.env` and adjust settings for your local development environment:

```bash
cp .env.example .env
```

### Key Configuration Variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `ENV` | `development` | Application mode (`development` or `production`). |
| `POSTGRES_HOST` | `127.0.0.1` | Database host (bound to localhost). |
| `POSTGRES_PORT` | `5432` | Database port. |
| `POSTGRES_USER` | `proximity` | PostgreSQL username. |
| `POSTGRES_PASSWORD` | `proximity_local_dev_password` | PostgreSQL password. |
| `POSTGRES_DB` | `proximity` | Development database name. |
| `POSTGRES_TEST_DB` | `proximity_test` | Isolated test database name. |
| `SECRET_KEY` | *(dev placeholder)* | JWT signing secret (**must be rotated in production**). |
| `MEDIA_STORAGE_DIR` | `./storage/media` | Filesystem path for protected media storage. |
| `MAX_LOCATION_AGE_SECONDS`| `30` | Maximum allowed GPS fix age before rejection. |
| `MAX_GPS_ACCURACY_METERS` | `50.0` | Maximum allowed GPS accuracy radius in meters. |

> **Security Notice:** Never commit `.env` to version control. In production environments, set `ENV=production` and generate a cryptographically strong 64-character signing secret for `SECRET_KEY`.

---

## Database Management & PostGIS

### Start Database Service
```bash
docker compose up -d db
```

### Apply Migrations
```bash
# Target development database
alembic upgrade head

# Target test database explicitly
alembic -x db=test upgrade head
```

The database schema includes native PostGIS `geography(Point, 4326)` columns with GiST spatial indexes on `landmarks.location` and `events.venue_location`.

---

## Running Tests Safely

All automated tests require an isolated test database with PostGIS enabled:

```bash
pytest -v
```

### Test Isolation Guarantee
- The test harness ([`tests/conftest.py`](tests/conftest.py)) enforces a strict safety guard preventing any connection to the development database `proximity`.
- Every test runs inside a rolled-back transaction (`connection.begin() ... transaction.rollback()`), ensuring zero data contamination across test runs.

---

## Core Architecture & Security Invariants

1. **Server-Authoritative Location Validation:**
   All landmark and event interactions require verified client location evidence (`latitude`, `longitude`, `accuracy_meters`, `timestamp`). Fixes older than 30s or with accuracy exceeding 50m are rejected.
2. **Monotonic Landmark Visit Isolation:**
   Landmark chat messages are strictly bounded to active visit sessions (`created_at >= entered_at`, `sequence >= min_message_sequence`). Leaving or timing out terminates the visit; upon re-entry, previous messages are unreachable.
3. **Pessimistic Sequence Locking:**
   Message sequence allocation uses PostgreSQL row-level locks (`SELECT ... FOR UPDATE`) on `channel_sequences` to ensure strictly ascending message ordering under concurrent load.
4. **Two-Phase Host Handoff:**
   Event host transfers require in-radius host nomination followed by in-radius recipient acceptance, atomically verified under database row locks.
5. **Ghost Mode & Privacy:**
   Enabling ghost mode immediately terminates active landmark visits and suppresses discovery in nearby queries.

---

## Implemented API Endpoints

| Router | Prefix | Key Endpoints | Description |
| :--- | :--- | :--- | :--- |
| **Auth** | `/auth` | `POST /register`, `POST /login`, `POST /refresh`, `POST /logout` | Argon2id password hashing, JWT access tokens, rotating refresh tokens. |
| **Users** | `/users` | `GET /me`, `GET /{id}`, `POST /{id}/block`, `DELETE /{id}/block` | Profile management, anonymous projection, bidirectional blocking. |
| **Landmarks** | `/landmarks` | `GET /`, `POST /{id}/enter`, `POST /{id}/leave`, `GET/POST /{id}/messages` | Geofenced POI discovery, visit isolation, message sequences. |
| **Events** | `/events` | `GET /`, `POST /`, `POST /{id}/start`, `POST /{id}/transfer-host`, `POST /{id}/accept-host` | Temporary events, venue schedules, two-phase host handoff. |
| **Media** | `/media` | `POST /upload`, `POST /{id}/token`, `GET /{id}` | Magic-byte MIME verification, 5MB ceiling, authorized download tokens. |
| **Nearby** | `/nearby` | `POST /participants` | Coarse distance projection with ghost mode and block suppression. |
| **Health** | `/health` | `GET /health`, `GET /health/database` | Application and PostGIS database readiness probes. |

---

## Scope & Deferred Features

- **Direct Messaging (DM):** The channel sequence infrastructure supports DM identifiers, but user-to-user conversation pairing tables are deferred in the initial schema. Uploading media for DM channels returns `HTTP 501 Not Implemented`.
