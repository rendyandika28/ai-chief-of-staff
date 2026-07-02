from app.tools.registry import ToolRegistry
from app.tools.time_tool import TimeTool
from app.tools.weather_tool import WeatherTool
from app.tools.cctv_tool import CctvTool
from app.tools.job_hunt_tool import JobHuntTool
from app.tools.reminder_tool import ReminderTool


def load_tools(scheduler=None, profile=None):
    registry = ToolRegistry()

    registry.register("time", TimeTool())
    registry.register("weather", WeatherTool())
    registry.register("cctv", CctvTool())
    registry.register("job_hunt", JobHuntTool())

    if scheduler:
        registry.register("reminder", ReminderTool(scheduler))

    return registry
