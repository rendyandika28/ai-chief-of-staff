from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

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

    llm = ClaudeLLM() if settings.LLM_PROVIDER == "anthropic" else DeepSeekLLM()
    memory = Memory()
    long_term = LongTermMemory()
    scheduler = Scheduler()

    knowledge_graph = KnowledgeGraph()
    goal_manager = GoalManager()

    agent = Agent(llm, memory, scheduler, long_term, knowledge_graph)

    watchers = WatcherManager(event_bus)

    # Morning check-in watcher (7-9 AM WIB)
    def morning_check():
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=7)))
        if 7 <= now.hour <= 9:
            facts = knowledge_graph.about("system", "Rendy")
            extra = ""
            if facts:
                recent = [f for f in facts if f.get("predicate") in ("meeting_with", "deadline", "health_status")]
                if recent:
                    extra = f" Lo ada {recent[0]['predicate'].replace('_',' ')} {recent[0]['object']}."
            return f"Selamat pagi! Udah bangun?{extra}"
        return None

    watchers.register(morning_check, 3600)  # check hourly

    return agent, memory, scheduler, event_bus, goal_manager, watchers
