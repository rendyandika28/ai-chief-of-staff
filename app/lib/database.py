import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def fetch(self, sql: str, params=()):
        with sqlite3.connect(str(self._path)) as conn:
            return conn.execute(sql, params).fetchall()

    def commit_sql(self, sql: str, params=()):
        with sqlite3.connect(str(self._path)) as conn:
            conn.execute(sql, params)
            conn.commit()
