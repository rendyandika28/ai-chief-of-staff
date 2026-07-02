class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, name, fn):
        self.tools[name] = fn

    def get(self, name):
        return self.tools.get(name)

    def list(self):
        return list(self.tools.keys())

    def describe(self):
        return [
            {"name": t.name, "description": t.description}
            for t in self.tools.values()
        ]
