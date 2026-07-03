from datetime import datetime

class TimeTool:
    name = "time"
    description = "Get current time"

    def run(self, input: str = ""):
        return datetime.now().isoformat()