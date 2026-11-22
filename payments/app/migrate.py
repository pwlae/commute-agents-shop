"""Apply migrations/NNNN_name.up.sql at start-up.

Uses the same bookkeeping table as golang-migrate
(schema_migrations: one row with version and dirty), so the
`migrate` CLI can take over the same database at any time.
"""
import logging
import re
from pathlib import Path

log = logging.getLogger("payments.migrate")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
_NAME = re.compile(r"^(\d+)_(.+)\.up\.sql$")
_LOCK_ID = 7_340_001


def pending(directory=MIGRATIONS):
    files = []
    for p in directory.iterdir():
        m = _NAME.match(p.name)
        if m:
            files.append((int(m.group(1)), p))
    return sorted(files)


def up(conn, directory=MIGRATIONS):
    """Apply every migration newer than the recorded version."""
    conn.autocommit = True
    conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version bigint NOT NULL PRIMARY KEY,"
            " dirty boolean NOT NULL)")
        row = conn.execute(
            "SELECT version, dirty FROM schema_migrations").fetchone()
        current = 0
        if row:
            current, dirty = row
            if dirty:
                raise RuntimeError(
                    f"database is dirty at version {current}")
        applied = 0
        for version, path in pending(directory):
            if version <= current:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("DELETE FROM schema_migrations")
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (%s, false)",
                    (version,))
            log.info("applied migration %s", path.name)
            applied += 1
        return applied
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
