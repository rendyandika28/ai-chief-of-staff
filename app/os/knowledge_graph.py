"""Knowledge graph — subject-predicate-object triple store with confidence scoring
and automatic cleanup of stale facts."""

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from app.lib.database import Database
from app.lib.vectors import to_blob, cosine_topk, rrf

WIB = timezone(timedelta(hours=7))

# Controlled vocabulary — extractor/remember must emit these. First seven are the
# predicates app.py:_find_stale_topic hunts for, so they're what wakes the
# stale-topic nudge. Anything outside this set is dropped (anti-pollution).
VALID_PREDICATES = {
    "working_on", "building", "project", "deadline", "goal", "planning", "learning",
    "works_at", "role_is", "prefers", "dislikes", "uses", "knows",
    "located_in", "contact_is",
}
# Predicates that hold ONE current value — a new object supersedes the old.
SINGLE_VALUED = {"works_at", "role_is", "located_in", "contact_is"}


class KnowledgeGraph:
    def __init__(self, db_path: str = "memory/knowledge.db", embedder=None):
        self._db = Database(db_path)
        self._embedder = embedder
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
        # Migration: add embedding column if missing (idempotent).
        try:
            self._db.commit_sql("ALTER TABLE facts ADD COLUMN embedding BLOB")
        except sqlite3.OperationalError:
            pass  # column already exists

    def store_facts(self, user_id: str, facts: list):
        """Single entry point for extracted/remembered facts: guard vocabulary,
        supersede single-valued contradictions, then upsert (+embed). Never raises."""
        for f in facts or []:
            if not isinstance(f, dict):
                continue
            s = (f.get("subject") or "Rendy").strip()
            p = (f.get("predicate") or "").strip().lower()
            o = (f.get("object") or "").strip()
            if not (s and o) or p not in VALID_PREDICATES:
                continue  # malformed or out-of-vocab
            if p in SINGLE_VALUED:
                self._supersede(user_id, s, p, o)
            self.upsert(user_id, s, p, o, confidence=0.8)

    def _supersede(self, user_id: str, subject: str, predicate: str, new_obj: str):
        """Single-valued predicate: drop prior values that differ from new_obj.

        ponytail: hard DELETE — current truth is what nudges/briefs read, not
        history. Switch to a status='superseded' column if history is ever needed.
        ponytail: exact object match; add fuzzy compare if near-dupes pile up.
        """
        self._db.commit_sql(
            "DELETE FROM facts WHERE user_id=? AND subject=? AND predicate=? AND object<>?",
            (user_id, subject, predicate, new_obj),
        )

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
            )  # text unchanged → keep existing embedding, no re-embed
        else:
            emb = self._embed_fact(subject, predicate, obj)
            self._db.commit_sql(
                "INSERT INTO facts (user_id, subject, predicate, object, confidence, source, created_at, updated_at, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, subject, predicate, obj, confidence, source, now, now, emb),
            )

    def _embed_fact(self, subject: str, predicate: str, obj: str):
        """Embed one fact string → BLOB, or None if embedder off/failing."""
        if not self._embedder:
            return None
        vecs = self._embedder.embed(
            [f"{subject} {predicate.replace('_', ' ')} {obj}"],
            task_type="RETRIEVAL_DOCUMENT")
        return to_blob(vecs[0]) if vecs else None

    def about(self, user_id: str, subject: str) -> list:
        rows = self._db.fetch(
            "SELECT predicate, object, confidence FROM facts WHERE user_id = ? AND subject = ? ORDER BY confidence DESC",
            (user_id, subject),
        )
        return [{"subject": subject, "predicate": r[0], "object": r[1], "confidence": r[2]} for r in rows]

    def _keyword_search(self, user_id: str, query: str) -> list:
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

    def _semantic_search(self, user_id: str, qvec) -> list:
        rows = self._db.fetch(
            "SELECT subject, predicate, object, confidence, embedding FROM facts "
            "WHERE user_id = ? AND confidence >= 0.3 AND embedding IS NOT NULL",
            (user_id,),
        )
        hits = cosine_topk(qvec, rows, k=20)
        return [{"subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3]} for r in hits]

    def search(self, user_id: str, query: str, qvec=None) -> list:
        """Hybrid: keyword ∪ semantic (if qvec), fused by RRF and deduped by SPO.
        qvec=None → keyword only (embedder off / trivial query)."""
        kw = self._keyword_search(user_id, query)
        if qvec is None:
            return kw
        sem = self._semantic_search(user_id, qvec)
        return rrf(kw, sem, key=lambda f: (f["subject"], f["predicate"], f["object"]))

    def context_for(self, user_id: str, query: str, qvec=None, k: int = 5) -> str:
        facts = self.search(user_id, query, qvec)[:k]
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

    def backfill_embeddings(self, batch: int = 100):
        """One-shot: embed facts stored before the embedder was wired in.
        No-op when embedder off. Runs on startup; cheap (batched, only NULLs)."""
        if not self._embedder:
            return
        rows = self._db.fetch(
            "SELECT id, subject, predicate, object FROM facts WHERE embedding IS NULL")
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            texts = [f"{s} {p.replace('_', ' ')} {o}" for _, s, p, o in chunk]
            vecs = self._embedder.embed(texts, task_type="RETRIEVAL_DOCUMENT")
            if not vecs:
                return  # embedder failing → leave NULLs, retry next start
            for (fid, *_), v in zip(chunk, vecs):
                self._db.commit_sql(
                    "UPDATE facts SET embedding = ? WHERE id = ?", (to_blob(v), fid))


def _demo():
    """Self-check: python -m app.os.knowledge_graph — no network."""
    import tempfile, os
    import numpy as np

    class _FakeEmbedder:
        """Deterministic bag-of-chars vector so cosine is stable offline."""
        enabled = True

        def embed(self, texts, task_type="RETRIEVAL_DOCUMENT"):
            out = []
            for t in texts:
                v = np.zeros(32, dtype=np.float32)
                for ch in t.lower():
                    v[ord(ch) % 32] += 1.0
                out.append(v)
            return out

    db = os.path.join(tempfile.mkdtemp(), "kg.db")
    kg = KnowledgeGraph(db, embedder=_FakeEmbedder())
    u = "u1"

    # out-of-vocab predicate dropped
    kg.store_facts(u, [{"subject": "Rendy", "predicate": "vibes_with", "object": "kopi"}])
    assert kg.about(u, "Rendy") == [], "out-of-vocab predicate must be dropped"

    # single-valued supersede: works_at X then Y → only Y survives
    kg.store_facts(u, [{"subject": "Rendy", "predicate": "works_at", "object": "PT X"}])
    kg.store_facts(u, [{"subject": "Rendy", "predicate": "works_at", "object": "PT Y"}])
    workplaces = [f["object"] for f in kg.about(u, "Rendy") if f["predicate"] == "works_at"]
    assert workplaces == ["PT Y"], f"works_at should supersede to PT Y, got {workplaces}"

    # multi-valued accumulates
    kg.store_facts(u, [{"subject": "Rendy", "predicate": "uses", "object": "Python"}])
    kg.store_facts(u, [{"subject": "Rendy", "predicate": "uses", "object": "React"}])
    uses = {f["object"] for f in kg.about(u, "Rendy") if f["predicate"] == "uses"}
    assert uses == {"Python", "React"}, f"uses should accumulate, got {uses}"

    # semantic search returns a hit even with a paraphrase-y query (no keyword overlap)
    qvec = _FakeEmbedder().embed(["React"])[0]
    hit = kg.search(u, "zzz no keyword overlap zzz", qvec=qvec)
    assert any(f["object"] == "React" for f in hit), "semantic recall should find React"

    # embedder off → keyword still works, no crash
    kg2 = KnowledgeGraph(os.path.join(tempfile.mkdtemp(), "kg2.db"))
    kg2.store_facts(u, [{"subject": "Rendy", "predicate": "building", "object": "AI chief"}])
    assert kg2.search(u, "AI chief") and kg2.search(u, "AI chief", qvec=None) is not None

    print("knowledge_graph self-check OK")


if __name__ == "__main__":
    _demo()
