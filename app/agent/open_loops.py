"""Open-loop tracker — silently capture actionable commitments from chat,
surface them at the right time. Storage = SQLite (reuse Database)."""

import re
from datetime import datetime, timedelta, timezone

from app.lib.database import Database
from app.lib.events import log_event
from app.schema import extract_json

WIB = timezone(timedelta(hours=7))

# Pre-filter: cheap gate so we don't hit Haiku on every idle line.
_ACTIONABLE = re.compile(
    r"\b(harus|kudu|mesti|jangan lupa|deadline|besok|lusa|nanti|"
    r"senin|selasa|rabu|kamis|jumat|jum'at|sabtu|minggu|"
    r"jan(uari)?|feb(ruari)?|mar(et)?|apr(il)?|mei|jun(i)?|jul(i)?|"
    r"agu?(stus)?|sep(tember)?|okt(ober)?|nov(ember)?|des(ember)?|"
    r"tanggal|minggu depan|bulan depan|inget(in)?|follow.?up|submit|kirim(in)?|"
    r"selesai(in)?|beres(in)?|apply|urus(in)?)\b",
    re.IGNORECASE,
)

_EXTRACT_PROMPT = (
    "Hari ini {today} (timezone WIB). Dari SATU pesan Rendy, keluarin daftar "
    "'open loop' — hal yang butuh DITINDAK Rendy nanti (komitmen, tugas, deadline, "
    "keputusan yang harus diambil, hal personal yang harus diurus).\n\n"
    "Balas HANYA JSON array. Tiap item: "
    '{{"text": "<ringkas, sudut pandang Rendy>", "due_at": "<ISO date/datetime WIB atau null>", '
    '"kind": "work|personal|decision"}}\n\n'
    "ATURAN KETAT:\n"
    "- Cuma ambil kalau ini komitmen/tugas NYATA yang butuh tindakan nanti.\n"
    "- Obrolan biasa, opini, pertanyaan, curhat, fakta lampau, hal yang UDAH selesai → array KOSONG [].\n"
    "- due_at: resolve relatif ('Jumat'→tanggal Jumat terdekat, 'besok'→{iso}+1) ke ISO. "
    "Gak ada waktu jelas → null.\n"
    "- Ragu → jangan ambil. Lebih baik kelewat daripada false positive.\n"
    "Gak ada yang actionable → []."
)


def _actionable(message: str) -> bool:
    return bool(_ACTIONABLE.search(message or ""))


def _parse_due(due_at: str):
    """ISO string → aware datetime WIB, or None. Date-only → end of that day."""
    if not due_at:
        return None
    try:
        dt = datetime.fromisoformat(due_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
        if dt.hour == 0 and dt.minute == 0:  # date-only → due by end of day
            dt = dt.replace(hour=23, minute=59)
    return dt


class OpenLoops:
    def __init__(self, llm, db_path: str = "memory/open_loops.db"):
        self._llm = llm  # fast_llm (Haiku)
        self._db = Database(db_path)
        self._init()

    def _init(self):
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS loops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                due_at TEXT,
                kind TEXT DEFAULT 'work',
                status TEXT DEFAULT 'open',
                created_at TEXT NOT NULL,
                surfaced_at TEXT
            )
        """)

    # ---- capture -----------------------------------------------------------
    def ingest(self, user_id: str, message: str):
        """Best-effort: extract actionable loops from one message. Never raises."""
        try:
            if not _actionable(message):
                return
            now = datetime.now(WIB)
            sys = _EXTRACT_PROMPT.format(
                today=now.strftime("%A, %d %B %Y"), iso=now.date().isoformat())
            raw = self._llm.chat(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": message}],
                max_tokens=400,
            )
            data = extract_json(raw)
            if isinstance(data, list):
                self.store(user_id, data)
        except Exception as e:
            log_event("error", f"open_loops.ingest: {e}")

    def store(self, user_id: str, loops: list):
        """Insert new open loops (dedup by open text). Used by the merged
        extractor and by the legacy ingest path. Never raises."""
        try:
            now = datetime.now(WIB)
            existing = {t.lower() for (t,) in self._db.fetch(
                "SELECT text FROM loops WHERE user_id=? AND status='open'", (user_id,))}
            for item in loops or []:
                if not isinstance(item, dict):
                    continue
                text = (item.get("text") or "").strip()
                if not text or text.lower() in existing:
                    continue
                self._db.commit_sql(
                    "INSERT INTO loops (user_id, text, due_at, kind, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'open', ?)",
                    (user_id, text, item.get("due_at") or None,
                     item.get("kind") or "work", now.isoformat()),
                )
                existing.add(text.lower())
        except Exception as e:
            log_event("error", f"open_loops.store: {e}")

    # ---- surface -----------------------------------------------------------
    def _open_rows(self, user_id: str):
        return self._db.fetch(
            "SELECT id, text, due_at, surfaced_at FROM loops "
            "WHERE user_id=? AND status='open'", (user_id,))

    def due_soon(self, user_id: str, within_hours: int = 18) -> list[str]:
        """Loops due within window (or overdue) that haven't been pinged yet.
        Stamps surfaced_at so each loop pings once."""
        now = datetime.now(WIB)
        horizon = now + timedelta(hours=within_hours)
        out = []
        for loop_id, text, due_at, surfaced_at in self._open_rows(user_id):
            if surfaced_at:
                continue
            due = _parse_due(due_at)
            if due is None or due > horizon:
                continue
            out.append(text)
            self._db.commit_sql(
                "UPDATE loops SET surfaced_at=? WHERE id=?", (now.isoformat(), loop_id))
        return out

    def agenda(self, user_id: str) -> list[str]:
        """Overdue + due today/tomorrow, for the morning brief. No stamping."""
        now = datetime.now(WIB)
        cutoff = (now + timedelta(days=1)).date()
        out = []
        for _, text, due_at, _s in self._open_rows(user_id):
            due = _parse_due(due_at)
            if due is None or due.date() > cutoff:
                continue
            tag = "overdue" if due < now else due.strftime("%a %H:%M")
            out.append(f"{text} ({tag})")
        return out

    # ---- close -------------------------------------------------------------
    def mark_done(self, user_id: str, hint: str) -> str:
        """Close the open loop that best matches hint (word overlap)."""
        rows = self._db.fetch(
            "SELECT id, text FROM loops WHERE user_id=? AND status='open'", (user_id,))
        if not rows:
            return "(gak ada loop kebuka)"
        words = set(re.findall(r"\w+", (hint or "").lower()))
        best, best_score = None, 0
        for loop_id, text in rows:
            score = len(words & set(re.findall(r"\w+", text.lower())))
            if score > best_score:
                best, best_score = (loop_id, text), score
        if not best or best_score == 0:
            return "(gak nemu loop yang cocok)"
        self._db.commit_sql("UPDATE loops SET status='done' WHERE id=?", (best[0],))
        return f"(kelar: {best[1]})"

    def expire_stale(self, user_id: str):
        """Open loops past due by >3 days → expired, so they don't pile up."""
        cutoff = datetime.now(WIB) - timedelta(days=3)
        for loop_id, _t, due_at, _s in self._open_rows(user_id):
            due = _parse_due(due_at)
            if due is not None and due < cutoff:
                self._db.commit_sql(
                    "UPDATE loops SET status='expired' WHERE id=?", (loop_id,))


