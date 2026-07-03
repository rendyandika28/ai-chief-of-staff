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

    memory = Memory()
    long_term = LongTermMemory()
    scheduler = Scheduler()
    knowledge_graph = KnowledgeGraph()

    agent = Agent(llm, memory, scheduler, long_term, knowledge_graph)

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

    # Job scraper — utility, respects profile preferences.
    def job_scraper():
        from app.agent.profile import Profile
        prefs = Profile().raw().get("job_preferences", {})
        role = prefs.get("roles", ["frontend engineer"])[0]
        loc = prefs.get("preferred_location", "remote")
        job_tool = agent.tools.get("job_hunt")
        if job_tool:
            return job_tool.run(f"report:{role}|{loc}") or None
        return None

    watchers.register(job_scraper, 21600)  # every 6 hours

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
