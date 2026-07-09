"""Tool wiring — plain dict of name → tool instance (each has .name/.description/.run)."""

from app.tools.time_tool import TimeTool
from app.tools.weather_tool import WeatherTool
from app.tools.cctv_tool import CctvTool
from app.tools.job_hunt_tool import JobHuntTool
from app.tools.reminder_tool import ReminderTool
from app.tools.news_tool import NewsTool
from app.tools.polymarket_tool import PolymarketTool
from app.tools.doc_tool import DocTool
from app.tools.calendar_tool import CalendarTool


class RememberTool:
    name = "remember"
    description = (
        "Simpen fakta penting soal Rendy yang baru lo pelajari dari obrolan "
        "(kerjaan, proyek, deadline, preferensi). "
        "Format input: subject|predicate|object — contoh: Rendy|works_at|PT X"
    )

    def __init__(self, knowledge_graph):
        self._kg = knowledge_graph

    def run(self, input: str = "", user_id: str = "") -> str:
        parts = [p.strip() for p in (input or "").split("|")]
        if len(parts) != 3 or not all(parts):
            return "Error: format harus subject|predicate|object"
        self._kg.upsert(user_id, parts[0], parts[1], parts[2], 0.8)
        return "(noted)"


def load_tools(scheduler=None, profile=None, knowledge_graph=None, llm=None) -> dict:
    tools = {
        "time": TimeTool(),
        "weather": WeatherTool(),
        "cctv": CctvTool(),
        "job_hunt": JobHuntTool(profile, llm),
        "news": NewsTool(),
        "polymarket": PolymarketTool(),
        "doc_gen": DocTool(),
        "calendar": CalendarTool(),
    }
    if scheduler:
        tools["reminder"] = ReminderTool(scheduler)
    if knowledge_graph:
        tools["remember"] = RememberTool(knowledge_graph)
    return tools
