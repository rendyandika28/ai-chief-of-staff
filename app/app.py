from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

from app.llm.deepseek import DeepSeekLLM
from app.agent.memory import Memory
from app.agent.agent import Agent
from app.agent.scheduler import Scheduler
from app.config.settings import settings
from app.memory.long_term import LongTermMemory
from app.os.event_bus import EventBus
from app.os.goal_manager import GoalManager
from app.os.knowledge_graph import KnowledgeGraph
from app.agents.watcher import WatcherManager


from app.llm.anthropic import ClaudeLLM

def create_core():
    event_bus = EventBus()

    # Haiku (fast/cheap) for planner + compression + facts
    fast_llm = ClaudeLLM(model="claude-haiku-4-5-20251001")
    # Sonnet (smart) for executor natural conversation
    smart_llm = ClaudeLLM()

    memory = Memory()
    long_term = LongTermMemory()
    scheduler = Scheduler()

    knowledge_graph = KnowledgeGraph()
    goal_manager = GoalManager()

    agent = Agent(fast_llm, smart_llm, memory, scheduler, long_term, knowledge_graph)

    watchers = WatcherManager(event_bus)

    # Morning check-in watcher (7-9 AM WIB)
    def morning_check():
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=7)))
        if 7 <= now.hour <= 9:
            knowledge_graph.cleanup()  # self-clean stale facts
            facts = knowledge_graph.about("507090539", "Rendy")
            extra = ""
            if facts:
                recent = [f for f in facts if f.get("predicate") in ("meeting_with", "deadline", "health_status")]
                if recent:
                    extra = f" Lo ada {recent[0]['predicate'].replace('_',' ')} {recent[0]['object']}."
            return f"Selamat pagi! Udah bangun?{extra}"
        return None

    watchers.register(morning_check, 3600)  # check hourly

    # Job scraper — every 6 hours, respects profile preferences
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

    return agent, memory, scheduler, event_bus, goal_manager, watchers
