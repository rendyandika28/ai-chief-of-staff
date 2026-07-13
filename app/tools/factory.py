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
from app.tools.web_research_tool import WebResearchTool
from app.tools.browser_tool import BrowserTool


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
        # Route through store_facts → same vocabulary guard + supersede + embed.
        self._kg.store_facts(user_id, [
            {"subject": parts[0], "predicate": parts[1], "object": parts[2]}])
        return "(noted)"


class LoopDoneTool:
    name = "loop_done"
    description = (
        "Tandain satu open-loop (komitmen/tugas Rendy) udah kelar. "
        "Panggil pas Rendy bilang suatu hal udah selesai/beres/dikerjain. "
        "Input: kata kunci hal yang kelar — contoh: proposal klien"
    )

    def __init__(self, open_loops):
        self._ol = open_loops

    def run(self, input: str = "", user_id: str = "") -> str:
        return self._ol.mark_done(user_id, input or "")


def load_tools(scheduler=None, profile=None, knowledge_graph=None, llm=None,
               open_loops=None) -> dict:
    tools = {
        "time": TimeTool(),
        "weather": WeatherTool(),
        "cctv": CctvTool(),
        "job_hunt": JobHuntTool(profile, llm),
        "news": NewsTool(),
        "polymarket": PolymarketTool(),
        "doc_gen": DocTool(),
        "calendar": CalendarTool(),
        "web_research": WebResearchTool(),
        "browse": BrowserTool(),
    }
    if scheduler:
        tools["reminder"] = ReminderTool(scheduler)
    if knowledge_graph:
        tools["remember"] = RememberTool(knowledge_graph)
    if open_loops:
        tools["loop_done"] = LoopDoneTool(open_loops)
    return tools
