"""Permission check + safety validation."""

import json
from pathlib import Path


class Guard:
    def __init__(self, event_bus=None, rules_path: str = "config/permissions.json"):
        self._bus = event_bus
        self._rules_path = Path(rules_path)

    def _load_rules(self):
        if not self._rules_path.exists():
            return []
        try:
            return json.loads(self._rules_path.read_text()).get("rules", [])
        except json.JSONDecodeError:
            return []

    def check(self, action: str, tool: str) -> bool:
        """Returns True if user approval required."""
        for rule in self._load_rules():
            if rule.get("action") != action:
                continue
            rule_tool = rule.get("tool", "*")
            if rule_tool != "*" and rule_tool != tool:
                continue
            return rule.get("require_approval", False)
        return False

    def validate(self, response: str) -> tuple[bool, list[str]]:
        flags = []
        if len(response) < 2:
            flags.append("empty_response")
        return (len(flags) == 0, flags)
