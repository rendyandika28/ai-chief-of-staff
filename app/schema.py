"""Single source of truth for the LLM action JSON protocol."""

import json
from typing import Optional

ACTIONS = ("chat", "tool", "chain")
VERDICTS = ("good", "retry")


def extract_json(raw: str) -> Optional[dict]:
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def validate(data, tool_exists) -> Optional[str]:
    """Returns None if valid, error string if invalid."""
    if not isinstance(data, dict):
        return "response is not a dict"
    action = data.get("action")
    if action not in ACTIONS:
        return f"unknown action '{action}', must be one of {ACTIONS}"

    if action == "chat":
        if not isinstance(data.get("message"), str):
            return "chat action missing 'message' string"
        return None

    if action == "tool":
        if not tool_exists(data.get("tool", "")):
            return f"unknown tool '{data.get('tool')}'"
        return None

    if action == "chain":
        steps = data.get("steps")
        if not isinstance(steps, list) or len(steps) == 0:
            return "chain action missing 'steps' list"
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                return f"chain step {i} is not a dict"
            if not tool_exists(step.get("tool", "")):
                return f"chain step {i}: unknown tool '{step.get('tool')}'"
        return None

    return "unknown validation error"


def validate_verdict(data) -> Optional[str]:
    """Returns None if valid verdict, error string if invalid."""
    if not isinstance(data, dict):
        return "verdict is not a dict"
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        return f"unknown verdict '{verdict}', must be one of {VERDICTS}"
    if not isinstance(data.get("feedback"), str):
        return "verdict missing 'feedback' string"
    return None


def prompt_instructions(tools_metadata: list[dict]) -> str:
    tool_lines = "\n".join(
        f"- {t['name']}: {t['description']}" for t in tools_metadata
    )
    return f"""
## Format output (WAJIB — JSON saja)

Kamu HARUS merespons HANYA dengan raw JSON. Tanpa markdown, tanpa backtick, tanpa teks sebelum/sesudah.

### Chat:
{{"action": "chat", "message": "jawaban kamu"}}

### Satu tool:
{{"action": "tool", "tool": "<nama>", "input": "<nilai>"}}

### Rantai tool (berurutan):
{{"action": "chain", "steps": [{{"tool": "<t1>", "input": "<i1>"}}, {{"tool": "<t2>", "input": "<i2>"}}]}}
Gunakan "{{prev}}" di input untuk merujuk hasil step sebelumnya.

### Tools tersedia:
{tool_lines}

### Aturan:
- action HARUS: chat, tool, atau chain
- JANGAN tambahkan teks di luar JSON
- JANGAN bungkus dalam blok ```json```
"""
