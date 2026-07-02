from datetime import datetime
from app.tools.base import Tool

class TimeTool(Tool):
    name = "time"
    description = "Get current time"

    def run(self, input: str = ""):
        return datetime.now().isoformat()