"""Semantic long-term memory using SQLite FTS5 + TF-IDF-like retrieval.

Stores conversation pairs and retrieves the most relevant past conversations
for any new user message. Zero external dependencies beyond Python stdlib.
"""

import math
from collections import Counter
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
        """Return top-k relevant memories for a user. Uses FTS5 + TF-IDF reranking."""

        # ponytail: FTS5 simple query - no BM25, no vector DB, adequate for personal agent scale
        escaped = query.replace('"', '""')
        try:
            rows = self._db.fetch(
                """SELECT user_message, assistant_response, rank
                   FROM memories
                   WHERE user_id = ? AND memories MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (user_id, f'"{escaped}"', k * 3),
            )
        except Exception:
            rows = []

        if not rows:
            return []

        scored = []
        query_tokens = set(query.lower().split())
        idf = self._compute_idf(rows)

        for user_msg, asst_resp, _ in rows:
            doc_text = (user_msg + " " + asst_resp).lower()
            doc_tokens = doc_text.split()
            if not doc_tokens:
                continue
            tf = Counter(doc_tokens)
            score = sum(
                tf.get(t, 0) * idf.get(t, 1.0) for t in query_tokens
            )
            scored.append((score, {"user": user_msg, "assistant": asst_resp}))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k] if _ > 0]

    def _compute_idf(self, rows) -> dict:
        N = max(len(rows), 1)
        doc_freq = Counter()
        for user_msg, asst_resp, _ in rows:
            tokens = set((user_msg + " " + asst_resp).lower().split())
            for t in tokens:
                doc_freq[t] += 1
        return {t: math.log((N + 1) / (df + 1)) + 1 for t, df in doc_freq.items()}
