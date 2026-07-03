"""Agent — single native tool-use pass, one persona (system.md), streaming."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
import logging
import re

from app.agent.profile import Profile
from app.tools.factory import load_tools

MAX_HISTORY = 20
WIB = timezone(timedelta(hours=7))
MEDIA_RE = re.compile(r'\[(?:VIDEO|IMAGE):.*?\]')
logger = logging.getLogger(__name__)


class Agent:
    """Memory → context → one Claude call (native tools, in-persona) → stream."""

    def __init__(self, llm, memory, scheduler=None,
                 long_term_memory=None, knowledge_graph=None):
        self.llm = llm
        self.memory = memory
        self.long_term = long_term_memory
        self.knowledge_graph = knowledge_graph
        self.profile = Profile()
        self.tools = load_tools(scheduler, self.profile, knowledge_graph)
        # Static prefix (persona + profile) — identical every turn, so cached.
        self._static_prompt = (
            Path("prompts/system.md").read_text(encoding="utf-8")
            + "\n\nUser Profile:\n" + self.profile.load()
        )
        self._tool_schema = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "Argument sesuai format di description. Kosongin kalau gak perlu."}
                    },
                },
            }
            for t in self.tools.values()
        ]

    def _execute(self, tool_name: str, tool_input: str = "", user_id: str = "") -> str:
        tool = self.tools.get(tool_name)
        if tool is None:
            return f"(tool '{tool_name}' gak ada)"
        try:
            return tool.run(tool_input, user_id=user_id)
        except TypeError:
            return tool.run(tool_input)

    def phrase(self, instruction: str) -> str | None:
        """One in-persona line for proactive pings (reminders, nudges, briefs)."""
        try:
            msg = [
                {"role": "system", "content": self._static_prompt},
                {"role": "user", "content": f"[SISTEM: bukan Rendy yang ngomong] {instruction}"},
            ]
            return self.llm.chat(msg, max_tokens=200).strip() or None
        except Exception:
            return None

    def chat_stream(self, user_id: str, message: str):
        try:
            yield from self._process(user_id, message)
        except Exception as e:
            logger.error(f"Agent crash: {e}", exc_info=True)
            yield "Maaf, ada error internal."

    def _process(self, user_id: str, message: str):
        history = self.memory.get(user_id) or []
        history = [h for h in history if "kesulitan memproses" not in h.get("content", "")]
        history = history[-MAX_HISTORY:]

        messages = [
            {"role": "system", "content": self._system_blocks(user_id, message)},
            *history,
            {"role": "user", "content": message},
        ]

        # Native tool-use runner — capture media markers so they survive
        # regardless of how the model phrases its reply.
        media = []

        def runner(name, inp):
            out = self._execute(name, (inp or {}).get("input", ""), user_id) or ""
            media.extend(MEDIA_RE.findall(out))
            clean = MEDIA_RE.sub("", out).strip()
            return clean or "(selesai)"

        full_text = ""
        for token in self.llm.stream_with_tools(messages, self._tool_schema, runner):
            full_text += token
            yield token

        for marker in media:
            yield marker

        if self.long_term:
            self.long_term.add(user_id, message, full_text)

    def _system_blocks(self, user_id: str, message: str) -> list:
        """Two system blocks: cached static prefix + small dynamic tail."""
        now = datetime.now(WIB)
        dynamic = f"HARI INI: {now.strftime('%A, %d %B %Y jam %H:%M WIB')}"

        context_lines = []
        if self.knowledge_graph:
            kg = self.knowledge_graph.context_for(user_id, message)
            if kg:
                context_lines.append(kg)
        if self.long_term:
            for m in self.long_term.search(user_id, message, k=3):
                context_lines.append(f"- Dulu lo tanya: {m['user']} → jawab: {m['assistant']}")
        if context_lines:
            dynamic += "\n\n## Yang lo inget soal ini:\n" + "\n".join(context_lines)

        return [
            {"type": "text", "text": self._static_prompt,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic},
        ]
