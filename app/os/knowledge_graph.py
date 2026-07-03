"""Knowledge graph — subject-predicate-object triple store with confidence scoring
and automatic cleanup of stale facts."""

import re
from datetime import datetime, timedelta, timezone

from app.lib.database import Database

WIB = timezone(timedelta(hours=7))


class KnowledgeGraph:
    def __init__(self, db_path: str = "memory/knowledge.db"):
        self._db = Database(db_path)
        self._init()

    def _init(self):
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT DEFAULT 'conversation',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._db.commit_sql("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_unique
            ON facts(user_id, subject, predicate, object)
        """)

    def upsert(self, user_id: str, subject: str, predicate: str, obj: str,
               confidence: float = 1.0, source: str = "conversation"):
        now = datetime.now().isoformat()
        existing = self._db.fetch(
            "SELECT id, confidence FROM facts WHERE user_id = ? AND subject = ? AND predicate = ? AND object = ?",
            (user_id, subject, predicate, obj),
        )
        if existing:
            new_conf = min(existing[0][1] + confidence * 0.3, 1.0)
            self._db.commit_sql(
                "UPDATE facts SET confidence = ?, updated_at = ? WHERE id = ?",
                (new_conf, now, existing[0][0]),
            )
        else:
            self._db.commit_sql(
                "INSERT INTO facts (user_id, subject, predicate, object, confidence, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, subject, predicate, obj, confidence, source, now, now),
            )

    def about(self, user_id: str, subject: str) -> list:
        rows = self._db.fetch(
            "SELECT predicate, object, confidence FROM facts WHERE user_id = ? AND subject = ? ORDER BY confidence DESC",
            (user_id, subject),
        )
        return [{"subject": subject, "predicate": r[0], "object": r[1], "confidence": r[2]} for r in rows]

    def search(self, user_id: str, query: str) -> list:
        tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2][:8]
        if not tokens:
            return []
        clause = " OR ".join(
            "(subject LIKE ? OR predicate LIKE ? OR object LIKE ?)" for _ in tokens
        )
        params = [user_id]
        for t in tokens:
            params += [f"%{t}%"] * 3
        rows = self._db.fetch(
            f"SELECT subject, predicate, object, confidence FROM facts "
            f"WHERE user_id = ? AND confidence >= 0.3 AND ({clause}) "
            f"ORDER BY confidence DESC LIMIT 20",
            tuple(params),
        )
        return [{"subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3]} for r in rows]

    def context_for(self, user_id: str, query: str, k: int = 5) -> str:
        facts = self.search(user_id, query)[:k]
        if not facts:
            return ""
        lines = ["Fakta yang diketahui:"]
        for f in facts:
            pred = f["predicate"].replace("_", " ")
            lines.append(f"  - {f['subject']} {pred} {f['object']}")
        return "\n".join(lines)

    def cleanup(self):
        """Remove stale facts. Called periodically to prevent memory bloat."""
        now = datetime.now(WIB)

        # Decay confidence for facts not updated in 7+ days
        cutoff = (now - timedelta(days=7)).isoformat()
        self._db.commit_sql(
            "UPDATE facts SET confidence = ROUND(confidence * 0.7, 2), updated_at = ? "
            "WHERE updated_at < ? AND confidence > 0.2",
            (now.isoformat(), cutoff),
        )

        # Delete facts with confidence < 0.2 (forgotten)
        self._db.commit_sql("DELETE FROM facts WHERE confidence < 0.2")

        # Delete facts older than 30 days with low confidence
        stale = (now - timedelta(days=30)).isoformat()
        self._db.commit_sql(
            "DELETE FROM facts WHERE created_at < ? AND confidence < 0.6",
            (stale,),
        )
