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

    # Health coach watchers
    def posture_reminder():
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=7)))
        h = now.hour
        # 9am-10pm, every 2 hours (9, 11, 13, 15, 17, 19, 21)
        if 9 <= h <= 21 and h % 2 == 1:
            return "🧘 Udah 2 jam bro. Berdiri dulu, stretching 2 menit. Jangan lupa minum air putih!"
        return None

    watchers.register(posture_reminder, 3600)  # hourly

    def evening_wind_down():
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=7)))
        if now.hour == 22 and now.minute < 5:
            knowledge_graph.upsert("system", "Rendy", "bedtime_reminder", now.strftime("%Y-%m-%d"), 0.5)
            return "🌙 Jam 10 malem bro. Matiin laptop, jangan begadang. Besok pagi lo bakal berterima kasih sama gue."
        return None

    watchers.register(evening_wind_down, 3600)

    def morning_health_check():
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=7)))
        if now.hour == 8 and now.minute < 10:
            knowledge_graph.cleanup()
            return (
                "☀️ Selamat pagi! Udah bangun?\n"
                "Gimana tidur lo tadi malem? (1-10)\n"
                "Ada yang sakit atau butuh diurus hari ini?\n"
                "Jangan lupa sarapan ya — protein + serat, jangan cuma kopi doang."
            )
        return None

    watchers.register(morning_health_check, 600)  # check every 10 min

    return agent, memory, scheduler, event_bus, goal_manager, watchers
