from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

from pathlib import Path
from datetime import datetime, timedelta, timezone

from app.agent.memory import Memory
from app.agent.agent import Agent
from app.agent.scheduler import Scheduler
from app.memory.long_term import LongTermMemory
from app.os.event_bus import EventBus
from app.os.knowledge_graph import KnowledgeGraph
from app.agents.watcher import WatcherManager
from app.llm.anthropic import ClaudeLLM

WIB = timezone(timedelta(hours=7))
USER_ID = "507090539"  # single-user bot (Rendy)

# Predicates worth following up on when they go quiet.
FOLLOWUP_PREDICATES = ("working_on", "building", "project", "progress",
                       "deadline", "goal", "planning", "learning")


def create_core():
    event_bus = EventBus()
    llm = ClaudeLLM(model="claude-sonnet-5")

    memory = Memory()
    long_term = LongTermMemory()
    scheduler = Scheduler()
    knowledge_graph = KnowledgeGraph()

    agent = Agent(llm, llm, memory, scheduler, long_term, knowledge_graph)

    watchers = WatcherManager(event_bus)

    # Signal-based proactivity: if a project/deadline the user mentioned has gone
    # quiet, nudge ONCE with an LLM-phrased line (persona), not a template alarm.
    def stale_topic_followup():
        now = datetime.now(WIB)
        if not (9 <= now.hour <= 21):  # waking hours only
            return None
        today = now.strftime("%Y-%m-%d")
        if _already_nudged_today(knowledge_graph, today):
            return None

        stale = _find_stale_topic(knowledge_graph, now)
        if not stale:
            return None

        knowledge_graph.upsert("system", "Rendy", "nudged_on", today, 1.0)
        return _phrase_followup(llm, stale)

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

    return agent, memory, scheduler, event_bus, watchers


def _already_nudged_today(kg, today: str) -> bool:
    for f in kg.about("system", "Rendy"):
        if f["predicate"] == "nudged_on" and f["object"] == today:
            return True
    return False


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


def _phrase_followup(llm, fact):
    persona = Path("prompts/system.md").read_text(encoding="utf-8")
    topic = f"{fact['predicate'].replace('_', ' ')} {fact['object']}"
    msg = [
        {"role": "system", "content": persona},
        {"role": "user", "content": (
            f"[SISTEM: bukan Rendy yang ngomong] Rendy beberapa hari lalu sempet cerita soal '{topic}' "
            "tapi udah lama gak dibahas. Buka obrolan santai buat nanya progressnya, SATU kalimat pendek, "
            "kayak temen yang inget. Jangan template, jangan kaku."
        )},
    ]
    try:
        return llm.chat(msg, max_tokens=120).strip() or None
    except Exception:
        return None
