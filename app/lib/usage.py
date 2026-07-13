"""Token/cost accounting — records every Claude call, aggregates per day.

Standalone (stdlib only) like events.py, so the dashboard container can import
it to render the spend card without pulling in bot deps.
"""

import os
import sqlite3
from pathlib import Path

# USD per token (input, output). Update if Anthropic pricing changes.
# Sonnet 5: $3/$15 per MTok. Haiku 4.5: $1/$5 per MTok. Matched by prefix so
# dated model ids (claude-haiku-4-5-20251001) still resolve.
_PRICES = {
    "claude-sonnet-5": (3.0 / 1e6, 15.0 / 1e6),
    "claude-haiku-4-5": (1.0 / 1e6, 5.0 / 1e6),
    "claude-opus-4-8": (5.0 / 1e6, 25.0 / 1e6),
}


def _price(model: str):
    for prefix, rates in _PRICES.items():
        if (model or "").startswith(prefix):
            return rates
    return (0.0, 0.0)  # unknown model → count tokens, no $ estimate


def _db_path() -> str:
    return os.path.join(os.getenv("MEMORY_DIR", "memory"), "usage.db")


def _conn() -> sqlite3.Connection:
    p = _db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=5)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute(
        "CREATE TABLE IF NOT EXISTS usage ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts TEXT DEFAULT CURRENT_TIMESTAMP, "  # UTC
        "model TEXT NOT NULL, in_tokens INTEGER, out_tokens INTEGER)"
    )
    return c


def record(model: str, in_tokens: int, out_tokens: int):
    # ponytail: accounting must never break a turn — swallow everything
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO usage (model, in_tokens, out_tokens) VALUES (?, ?, ?)",
                (model, int(in_tokens or 0), int(out_tokens or 0)))
            c.commit()
    except Exception:
        pass


def today() -> dict:
    """Aggregate today's (UTC) usage: tokens in/out + estimated USD cost."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT model, SUM(in_tokens), SUM(out_tokens) FROM usage "
                "WHERE ts >= date('now') GROUP BY model").fetchall()
    except Exception:
        return {"in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0, "by_model": {}}
    tin = tout = 0
    cost = 0.0
    by_model = {}
    for model, i, o in rows:
        i, o = int(i or 0), int(o or 0)
        pin, pout = _price(model)
        c_usd = i * pin + o * pout
        tin += i
        tout += o
        cost += c_usd
        by_model[model] = {"in": i, "out": o, "cost_usd": round(c_usd, 4)}
    return {"in_tokens": tin, "out_tokens": tout,
            "cost_usd": round(cost, 4), "by_model": by_model}


if __name__ == "__main__":
    import tempfile
    os.environ["MEMORY_DIR"] = tempfile.mkdtemp()
    record("claude-sonnet-5", 1_000_000, 1_000_000)      # $3 + $15 = $18
    record("claude-haiku-4-5-20251001", 1_000_000, 0)    # $1 (dated id → prefix match)
    t = today()
    assert t["in_tokens"] == 2_000_000, t
    assert abs(t["cost_usd"] - 19.0) < 1e-6, t["cost_usd"]  # 18 + 1
    print("usage selfcheck: OK", t["cost_usd"])
