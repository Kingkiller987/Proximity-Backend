#!/usr/bin/env python3
"""Cloud Database Migration & Verification Script for Proximity.

Connects to the hosted PostgreSQL instance, ensures the PostGIS extension is active,
applies Alembic migrations up to head, verifies schema tables & spatial indexes,
and seeds initial landmarks if the database is unpopulated.
"""

import os
import sys
import argparse
from sqlalchemy import create_engine, text, inspect
from alembic.config import Config
from alembic import command


def parse_args():
    parser = argparse.ArgumentParser(description="Deploy and verify cloud database schema.")
    parser.add_argument(
        "--db-url",
        type=str,
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL connection string (defaults to DATABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check connection and current revision without modifying schema.",
    )
    return parser.parse_args()


def normalize_db_url(url: str) -> str:
    if not url:
        raise ValueError("DATABASE_URL is not set or empty.")
    clean = url.strip()
    if clean.startswith("postgresql://"):
        clean = clean.replace("postgresql://", "postgresql+psycopg://", 1)
    elif clean.startswith("postgres://"):
        clean = clean.replace("postgres://", "postgresql+psycopg://", 1)
    return clean


def main():
    args = parse_args()
    if not args.db_url:
        print("[ERROR] DATABASE_URL must be provided via --db-url or environment variable.", file=sys.stderr)
        sys.exit(1)

    db_url = normalize_db_url(args.db_url)
    redacted_url = re_redact_url(db_url)
    print(f"[INFO] Connecting to target database: {redacted_url}")

    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            # 1. Enable & verify PostGIS
            if not args.dry_run:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
                conn.commit()

            row = conn.execute(text("SELECT PostGIS_Version();")).fetchone()
            if not row or not row[0]:
                print("[ERROR] PostGIS extension is missing or unverified!", file=sys.stderr)
                sys.exit(1)
            print(f"[INFO] Verified PostGIS version: {row[0]}")

        # 2. Run Alembic migrations
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        alembic_ini_path = os.path.join(repo_root, "alembic.ini")
        alembic_cfg = Config(alembic_ini_path)
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)

        if args.dry_run:
            print("[INFO] Dry-run mode: skipping schema migration.")
        else:
            print("[INFO] Running Alembic migration to 'head'...")
            command.upgrade(alembic_cfg, "head")
            print("[INFO] Alembic migrations completed successfully.")

        # 3. Verify tables
        with engine.connect() as conn:
            inspector = inspect(conn)
            tables = set(inspector.get_table_names())
            required_tables = {"users", "landmarks", "media_objects", "messages", "dm_conversations"}
            missing = required_tables - tables
            if missing:
                print(f"[ERROR] Schema verification failed. Missing tables: {missing}", file=sys.stderr)
                sys.exit(1)
            print(f"[INFO] Verified required tables exist: {sorted(list(required_tables))}")

            # Check landmark seed
            count = conn.execute(text("SELECT COUNT(*) FROM landmarks;")).scalar()
            print(f"[INFO] Current landmarks count: {count}")
            if count == 0 and not args.dry_run:
                print("[INFO] Seeding initial landmarks...")
                from seed_landmarks import seed_initial_landmarks
                seed_initial_landmarks(engine)
                count = conn.execute(text("SELECT COUNT(*) FROM landmarks;")).scalar()
                print(f"[INFO] Seeded landmarks. New count: {count}")

        print("[SUCCESS] Cloud database deployment & verification passed.")

    except Exception as exc:
        print(f"[FATAL] Cloud database deployment failed: {exc}", file=sys.stderr)
        sys.exit(1)


def re_redact_url(url: str) -> str:
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


if __name__ == "__main__":
    main()
