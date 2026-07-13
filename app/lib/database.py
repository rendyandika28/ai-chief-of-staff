import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        """WAL + busy_timeout so concurrent watcher/scheduler/main threads don't
        hit 'database is locked'. WAL persists on the file; busy_timeout is
        per-connection (wait up to 5s for a lock instead of erroring)."""
        conn = sqlite3.connect(str(self._path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def fetch(self, sql: str, params=()):
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def commit_sql(self, sql: str, params=()):
        with self._connect() as conn:
            conn.execute(sql, params)
            conn.commit()
