# Proximity Backend Authoritative Agent Instructions

## Workspace Boundaries
- **Authoritative Backend Host:** `/home/kingp/proximity-backend/` on Debian 13 (`kingp@192.168.86.189`).
  - Contains all backend application source code, Alembic migrations, database models, API routers, tests, and configuration.
  - Remote repository: `git@github.com:PaoloCalvacca/Proximity-Backend.git`.
- **Frontend Workspace:** `~/Documents/CodingStuff/Proximity/` (Mac) — **STRICTLY READ-ONLY**.
  - Never edit, format, generate, delete, stage, commit, or modify any file in this directory.
  - Never create a monorepo or submodule combining frontend and backend.
- **Local Agent Workspace:** `~/Documents/CodingStuff/Proximity-Backend-Agent/` (Mac).
  - Contains only agent plans, architecture decisions, and notes.

## Mandatory Continuous GitHub Synchronization
1. Every completed logical backend code change (feature, bugfix, refactor, migration, test, or docs) must be:
   - Tested against isolated `proximity_test` (all tests passing).
   - Reviewed via `git diff` for cleanliness and requirements compliance.
   - Scanned to prevent any secrets, tokens, or credentials from being staged.
   - Committed with a descriptive Conventional Commit message.
   - Pushed immediately to `origin/master`.
   - Verified on GitHub by checking remote commit SHA.
2. Never proceed to the next logical change without completing this sequence and confirming GitHub synchronization.
3. Maintain a 6-point synchronization ledger in every response involving changes.

## Security & Architectural Principles
1. **Server-Authoritative Enforcement:**
   - Location evidence (freshness <= 30s, accuracy <= 50m) must be verified on the server for all landmark and event interactions.
   - Landmark visit sessions are strictly isolative: users see only messages created during their active visit session (`created_at >= entered_at`, `sequence >= min_sequence`).
   - Leaving or stale fix terminates the visit session. Re-entry creates a new session; earlier messages are unreachable.
   - Protected media requires active authorized visit/event/DM token.
   - Event starting and handoff require verified in-radius presence of the authorized handler/recipient.
   - Ghost mode immediately terminates landmark visits and suppresses nearby discovery.
2. **Safety & Permission Rules:**
   - Never log, echo, commit, or expose secrets or `.env` contents.
   - Never run destructive database commands (`DROP DATABASE`, volume deletion) without explicit approval.
   - PostgreSQL is bound to localhost (`127.0.0.1:5432`) and must never be exposed publicly.
   - Tests run strictly against `proximity_test`. Never mutate or apply migrations to `proximity`.
   - Never claim a test or command succeeded unless observed.
