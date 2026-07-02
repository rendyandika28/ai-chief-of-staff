import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def execute(self, sql: str, params=()):
        with sqlite3.connect(str(self._path)) as conn:
            return conn.execute(sql, params)

    def fetch(self, sql: str, params=()):
        with sqlite3.connect(str(self._path)) as conn:
            return conn.execute(sql, params).fetchall()

    def commit_sql(self, sql: str, params=()):
        with sqlite3.connect(str(self._path)) as conn:
            conn.execute(sql, params)
            conn.commit()

    def insert(self, sql: str, params=()) -> int:
        """Execute INSERT and return lastrowid in a single connection."""
        with sqlite3.connect(str(self._path)) as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
