"""Lessons — free-text behavioral guidance distilled from Rendy's corrections and
preferences ("kepanjangan", "lain kali to the point", "gua lebih suka X").

Episodic-lite: instead of recording every task+outcome (unreliable to grade in
chat), we capture the clear negative/preference signal — a correction — as a
one-line lesson, embed it, and surface relevant ones back into the system prompt
so the bot visibly learns Rendy's style over time. Distinct from KG facts (SPO
triples): lessons are procedures, not structured attributes.
"""

import re
import sqlite3
from datetime import datetime

from app.lib.database import Database
from app.lib.vectors import to_blob, from_blob, cosine_topk

CAP = 200  # keep the newest N; corrections are rare so this rarely bites


class Lessons:
    def __init__(self, db_path: str = "memory/lessons.db", embedder=None):
        self._db = Database(db_path)
        self._embedder = embedder
        self._init()

    def _init(self):
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                embedding BLOB
            )
        """)
        self._db.commit_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_lesson_unique ON lessons(user_id, text)")

    def add(self, user_id: str, text: str):
        text = (text or "").strip()
        if not text:
            return
        if self._db.fetch("SELECT 1 FROM lessons WHERE user_id=? AND text=? LIMIT 1",
                          (user_id, text)):
            return  # dedup exact
        emb = None
        if self._embedder:
            vecs = self._embedder.embed([text], task_type="RETRIEVAL_DOCUMENT")
            emb = to_blob(vecs[0]) if vecs else None
        self._db.commit_sql(
            "INSERT INTO lessons (user_id, text, created_at, embedding) VALUES (?, ?, ?, ?)",
            (user_id, text, datetime.now().isoformat(), emb))
        # trim to CAP newest
        self._db.commit_sql(
            "DELETE FROM lessons WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM lessons WHERE user_id=? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, CAP))

    def _recent(self, user_id: str, n: int = 3) -> list:
        return [r[0] for r in self._db.fetch(
            "SELECT text FROM lessons WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, n))]

    def _semantic(self, user_id: str, qvec, k: int, threshold: float) -> list:
        import numpy as np
        rows = self._db.fetch(
            "SELECT text, embedding FROM lessons WHERE user_id=? AND embedding IS NOT NULL",
            (user_id,))
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scored = []
        for text, emb in rows:
            if not emb:
                continue
            v = from_blob(emb)
            cos = float(v @ q / (np.linalg.norm(v) or 1.0))
            if cos >= threshold:
                scored.append((cos, text))
        scored.sort(reverse=True)
        return [t for _, t in scored[:k]]

    def _keyword(self, user_id: str, query: str, k: int) -> list:
        tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2][:6]
        if not tokens:
            return []
        clause = " OR ".join("text LIKE ?" for _ in tokens)
        rows = self._db.fetch(
            f"SELECT text FROM lessons WHERE user_id=? AND ({clause}) ORDER BY id DESC LIMIT ?",
            (user_id, *[f"%{t}%" for t in tokens], k))
        return [r[0] for r in rows]

    def for_context(self, user_id: str, qvec=None, query: str = "", k: int = 5) -> list:
        """Lessons to inject this turn: newest few (fresh/global prefs) + the ones
        most relevant to the current message. Deduped, small cap."""
        recent = self._recent(user_id, 3)
        # ponytail: threshold 0.35 — lessons are short/general; keep it loose
        rel = (self._semantic(user_id, qvec, k, 0.35) if qvec is not None
               else self._keyword(user_id, query, k))
        out = list(dict.fromkeys(recent + rel))  # recent first, dedup, preserve order
        return out[:6]

    def backfill_embeddings(self, batch: int = 100):
        if not self._embedder:
            return
        rows = self._db.fetch("SELECT id, text FROM lessons WHERE embedding IS NULL")
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            vecs = self._embedder.embed([t for _, t in chunk], task_type="RETRIEVAL_DOCUMENT")
            if not vecs:
                return
            for (lid, _), v in zip(chunk, vecs):
                self._db.commit_sql("UPDATE lessons SET embedding=? WHERE id=?",
                                    (to_blob(v), lid))


def _demo():
    """Self-check: python -m app.memory.lessons — no network."""
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

    db = os.path.join(tempfile.mkdtemp(), "lessons.db")
    L = Lessons(db, embedder=_FakeEmbedder())
    u = "u1"

    L.add(u, "Bot harus bikin brief ringkas, max 3 kalimat")
    L.add(u, "Bot harus bikin brief ringkas, max 3 kalimat")  # dedup
    assert len(L._recent(u, 10)) == 1, "exact dedup"

    L.add(u, "Bot harus panggil Rendy 'bro', jangan formal")
    ctx = L.for_context(u, qvec=None, query="tolong bikin brief dong")
    assert any("ringkas" in c for c in ctx), "keyword recall should surface the brief lesson"

    # semantic path: query vector close to the 'bro' lesson
    qvec = _FakeEmbedder().embed(["panggil bro jangan formal"])[0]
    ctx2 = L.for_context(u, qvec=qvec, query="zzz")
    assert any("bro" in c for c in ctx2), "semantic recall should surface the naming lesson"

    # empty add ignored
    L.add(u, "   ")
    assert len(L._recent(u, 10)) == 2, "blank lesson ignored"

    print("lessons self-check OK")


if __name__ == "__main__":
    _demo()
