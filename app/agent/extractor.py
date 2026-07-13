"""Merged memory extractor — ONE Haiku call per gated turn → (loops, facts).

Replaces two separate extraction calls (open-loops + KG facts) with a single
call that returns both. Halves per-turn extraction cost. Best-effort: any
failure yields ([], []) and the turn proceeds untouched.
"""

import re
from datetime import datetime, timedelta, timezone

from app.lib.events import log_event
from app.schema import extract_json
from app.agent.open_loops import _actionable  # reuse the loop prefilter
from app.os.knowledge_graph import VALID_PREDICATES

WIB = timezone(timedelta(hours=7))

# Second prefilter: lines that plausibly state a durable fact about Rendy.
_FACTWORTHY = re.compile(
    r"\b(kerja|kantor|proyek|project|bangun|ngerjain|garap|belajar|"
    r"suka|prefer|benci|pindah|pake|pakai|goal|target|deadline|"
    r"tim|klien|client|nama|tinggal|domisili|jabatan|posisi|role|lagi)\b",
    re.IGNORECASE,
)

_PROMPT = (
    "Hari ini {today} (timezone WIB). Dari SATU pesan Rendy, keluarin dua hal:\n\n"
    "1. loops — hal yang butuh DITINDAK Rendy nanti (komitmen, tugas, deadline, "
    "keputusan yang harus diambil, hal personal yang harus diurus).\n"
    "2. facts — fakta DURABLE soal Rendy (kerjaan, proyek, preferensi, identitas).\n\n"
    "Balas HANYA JSON object: "
    '{{"loops": [...], "facts": [...]}}\n'
    'Tiap loop: {{"text": "<ringkas, sudut pandang Rendy>", '
    '"due_at": "<ISO date/datetime WIB atau null>", "kind": "work|personal|decision"}}\n'
    'Tiap fact: {{"subject": "Rendy", "predicate": "<SATU dari daftar>", "object": "<nilai>"}}\n\n'
    "PREDICATE WAJIB salah satu ini (JANGAN ngarang di luar daftar):\n"
    "  working_on, building, project, deadline, goal, planning, learning,\n"
    "  works_at, role_is, prefers, dislikes, uses, knows, located_in, contact_is\n\n"
    "ATURAN KETAT:\n"
    "- loops: cuma komitmen/tugas NYATA yang butuh tindakan nanti. Obrolan biasa, "
    "opini, pertanyaan, curhat, hal yang UDAH selesai → jangan masuk.\n"
    "- facts: cuma fakta yang masih relevan ke depan. Bukan hal sekali lewat.\n"
    "- due_at: resolve relatif ('Jumat'→Jumat terdekat, 'besok'→{iso}+1) ke ISO. "
    "Gak jelas → null.\n"
    "- Ragu → jangan ambil. Lebih baik kelewat daripada salah.\n"
    "- Gak ada apa-apa → {{\"loops\": [], \"facts\": []}}."
)


class MemoryExtractor:
    def __init__(self, llm):
        self._llm = llm  # fast_llm (Haiku)

    def _gate(self, message: str) -> bool:
        """Cheap prefilter: skip the LLM entirely on idle chatter."""
        return bool(_actionable(message) or _FACTWORTHY.search(message or ""))

    def extract(self, message: str):
        """One message → (loops: list[dict], facts: list[dict]). Never raises."""
        try:
            if not self._gate(message):
                return [], []
            now = datetime.now(WIB)
            sys = _PROMPT.format(
                today=now.strftime("%A, %d %B %Y"), iso=now.date().isoformat())
            raw = self._llm.chat(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": message}],
                max_tokens=500,
            )
            data = extract_json(raw)
            if not isinstance(data, dict):
                return [], []
            loops = data.get("loops")
            facts = data.get("facts")
            # ponytail: one prompt does two jobs to save a call; if fact quality
            # sags, split back into two focused calls (higher cost).
            return (loops if isinstance(loops, list) else [],
                    facts if isinstance(facts, list) else [])
        except Exception as e:
            log_event("error", f"extractor: {e}")
            return [], []


def _demo():
    """Self-check: python -m app.agent.extractor — no network."""
    class _FakeLLM:
        calls = 0

        def chat(self, messages, max_tokens=500):
            _FakeLLM.calls += 1
            return ('{"loops": [{"text": "kirim proposal", "due_at": null, "kind": "work"}], '
                    '"facts": [{"subject": "Rendy", "predicate": "building", "object": "AI chief"}]}')

    ex = MemoryExtractor(_FakeLLM())

    # gate: idle chatter → no LLM call, empty result
    before = _FakeLLM.calls
    assert ex.extract("santai aja lah hari ini") == ([], [])
    assert _FakeLLM.calls == before, "idle chatter must not hit the LLM"

    # actionable/factworthy → LLM runs, both lists returned
    loops, facts = ex.extract("gua lagi bangun AI chief, harus kirim proposal")
    assert loops and facts, f"should extract both, got {loops} / {facts}"
    assert facts[0]["predicate"] == "building"

    # malformed JSON → ([], []), no raise
    class _BadLLM:
        def chat(self, messages, max_tokens=500):
            return "not json at all"

    assert MemoryExtractor(_BadLLM()).extract("harus submit besok") == ([], [])

    # predicate vocabulary is exported and non-empty (extractor prompt relies on it)
    assert "building" in VALID_PREDICATES

    print("extractor self-check OK")


if __name__ == "__main__":
    _demo()
