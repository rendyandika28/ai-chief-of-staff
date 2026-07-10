"""Memory consolidation — periodically compact old raw chat into a compact,
still-relevant digest stored in long-term memory, then drop the raw turns.
Keeps conversations.db bounded without losing durable memory."""

from datetime import datetime, timedelta, timezone

from app.lib.events import log_event

WINDOW_DAYS = 30     # compact chat older than this
MIN_BATCH = 40       # only bother when enough old turns piled up (~monthly)

_DIGEST_PROMPT = (
    "Ini transkrip obrolan lama Rendy sama asistennya. Ringkes jadi memory PADAT "
    "yang MASIH relevan buat Rendy ke depan: fakta penting, keputusan, preferensi, "
    "konteks proyek/kerjaan yang berkelanjutan, orang/hal yang kesebut berulang.\n\n"
    "Buang basa-basi, smalltalk, hal yang udah kelar/basi, dan info yang gak berguna lagi. "
    "Format bullet ringkas, bahasa Indonesia. Kalau gak ada yang layak disimpen, balas kosong."
)


class MemoryConsolidator:
    def __init__(self, memory, long_term, llm,
                 window_days: int = WINDOW_DAYS, min_batch: int = MIN_BATCH):
        self._memory = memory
        self._long_term = long_term
        self._llm = llm  # fast_llm (Haiku)
        self._window = window_days
        self._min_batch = min_batch

    def run(self, user_id: str):
        """Compact old raw chat into one long-term digest, then delete the raw rows.
        Best-effort: on any failure, raw rows are NOT deleted (no data loss)."""
        try:
            # SQLite CURRENT_TIMESTAMP is UTC — compare in the same format.
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self._window)
                      ).strftime("%Y-%m-%d %H:%M:%S")
            rows = self._memory.older_than(user_id, cutoff)
            if len(rows) < self._min_batch:
                return None

            transcript = "\n".join(f"{role}: {content}" for _, role, content in rows)
            summary = self._llm.chat(
                [{"role": "system", "content": _DIGEST_PROMPT},
                 {"role": "user", "content": transcript}],
                max_tokens=1500,
            ).strip()
            if not summary:
                # Nothing worth keeping — still safe to drop the raw noise.
                self._memory.delete([r[0] for r in rows])
                return 0

            today = datetime.now(timezone.utc).date().isoformat()
            self._long_term.add(user_id, f"[ringkasan obrolan s/d {today}]", summary)
            self._memory.delete([r[0] for r in rows])
            log_event("consolidate", f"compacted {len(rows)} turns → digest")
            return len(rows)
        except Exception as e:
            log_event("error", f"consolidate: {e}")
            return None


def _demo():
    """Self-check: python -m app.agent.consolidate — no network."""
    import tempfile, os
    from app.agent.memory import Memory
    from app.memory.long_term import LongTermMemory

    class _FakeLLM:
        calls = 0
        def chat(self, messages, max_tokens=1500):
            _FakeLLM.calls += 1
            return "- Rendy lagi bangun AI Chief of Staff\n- prefer remote job"

    d = tempfile.mkdtemp()
    mem = Memory(os.path.join(d, "conv.db"))
    lt = LongTermMemory(os.path.join(d, "lt.db"))
    u = "u1"

    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    new_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # 50 old turns (> MIN_BATCH) + 5 recent
    for i in range(50):
        mem._db.commit_sql(
            "INSERT INTO messages (user_id, role, content, timestamp) VALUES (?,?,?,?)",
            (u, "user", f"pesan lama {i}", old_ts))
    for i in range(5):
        mem._db.commit_sql(
            "INSERT INTO messages (user_id, role, content, timestamp) VALUES (?,?,?,?)",
            (u, "user", f"pesan baru {i}", new_ts))

    c = MemoryConsolidator(mem, lt, _FakeLLM())
    n = c.run(u)
    assert n == 50, f"should compact 50 old turns, got {n}"

    remaining = mem.get(u, limit=100)
    assert len(remaining) == 5, f"recent turns must survive, got {len(remaining)}"
    assert all("baru" in m["content"] for m in remaining), "only recent should remain"

    hits = lt.search(u, "AI Chief remote", k=5)
    assert any("Rendy" in h["assistant"] for h in hits), "digest must land in long-term"

    # under batch threshold → no-op, no extra LLM call
    before = _FakeLLM.calls
    assert c.run(u) is None, "below min_batch should skip"
    assert _FakeLLM.calls == before, "skip must not call LLM"

    print("consolidate self-check OK")


if __name__ == "__main__":
    _demo()
