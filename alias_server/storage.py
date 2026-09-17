"""SQLite transactions and consistent backups, without a database daemon."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3


class AliasStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        with closing(self.connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS company_aliases (
                    alias_key TEXT PRIMARY KEY NOT NULL CHECK (trim(alias_key) <> ''),
                    cleaned_alias TEXT NOT NULL CHECK (trim(cleaned_alias) <> ''),
                    canonical_name TEXT NOT NULL CHECK (trim(canonical_name) <> ''),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def check(self):
        with closing(self.connect()) as connection:
            connection.execute("SELECT alias_key FROM company_aliases LIMIT 1").fetchone()

    def list_aliases(self):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(
                "SELECT cleaned_alias, alias_key, canonical_name FROM company_aliases ORDER BY alias_key"
            )]

    def upsert_aliases(self, rows):
        with closing(self.connect()) as connection, connection:
            connection.executemany("""
                INSERT INTO company_aliases (alias_key, cleaned_alias, canonical_name)
                VALUES (:alias_key, :cleaned_alias, :canonical_name)
                ON CONFLICT(alias_key) DO UPDATE SET
                    cleaned_alias = excluded.cleaned_alias,
                    canonical_name = excluded.canonical_name,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """, rows)


def backup_database(database: Path, directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.now(timezone.utc)
    target = directory / f"aliases-{now.strftime('%Y%m%dT%H%M%S%fZ')}.sqlite3"
    temporary = target.with_suffix(".partial")
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
                destination.execute("SELECT alias_key FROM company_aliases LIMIT 1")
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    cutoff = (now - timedelta(days=14)).timestamp()
    for old in directory.glob("aliases-*.sqlite3"):
        if old.is_file() and old.stat().st_mtime < cutoff:
            old.unlink()
    return target


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Create a consistent SQLite alias backup")
    parser.add_argument("database", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(backup_database(args.database, args.directory))
