from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

from datetime import datetime, timedelta, timezone

from app.agent.memory import Memory
from app.agent.agent import Agent
from app.agent.scheduler import Scheduler
from app.memory.long_term import LongTermMemory
from app.memory.lessons import Lessons
from app.os.knowledge_graph import KnowledgeGraph
from app.agent.watcher import WatcherManager
from app.agent.open_loops import OpenLoops
from app.agent.consolidate import MemoryConsolidator
from app.agent.extractor import MemoryExtractor
from app.agent.proactive import (
    find_conflicts, relevant_facts, unseen_conflicts, mark_seen)
from app.llm.anthropic import ClaudeLLM
from app.llm.embedder import Embedder
from app.llm.groq import GroqLLM
from app.config.settings import settings

WIB = timezone(timedelta(hours=7))
USER_ID = "507090539"  # single-user bot (Rendy)

# Predicates worth following up on when they go quiet.
FOLLOWUP_PREDICATES = ("working_on", "building", "project", "progress",
                       "deadline", "goal", "planning", "learning")


def create_core():
    llm = ClaudeLLM()
    # Cheap/mechanical work (extraction, proactive one-liners, job scoring,
    # consolidation) runs on Groq's free tier; Haiku is the reliability fallback.
    # Cuts the bulk of recurring Claude spend — main agent stays on Claude.
    haiku = ClaudeLLM(model="claude-haiku-4-5-20251001")
    fast_llm = GroqLLM(fallback=haiku) if settings.GROQ_API_KEY else haiku

    embedder = Embedder()  # semantic layer; no GEMINI_API_KEY → keyword-only

    memory = Memory()
    long_term = LongTermMemory(embedder=embedder)
    scheduler = Scheduler()
    knowledge_graph = KnowledgeGraph(embedder=embedder)
    open_loops = OpenLoops(fast_llm)
    lessons = Lessons(embedder=embedder)  # learned corrections/preferences
    extractor = MemoryExtractor(fast_llm)  # merged loop+fact+lesson extraction, 1 call/turn

    # Embed anything stored before the embedder existed. No-op when off; cheap.
    knowledge_graph.backfill_embeddings()
    long_term.backfill_embeddings()
    lessons.backfill_embeddings()

    agent = Agent(llm, memory, scheduler, long_term, knowledge_graph,
                  fast_llm=fast_llm, open_loops=open_loops,
                  extractor=extractor, embedder=embedder, lessons=lessons)

    watchers = WatcherManager()

    # Forget stale facts daily so the knowledge graph doesn't grow forever.
    watchers.register(lambda: knowledge_graph.cleanup(), 86400)

    # Compact raw chat >30 hari jadi ringkasan long-term, buang mentahnya.
    # Bikin conversations.db bounded tanpa ilang memory. Return diabaikan (gak push).
    consolidator = MemoryConsolidator(memory, long_term, fast_llm)
    watchers.register(lambda: (consolidator.run(USER_ID), None)[1], 86400)

    # Signal-based proactivity: if a project/deadline the user mentioned has gone
    # quiet, nudge with an LLM-phrased line (persona). Max 2/day, min 4h apart.
    def stale_topic_followup():
        now = datetime.now(WIB)
        if not (9 <= now.hour <= 21):  # waking hours only
            return None
        if not _nudge_allowed(knowledge_graph, now):
            return None

        stale = _find_stale_topic(knowledge_graph, now)
        if not stale:
            return None

        knowledge_graph.upsert("system", "Rendy", "nudged_on", now.isoformat(), 1.0)
        topic = f"{stale['predicate'].replace('_', ' ')} {stale['object']}"
        return agent.phrase(
            f"Rendy beberapa hari lalu sempet cerita soal '{topic}' tapi udah lama gak dibahas. "
            "Buka obrolan santai buat nanya progressnya, SATU kalimat pendek, "
            "kayak temen yang inget. Jangan template, jangan kaku."
        )

    watchers.register(stale_topic_followup, 3600)  # check hourly, gated to once/day

    # Open-loop deadline ping — sentil sekali pas komitmen Rendy mepet deadline.
    def deadline_ping():
        now = datetime.now(WIB)
        if not (9 <= now.hour <= 21):  # jam melek aja
            return None
        open_loops.expire_stale(USER_ID)  # sekalian bersihin yang basi
        loops = open_loops.due_soon(USER_ID)  # nge-stamp: tiap loop ping sekali
        if not loops:
            return None
        return agent.phrase(
            "Sentil Rendy soal deadline yang mepet ini, santai & ringkas, "
            "kayak temen yang inget. Jangan template:\n- " + "\n- ".join(loops)
        )

    watchers.register(deadline_ping, 3600)  # tiap 1 jam

    # Kalender — notify meeting bentar lagi (esp. gmeet) + undangan baru yg belum di-RSVP.
    # Invite → kartu detail + tombol RSVP (lewat watchers.on_invite). Meeting soon →
    # ping casual (lewat return/on_alert). Tiap tag di-commit HANYA setelah kekirim.
    def calendar_watcher():
        from app.tools.calendar_tool import (
            fetch_events, load_seen, save_seen, format_invite_card)
        now = datetime.now(WIB)
        try:
            events = fetch_events(now, now + timedelta(days=7))
        except FileNotFoundError:
            return None  # belum ada akun tersambung
        except Exception as e:
            logging.getLogger(__name__).warning(f"calendar_watcher: {e}")
            return None

        current_ids = {e["id"] for e in events}
        # prune event yg udah lewat (gak ada di window 7 hari) — aman disimpen kapan pun
        pruned = {k: v for k, v in load_seen().items() if k in current_ids}
        soon = []  # (text, key)

        for e in events:
            key = e["id"]
            done = set(pruned.get(key, []))
            if e["timed"]:
                mins = (e["start"] - now).total_seconds() / 60
                if 0 <= mins <= 16 and "soon" not in done:
                    link = f" — {e['gmeet']}" if e["gmeet"] else ""
                    # Pre-meeting enrichment: fakta KG relevan (kalau ada).
                    ctx = f"{e['summary']} {e.get('organizer','')} {' '.join(e.get('guests',[]))}"
                    facts = relevant_facts(knowledge_graph, embedder, USER_ID, ctx)
                    hint = f" (inget: {'; '.join(facts)})" if facts else ""
                    soon.append(
                        (f"{int(mins)} menit lagi: {e['summary']} [{e['label']}]{link}{hint}", key))
            # Undangan baru → kartu + tombol. Commit "invite" HANYA kalau kekirim.
            if e["needs_action"] and "invite" not in done:
                payload = {"text": format_invite_card(e),
                           "label": e["label"], "event_id": e["id"]}
                if watchers.on_invite and watchers.on_invite(payload):
                    pruned.setdefault(key, []).append("invite")

        if not soon:
            save_seen(pruned)  # invite tags (kalau ada) tetep ke-persist
            return None

        msg = agent.phrase(
            "Sampaikan ke Rendy dengan gaya lo, ringkas dan to the point:\n"
            + "\n".join(t for t, _ in soon)
        )
        if not msg:
            save_seen(pruned)  # invite udah ke-commit; soon dicoba lagi tick berikutnya
            return None
        for _, key in soon:
            pruned.setdefault(key, []).append("soon")
        save_seen(pruned)
        return msg

    watchers.register(calendar_watcher, 300)  # tiap 5 menit

    # Conflict detector — bentrok jadwal (meeting overlap / deadline nabrak meeting).
    # Cuma yang URGENT (≤36h) yang di-ping, sekali each (seen-store). Future
    # conflict masuk weekly review. Satu-satunya sumber interrupt baru di v2.
    def conflict_watcher():
        from app.tools.calendar_tool import fetch_events
        now = datetime.now(WIB)
        if not (9 <= now.hour <= 21):
            return None
        try:
            events = fetch_events(now, now + timedelta(hours=48))
        except FileNotFoundError:
            return None
        except Exception as e:
            logging.getLogger(__name__).warning(f"conflict_watcher: {e}")
            return None
        conflicts = [c for c in find_conflicts(events, open_loops.due_items(USER_ID), now)
                     if c["urgent"]]
        conflicts = unseen_conflicts(conflicts, now)
        if not conflicts:
            return None
        msg = agent.phrase(
            "Kasih tau Rendy soal bentrok jadwal ini, santai & ringkas, "
            "kayak temen yang jagain kalender. Jangan template:\n- "
            + "\n- ".join(c["text"] for c in conflicts))
        if msg:
            mark_seen(conflicts)  # stamp cuma kalau berhasil di-phrase
        return msg

    watchers.register(conflict_watcher, 3600)  # tiap 1 jam

    # Health watcher — bot jaga kesehatan dirinya. Alert Rendy kalau ada error
    # BERULANG (bukan blip sekali). Gate ketat: cuma error yg muncul >=2x dlm 1 jam,
    # signature baru (belum pernah di-alert), max 1 alert / 3 jam.
    def health_watcher():
        import json
        from datetime import timezone as _tz
        from app.lib.events import recent
        seen_path = Path("memory/health_seen.json")
        try:
            state = json.loads(seen_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            state = {"signatures": [], "last_alert": ""}

        now_utc = datetime.now(_tz.utc)
        window = (now_utc - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        counts = {}
        for e in recent(200):
            if e["kind"] == "error" and (e["ts"] or "") >= window:
                sig = (e["detail"] or "")[:60]
                counts[sig] = counts.get(sig, 0) + 1
        recurring = {s for s, n in counts.items() if n >= 2}
        new = recurring - set(state["signatures"])
        if not new:
            return None
        # rate-limit: max 1 alert / 3 jam
        if state["last_alert"] and state["last_alert"] > (now_utc - timedelta(hours=3)).isoformat():
            return None
        msg = agent.phrase(
            "Kasih tau Rendy ada error yang muncul berulang di sistem lo sendiri, "
            "ringkas & jujur, jangan bikin panik:\n- " + "\n- ".join(list(new)[:5]))
        if msg:
            state["signatures"] = list(set(state["signatures"]) | new)[-100:]
            state["last_alert"] = now_utc.isoformat()
            seen_path.write_text(json.dumps(state))
        return msg

    watchers.register(health_watcher, 900)  # tiap 15 menit

    # Morning brief — persistent daily task at 07:00 WIB, survives restarts.
    if not scheduler.has_pending("__morning_brief__"):
        run_at, interval = Scheduler.calc_daily("07:00")
        scheduler.add("system", "__morning_brief__", run_at=run_at, interval_seconds=interval)

    def morning_brief():
        parts = []
        weather = agent.tools.get("weather")
        if weather:
            try:
                # City dari profile (bukan hardcode) — ikut kalau Rendy pindah kota.
                city = (agent.profile.contact().get("location") or "Jakarta").split(",")[0].strip()
                parts.append(f"Cuaca: {weather.run(city)}")
            except Exception:
                pass
        cal = agent.tools.get("calendar")
        if cal:
            try:
                agenda = cal.run("")
                if agenda and "Gak ada agenda" not in agenda and "Belum ada akun" not in agenda:
                    parts.append("Agenda hari ini:\n" + agenda)
            except Exception:
                pass
        # Pre-meeting KG context + konflik hari ini (structured, di atas agenda teks).
        try:
            events = _today_events()
            ctx_lines = []
            for e in events:
                ctx = f"{e['summary']} {e.get('organizer','')} {' '.join(e.get('guests',[]))}"
                facts = relevant_facts(knowledge_graph, embedder, USER_ID, ctx)
                if facts:
                    ctx_lines.append(f"{e['summary']}: {'; '.join(facts)}")
            if ctx_lines:
                parts.append("Konteks meeting:\n" + "\n".join(ctx_lines))
            clashes = [c["text"] for c in find_conflicts(
                events, open_loops.due_items(USER_ID), datetime.now(WIB))]
            if clashes:
                parts.append("Perhatiin bentrok:\n- " + "\n- ".join(clashes))
        except Exception:
            pass
        reminders = scheduler.due_today(USER_ID)
        if reminders:
            parts.append("Reminder hari ini: " + "; ".join(reminders))
        loops = open_loops.agenda(USER_ID)
        if loops:
            parts.append("Open loop (belum kelar): " + "; ".join(loops))
        stale = _find_stale_topic(knowledge_graph, datetime.now(WIB))
        if stale:
            parts.append(f"Topik yang lama gak dibahas: {stale['predicate'].replace('_', ' ')} {stale['object']}")
        return agent.phrase(
            "Bikin morning brief singkat (2-4 kalimat) buat Rendy dari data ini, "
            "gaya lo sendiri, sapa sekilas terus langsung isi:\n" + "\n".join(parts)
        )

    scheduler.morning_brief = morning_brief

    def _window_events(start, end):
        """Timed calendar events in [start, end]. [] on no account / error."""
        from app.tools.calendar_tool import fetch_events
        try:
            return [e for e in fetch_events(start, end) if e.get("timed")]
        except Exception:
            return []

    def _today_events():
        now = datetime.now(WIB)
        return _window_events(now, now.replace(hour=23, minute=59))

    # Weekly review — Minggu 19:00 WIB. Reflektif: kelar minggu ini, masih
    # nganggur, topik kesentuh, minggu depan + bentrok. Nol interrupt.
    if not scheduler.has_pending("__weekly_review__"):
        run_at, interval = Scheduler.calc_weekly("minggu", "19:00")
        scheduler.add("system", "__weekly_review__", run_at=run_at, interval_seconds=interval)

    def weekly_review():
        now = datetime.now(WIB)
        parts = []
        done = open_loops.closed_since(USER_ID, (now - timedelta(days=7)).isoformat())
        if done:
            parts.append("Kelar minggu ini: " + "; ".join(done))
        openloops = open_loops.agenda(USER_ID)
        if openloops:
            parts.append("Masih nganggur: " + "; ".join(openloops))
        # KG stores updated_at in naive local time; use naive now to match (7h
        # tz skew is immaterial across a 7-day window).
        touched = knowledge_graph.touched_since(
            USER_ID, (datetime.now() - timedelta(days=7)).isoformat())
        if touched:
            topics = {f"{f['predicate'].replace('_', ' ')} {f['object']}" for f in touched}
            parts.append("Yang lo pikirin minggu ini: " + "; ".join(list(topics)[:6]))
        next_week = _window_events(now, now + timedelta(days=7))
        clashes = [c["text"] for c in find_conflicts(
            next_week, open_loops.due_items(USER_ID), now)]
        if clashes:
            parts.append("Bentrok minggu depan:\n- " + "\n- ".join(clashes))
        if not parts:
            return None  # minggu kosong → jangan spam
        return agent.phrase(
            "Bikin weekly review buat Rendy dari data ini (santai, reflektif, "
            "3-5 kalimat), tutup dengan tawaran bantu minggu depan:\n" + "\n".join(parts))

    scheduler.weekly_review = weekly_review

    # Evening wind-down — harian 21:00. Retrospektif + intip besok pagi.
    if not scheduler.has_pending("__evening_brief__"):
        run_at, interval = Scheduler.calc_daily("21:00")
        scheduler.add("system", "__evening_brief__", run_at=run_at, interval_seconds=interval)

    def evening_brief():
        now = datetime.now(WIB)
        parts = []
        done = open_loops.closed_since(USER_ID, now.replace(hour=0, minute=0).isoformat())
        if done:
            parts.append("Hari ini lo kelarin: " + "; ".join(done))
        tomorrow = now.replace(hour=23, minute=59) + timedelta(minutes=1)
        tmr_events = _window_events(tomorrow, tomorrow.replace(hour=23, minute=59))
        if tmr_events:
            first = tmr_events[0]
            parts.append(f"Besok mulai: {first['summary']} jam {first['start']:%H:%M}")
        if not (done or tmr_events):
            return None  # ga ada yang kelar & ga ada meeting besok → diem
        return agent.phrase(
            "Bikin wind-down malam buat Rendy (1-2 kalimat, santai, penutup hari) "
            "dari data ini:\n" + "\n".join(parts))

    scheduler.evening_brief = evening_brief

    return agent, memory, scheduler, watchers


def _nudge_allowed(kg, now) -> bool:
    """Max 2 stale-topic nudges per day, at least 4h apart."""
    today = now.strftime("%Y-%m-%d")
    stamps = sorted(
        f["object"] for f in kg.about("system", "Rendy")
        if f["predicate"] == "nudged_on" and f["object"].startswith(today)
    )
    if len(stamps) >= 2:
        return False
    if stamps and stamps[-1] > (now - timedelta(hours=4)).isoformat():
        return False
    return True


def _find_stale_topic(kg, now):
    """Return a fact (dict) the user mentioned but hasn't touched in 2+ days."""
    facts = kg.about(USER_ID, "Rendy")
    cutoff = now - timedelta(days=2)
    for f in facts:
        if f["predicate"] not in FOLLOWUP_PREDICATES:
            continue
        rows = kg._db.fetch(
            "SELECT updated_at FROM facts WHERE user_id=? AND subject=? AND predicate=? AND object=?",
            (USER_ID, f["subject"], f["predicate"], f["object"]),
        )
        if rows and rows[0][0] < cutoff.isoformat():
            return f
    return None
