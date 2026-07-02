from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

from app.llm.deepseek import DeepSeekLLM
from app.agent.memory import Memory
from app.agent.agent import Agent
from app.agent.scheduler import Scheduler
from app.memory.long_term import LongTermMemory
from app.os.event_bus import EventBus
from app.os.goal_manager import GoalManager
from app.os.knowledge_graph import KnowledgeGraph
from app.agents.watcher import WatcherManager


def create_core():
    event_bus = EventBus()

    llm = DeepSeekLLM()
    memory = Memory()
    long_term = LongTermMemory()
    scheduler = Scheduler()

    knowledge_graph = KnowledgeGraph()
    goal_manager = GoalManager()

    agent = Agent(llm, memory, scheduler, long_term, knowledge_graph)

    watchers = WatcherManager(event_bus)

    return agent, memory, scheduler, event_bus, goal_manager, watchers
