from app.tools.registry import ToolRegistry
from app.tools.time_tool import TimeTool
from app.tools.webfetch_tool import WebFetchTool
from app.tools.calc_tool import CalculatorTool
from app.tools.browser_tool import BrowserTool
from app.tools.reminder_tool import ReminderTool
from app.tools.http_tool import HttpTool
from app.tools.file_tool import FileTool
from app.tools.weather_tool import WeatherTool
from app.tools.traffic_tool import TrafficTool
from app.tools.cctv_tool import CctvTool
from app.tools.job_hunt_tool import JobHuntTool
from app.tools.auto_apply_tool import AutoApplyTool
from app.tools.home_cctv import HomeCctvTool


def load_tools(scheduler=None, profile=None):
    registry = ToolRegistry()

    browser = BrowserTool()

    registry.register("time", TimeTool())
    registry.register("webfetch", WebFetchTool())
    registry.register("calc", CalculatorTool())
    registry.register("browser", browser)
    registry.register("http", HttpTool())
    registry.register("files", FileTool())
    registry.register("weather", WeatherTool())
    registry.register("traffic", TrafficTool(browser))
    registry.register("cctv", CctvTool(browser))
    registry.register("job_hunt", JobHuntTool(browser))
    registry.register("auto_apply", AutoApplyTool(browser, profile))
    registry.register("cctv_home", HomeCctvTool())

    if scheduler:
        registry.register("reminder", ReminderTool(scheduler))

    return registry
