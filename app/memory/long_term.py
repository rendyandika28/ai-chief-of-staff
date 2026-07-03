"""Long-term memory: SQLite FTS5 over conversation pairs, BM25-ranked."""

import re
from app.lib.database import Database


class LongTermMemory:
    def __init__(self, db_path: str = "memory/long_term.db"):
        self._db = Database(db_path)
        self._init()

    def _init(self):
        self._db.commit_sql("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
                user_id,
                user_message,
                assistant_response,
                tokenize='unicode61'
            )
        """)

    def add(self, user_id: str, user_message: str, assistant_response: str):
        self._db.commit_sql(
            "INSERT INTO memories (user_id, user_message, assistant_response) VALUES (?, ?, ?)",
            (user_id, user_message, assistant_response),
        )

    def search(self, user_id: str, query: str, k: int = 5) -> list[dict]:
        """Top-k past exchanges sharing any keyword with the query (FTS5 BM25)."""
        tokens = re.findall(r"\w+", query.lower())[:20]
        if not tokens:
            return []
        # ponytail: BM25 keyword recall; upgrade to embeddings if paraphrase misses hurt
        terms = " OR ".join(f'"{t}"' for t in tokens)
        match = f"{{user_message assistant_response}} : ({terms})"
        try:
            rows = self._db.fetch(
                """SELECT user_message, assistant_response
                   FROM memories
                   WHERE user_id = ? AND memories MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (user_id, match, k),
            )
        except Exception:
            return []
        return [{"user": r[0], "assistant": r[1]} for r in rows]
