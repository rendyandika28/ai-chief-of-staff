from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

from datetime import datetime, timedelta, timezone

from app.agent.memory import Memory
from app.agent.agent import Agent
from app.agent.scheduler import Scheduler
from app.memory.long_term import LongTermMemory
from app.os.knowledge_graph import KnowledgeGraph
from app.agent.watcher import WatcherManager
from app.llm.anthropic import ClaudeLLM

WIB = timezone(timedelta(hours=7))
USER_ID = "507090539"  # single-user bot (Rendy)

# Predicates worth following up on when they go quiet.
FOLLOWUP_PREDICATES = ("working_on", "building", "project", "progress",
                       "deadline", "goal", "planning", "learning")


def create_core():
    llm = ClaudeLLM()
    fast_llm = ClaudeLLM(model="claude-haiku-4-5-20251001")

    memory = Memory()
    long_term = LongTermMemory()
    scheduler = Scheduler()
    knowledge_graph = KnowledgeGraph()

    agent = Agent(llm, memory, scheduler, long_term, knowledge_graph, fast_llm=fast_llm)

    watchers = WatcherManager()

    # Forget stale facts daily so the knowledge graph doesn't grow forever.
    watchers.register(lambda: knowledge_graph.cleanup(), 86400)

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

    # Job scraper — scrape + simpan ke jobs.json tiap 6 jam. TIDAK push ke Telegram;
    # lowongan sekarang hidup di menu "Lowongan" dashboard (biar gak spam chat).
    def job_scraper():
        from app.agent.profile import Profile
        prefs = Profile().raw().get("job_preferences", {})
        role = prefs.get("roles", ["frontend engineer"])[0]
        loc = prefs.get("preferred_location", "remote")
        job_tool = agent.tools.get("job_hunt")
        if job_tool:
            job_tool.run(f"report:{role}|{loc}")  # simpan lowongan baru; return diabaikan
        return None

    watchers.register(job_scraper, 21600)  # every 6 hours

    # Kalender — notify meeting bentar lagi (esp. gmeet) + undangan baru yg belum di-RSVP.
    def calendar_watcher():
        from app.tools.calendar_tool import fetch_events, load_seen, save_seen
        now = datetime.now(WIB)
        try:
            events = fetch_events(now, now + timedelta(days=7))
        except FileNotFoundError:
            return None  # belum ada akun tersambung
        except Exception as e:
            logging.getLogger(__name__).warning(f"calendar_watcher: {e}")
            return None

        seen = load_seen()
        current_ids = {e["id"] for e in events}
        pending = {}  # event_id -> tag baru, di-commit HANYA kalau alert kekirim
        alerts = []
        for e in events:
            key = e["id"]
            # event yg sama bisa muncul di >1 akun — dedup by id (persisted + run ini)
            done = set(seen.get(key, [])) | set(pending.get(key, []))
            if e["timed"]:
                mins = (e["start"] - now).total_seconds() / 60
                if 0 <= mins <= 16 and "soon" not in done:
                    link = f" — {e['gmeet']}" if e["gmeet"] else ""
                    alerts.append(f"{int(mins)} menit lagi: {e['summary']} [{e['label']}]{link}")
                    pending.setdefault(key, []).append("soon")
            if e["needs_action"] and "invite" not in done:
                when = e["start"].strftime("%a %d/%m %H:%M")
                alerts.append(f"Undangan baru: {e['summary']} [{e['label']}] — {when}, belum di-RSVP")
                pending.setdefault(key, []).append("invite")

        # prune event yg udah lewat (gak ada di window 7 hari) — aman disimpen kapan pun
        pruned = {k: v for k, v in seen.items() if k in current_ids}
        if not alerts:
            save_seen(pruned)
            return None

        msg = agent.phrase(
            "Sampaikan ke Rendy dengan gaya lo, ringkas dan to the point:\n" + "\n".join(alerts)
        )
        if not msg:
            save_seen(pruned)  # jangan commit tag baru — biar dicoba lagi tick berikutnya
            return None

        # ponytail: commit tag baru begitu pesan siap. Kalau kirim ke Telegram gagal
        # (jarang), notif itu gak diretry — cukup buat sekarang.
        for key, new in pending.items():
            pruned.setdefault(key, []).extend(new)
        save_seen(pruned)
        return msg

    watchers.register(calendar_watcher, 300)  # tiap 5 menit

    # Morning brief — persistent daily task at 07:00 WIB, survives restarts.
    if not scheduler.has_pending("__morning_brief__"):
        run_at, interval = Scheduler.calc_daily("07:00")
        scheduler.add("system", "__morning_brief__", run_at=run_at, interval_seconds=interval)

    def morning_brief():
        parts = []
        weather = agent.tools.get("weather")
        if weather:
            try:
                parts.append(f"Cuaca: {weather.run('jakarta')}")
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
        reminders = scheduler.due_today(USER_ID)
        if reminders:
            parts.append("Reminder hari ini: " + "; ".join(reminders))
        stale = _find_stale_topic(knowledge_graph, datetime.now(WIB))
        if stale:
            parts.append(f"Topik yang lama gak dibahas: {stale['predicate'].replace('_', ' ')} {stale['object']}")
        return agent.phrase(
            "Bikin morning brief singkat (2-4 kalimat) buat Rendy dari data ini, "
            "gaya lo sendiri, sapa sekilas terus langsung isi:\n" + "\n".join(parts)
        )

    scheduler.morning_brief = morning_brief

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
