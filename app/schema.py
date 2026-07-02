"""JSON extraction helper for LLM outputs (fact extraction)."""

import json
from typing import Optional, Union


def extract_json(raw: str) -> Optional[Union[dict, list]]:
    """Pull the first JSON object or array out of a possibly-noisy LLM reply."""
    raw = raw.strip()
    candidates = []
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end > start:
            candidates.append((start, raw[start:end + 1]))
    for _, snippet in sorted(candidates):  # prefer whichever token comes first
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            continue
    return None