def _demo():
    """Self-check: python -m app.agent.open_loops — no network."""
    import tempfile, os

    class _FakeLLM:
        def chat(self, messages, max_tokens=400):
            # actionable message → one loop; prefilter blocks non-actionable before we get here
            return '[{"text": "kirim proposal ke klien", "due_at": "%s", "kind": "work"}]' % (
                (datetime.now(WIB) + timedelta(hours=5)).isoformat())

    db = os.path.join(tempfile.mkdtemp(), "loops.db")
    ol = OpenLoops(_FakeLLM(), db)
    u = "u1"

    # prefilter: idle chatter → 0 loops, no llm insert
    ol.ingest(u, "lagi mager banget nih santai aja")
    assert ol._open_rows(u) == [], "idle chatter should capture nothing"

    # actionable → 1 loop
    ol.ingest(u, "gua harus kirim proposal ke klien Jumat")
    assert len(ol._open_rows(u)) == 1, "commitment should capture one loop"

    # dedup: same open text not inserted twice
    ol.ingest(u, "inget ya gua harus kirim proposal ke klien")
    assert len(ol._open_rows(u)) == 1, "dedup by open text"

    # due_soon: within 18h & not surfaced → returned + stamped; 2nd call empty
    assert ol.due_soon(u) == ["kirim proposal ke klien"], "due within 18h should surface"
    assert ol.due_soon(u) == [], "surfaced loop should not re-ping"

    # mark_done: hint overlap closes it
    assert "kelar" in ol.mark_done(u, "udah kirim proposalnya")
    assert ol._open_rows(u) == [], "done loop leaves open set empty"

    # agenda: overdue loop shows, far-future doesn't
    now = datetime.now(WIB)
    ol._db.commit_sql(
        "INSERT INTO loops (user_id, text, due_at, status, created_at) "
        "VALUES (?,?,?,'open',?)",
        (u, "bayar invoice", (now - timedelta(hours=2)).isoformat(), now.isoformat()))
    ol._db.commit_sql(
        "INSERT INTO loops (user_id, text, due_at, status, created_at) "
        "VALUES (?,?,?,'open',?)",
        (u, "renew domain", (now + timedelta(days=10)).isoformat(), now.isoformat()))
    ag = ol.agenda(u)
    assert any("bayar invoice" in x for x in ag), "overdue should be in agenda"
    assert not any("renew domain" in x for x in ag), "far-future not in agenda"

    # expire_stale: >3 days overdue → expired
    ol._db.commit_sql(
        "INSERT INTO loops (user_id, text, due_at, status, created_at) "
        "VALUES (?,?,?,'open',?)",
        (u, "basi", (now - timedelta(days=4)).isoformat(), now.isoformat()))
    ol.expire_stale(u)
    assert not any(t == "basi" for _, t, _, _ in ol._open_rows(u)), "stale should expire"

    print("open_loops self-check OK")


if __name__ == "__main__":
    _demo()
