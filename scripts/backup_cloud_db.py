#!/usr/bin/env python3
"""Database Backup, Export, and Restoration Verification Utility for Proximity.

Creates a SQL export of the target database before schema migrations,
and supports dry-run restoration into an isolated test database to verify backup integrity.
"""

import os
import sys
import argparse
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse


def parse_args():
    parser = argparse.ArgumentParser(description="Backup or verify restore of Proximity database.")
    parser.add_argument(
        "--db-url",
        type=str,
        default=os.getenv("DATABASE_URL"),
        help="Target PostgreSQL connection URL",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups"),
        help="Directory where SQL backup exports are saved",
    )
    parser.add_argument(
        "--verify-restore-url",
        type=str,
        default=None,
        help="Optional isolated test database URL to test restoring the backup into",
    )
    return parser.parse_args()


def export_backup(db_url: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    clean_url = db_url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlparse(clean_url)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    export_filename = f"proximity_backup_{timestamp}.sql"
    export_path = os.path.join(output_dir, export_filename)

    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "127.0.0.1",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", parsed.path.lstrip("/") or "postgres",
        "--no-owner",
        "--no-acl",
        "-f", export_path,
    ]

    print(f"[INFO] Exporting database backup to {export_path}...")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr}")

    size = os.path.getsize(export_path)
    print(f"[SUCCESS] Backup completed. File size: {size} bytes ({export_path})")
    return export_path


def verify_restore(backup_path: str, restore_db_url: str):
    print(f"[INFO] Verifying restore against isolated database...")
    clean_url = restore_db_url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlparse(clean_url)

    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    cmd = [
        "psql",
        "-h", parsed.hostname or "127.0.0.1",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", parsed.path.lstrip("/") or "postgres",
        "-f", backup_path,
    ]

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"psql restore failed: {result.stderr}")

    print(f"[SUCCESS] Restoration test passed. Backup is verifiable and recoverable.")


def main():
    args = parse_args()
    if not args.db_url:
        print("[ERROR] DATABASE_URL must be supplied via --db-url or env var.", file=sys.stderr)
        sys.exit(1)

    try:
        backup_file = export_backup(args.db_url, args.output_dir)
        if args.verify_restore_url:
            verify_restore(backup_file, args.verify_restore_url)
    except Exception as exc:
        print(f"[FATAL] Backup operation failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
