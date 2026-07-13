"""Long-term memory: SQLite FTS5 over conversation pairs, BM25-ranked, with an
optional semantic layer (Gemini embeddings) fused in via RRF.

FTS5 virtual tables can't take extra columns, so vectors live in a sidecar table
keyed by the FTS5 rowid.
ponytail: sidecar rowid is stable as long as the FTS5 table isn't rebuilt.
"""

import re
from app.lib.database import Database
from app.lib.vectors import to_blob, cosine_topk, rrf


class LongTermMemory:
    def __init__(self, db_path: str = "memory/long_term.db", embedder=None):
        self._db = Database(db_path)
        self._embedder = embedder
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
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS lt_vectors (
                rowid INTEGER PRIMARY KEY,
                embedding BLOB
            )
        """)

    def add(self, user_id: str, user_message: str, assistant_response: str):
        self._db.commit_sql(
            "INSERT INTO memories (user_id, user_message, assistant_response) VALUES (?, ?, ?)",
            (user_id, user_message, assistant_response),
        )
        emb = self._embed(f"{user_message} {assistant_response}")
        if emb is not None:
            # Each Database call uses its own connection, so last_insert_rowid()
            # won't carry over. Single-writer bot → the newest rowid is ours.
            # ponytail: max(rowid) assumes one writer; fine for this bot.
            rowid = self._db.fetch("SELECT max(rowid) FROM memories")[0][0]
            self._db.commit_sql(
                "INSERT OR REPLACE INTO lt_vectors (rowid, embedding) VALUES (?, ?)",
                (rowid, emb),
            )

    def _embed(self, text: str):
        if not self._embedder:
            return None
        vecs = self._embedder.embed([text], task_type="RETRIEVAL_DOCUMENT")
        return to_blob(vecs[0]) if vecs else None

    def _keyword_search(self, user_id: str, query: str, k: int) -> list[dict]:
        tokens = re.findall(r"\w+", query.lower())[:20]
        if not tokens:
            return []
        # ponytail: BM25 keyword recall; semantic layer below covers paraphrase misses
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

    def _semantic_search(self, user_id: str, qvec, k: int) -> list[dict]:
        rows = self._db.fetch(
            """SELECT m.user_message, m.assistant_response, v.embedding
               FROM memories m JOIN lt_vectors v ON m.rowid = v.rowid
               WHERE m.user_id = ?""",
            (user_id,),
        )
        hits = cosine_topk(qvec, rows, k=k)
        return [{"user": r[0], "assistant": r[1]} for r in hits]

    def search(self, user_id: str, query: str, qvec=None, k: int = 5) -> list[dict]:
        """Hybrid: BM25 ∪ semantic (if qvec), fused by RRF. qvec=None → BM25 only."""
        kw = self._keyword_search(user_id, query, k)
        if qvec is None:
            return kw
        sem = self._semantic_search(user_id, qvec, k)
        return rrf(kw, sem, key=lambda m: (m["user"], m["assistant"]))[:k]

    def backfill_embeddings(self, batch: int = 100):
        """One-shot: embed memories stored before the embedder was wired in."""
        if not self._embedder:
            return
        rows = self._db.fetch(
            """SELECT m.rowid, m.user_message, m.assistant_response
               FROM memories m LEFT JOIN lt_vectors v ON m.rowid = v.rowid
               WHERE v.rowid IS NULL""")
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            texts = [f"{u} {a}" for _, u, a in chunk]
            vecs = self._embedder.embed(texts, task_type="RETRIEVAL_DOCUMENT")
            if not vecs:
                return
            for (rid, *_), v in zip(chunk, vecs):
                self._db.commit_sql(
                    "INSERT OR REPLACE INTO lt_vectors (rowid, embedding) VALUES (?, ?)",
                    (rid, to_blob(v)))


def _demo():
    """Self-check: python -m app.memory.long_term — no network."""
    import tempfile, os
    import numpy as np

    class _FakeEmbedder:
        def embed(self, texts, task_type="RETRIEVAL_DOCUMENT"):
            out = []
            for t in texts:
                v = np.zeros(32, dtype=np.float32)
                for ch in t.lower():
                    v[ord(ch) % 32] += 1.0
                out.append(v)
            return out

    d = tempfile.mkdtemp()
    lt = LongTermMemory(os.path.join(d, "lt.db"), embedder=_FakeEmbedder())
    u = "u1"
    lt.add(u, "gua lagi bangun AI chief of staff", "mantap, semangat")
    lt.add(u, "cuaca hari ini gimana", "cerah")

    # keyword still works
    assert lt.search(u, "AI chief"), "keyword recall"

    # semantic: query vector close to the 'AI chief' row, no shared query string
    qvec = _FakeEmbedder().embed(["bangun AI chief of staff"])[0]
    hits = lt.search(u, "zzzz", qvec=qvec)
    assert any("chief" in h["user"] for h in hits), "semantic recall should surface it"

    # embedder off → BM25 only, no crash
    lt2 = LongTermMemory(os.path.join(d, "lt2.db"))
    lt2.add(u, "test message", "reply")
    assert lt2.search(u, "test") and lt2.search(u, "test", qvec=None) is not None

    print("long_term self-check OK")


if __name__ == "__main__":
    _demo()
