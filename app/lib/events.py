"""Lightweight activity log — 'what the agent is doing'. Standalone (stdlib only)
so the dashboard container can import it without pulling in bot deps."""

import os
import sqlite3
from pathlib import Path


def _db_path() -> str:
    return os.path.join(os.getenv("MEMORY_DIR", "memory"), "events.db")


def _conn() -> sqlite3.Connection:
    p = _db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=5)
    c.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts TEXT DEFAULT CURRENT_TIMESTAMP, "  # UTC
        "kind TEXT NOT NULL, "
        "detail TEXT DEFAULT '')"
    )
    return c


def log_event(kind: str, detail: str = ""):
    # ponytail: logging jangan pernah bikin bot mati — telen semua error
    try:
        with _conn() as c:
            c.execute("INSERT INTO events (kind, detail) VALUES (?, ?)", (kind, (detail or "")[:500]))
            c.commit()
    except Exception:
        pass


def recent(limit: int = 60) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT ts, kind, detail FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"ts": r[0], "kind": r[1], "detail": r[2]} for r in rows]
    except Exception:
        return []


if __name__ == "__main__":
    import tempfile

    os.environ["MEMORY_DIR"] = tempfile.mkdtemp()
    log_event("tool", "polymarket: bitcoin")
    log_event("proactive", "Reminder terkirim")
    r = recent()
    assert len(r) == 2, r
    assert r[0]["kind"] == "proactive" and r[1]["detail"].startswith("polymarket"), r
    print("events selfcheck: OK")
