"""Agent — single native tool-use pass, one persona (system.md), streaming."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
import logging
import re

from app.agent.profile import Profile
from app.prompt.builder import PromptBuilder
from app.tools.init import load_tools
from app.schema import extract_json

MAX_HISTORY = 30
WIB = timezone(timedelta(hours=7))
MEDIA_RE = re.compile(r'\[(?:VIDEO|IMAGE):.*?\]')
logger = logging.getLogger(__name__)


class Agent:
    """Memory → context → one Claude call (native tools, in-persona) → stream."""

    def __init__(self, fast_llm, smart_llm, memory, scheduler=None,
                 long_term_memory=None, knowledge_graph=None):
        self.fast_llm = fast_llm      # cheap calls: compression, fact extraction
        self.smart_llm = smart_llm    # main conversation + tool use
        self.memory = memory
        self.long_term = long_term_memory
        self.knowledge_graph = knowledge_graph
        self.profile = Profile()
        self.tools = load_tools(scheduler, self.profile)
        self.builder = PromptBuilder()
        self._system_prompt = Path("prompts/system.md").read_text(encoding="utf-8")
        self._tool_schema = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "Argument sesuai format di description. Kosongin kalau gak perlu."}
                    },
                },
            }
            for t in self.tools.describe()
        ]

    def _execute(self, tool_name: str, tool_input: str = "", user_id: str = "") -> str:
        tool = self.tools.get(tool_name)
        if tool is None:
            return f"(tool '{tool_name}' gak ada)"
        try:
            return tool.run(tool_input, user_id=user_id)
        except TypeError:
            return tool.run(tool_input)

    def chat(self, user_id: str, message: str) -> str:
        """Legacy sync interface — joins streaming tokens."""
        return "".join(list(self.chat_stream(user_id, message)))

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

        if len(history) > 20:
            compressed = self._compress_history(history[:-10])
            history = [{"role": "system", "content": f"Ringkasan: {compressed}"}] + history[-10:]

        system_prompt = self._build_system_prompt(user_id, message)

        messages = self.builder.build(
            system_prompt=system_prompt,
            profile=self.profile.load(),
            history=history,
            message=message,
        )

        # Native tool-use runner — capture media markers so they survive
        # regardless of how the model phrases its reply.
        media = []

        def runner(name, inp):
            out = self._execute(name, (inp or {}).get("input", ""), user_id) or ""
            media.extend(MEDIA_RE.findall(out))
            clean = MEDIA_RE.sub("", out).strip()
            return clean or "(selesai)"

        full_text = ""
        for token in self.smart_llm.stream_with_tools(messages, self._tool_schema, runner):
            full_text += token
            yield token

        for marker in media:
            yield marker

        if self.long_term:
            self.long_term.add(user_id, message, full_text)
        self._extract_facts(user_id, message, full_text)

    def _build_system_prompt(self, user_id: str, message: str) -> str:
        now = datetime.now(WIB)
        prompt = self._system_prompt
        prompt += f"\n\nHARI INI: {now.strftime('%A, %d %B %Y jam %H:%M WIB')}"

        context_lines = []
        if self.knowledge_graph:
            kg = self.knowledge_graph.context_for(user_id, message)
            if kg:
                context_lines.append(kg)
        if self.long_term:
            for m in self.long_term.search(user_id, message, k=3):
                context_lines.append(f"- Dulu lo tanya: {m['user']} → jawab: {m['assistant']}")
        if context_lines:
            prompt += "\n\n## Yang lo inget soal ini:\n" + "\n".join(context_lines)
        return prompt

    def _compress_history(self, history: list) -> str:
        try:
            lines = "\n".join(f"{h['role']}: {h['content'][:200]}" for h in history)
            msg = [
                {"role": "system", "content": "Ringkas percakapan ini dalam 2-3 kalimat. Fokus ke topik, keputusan, fakta. Bahasa Indonesia."},
                {"role": "user", "content": lines},
            ]
            return self.fast_llm.chat(msg, max_tokens=200)
        except Exception:
            return ""

    def _extract_facts(self, user_id: str, message: str, response: str):
        if not self.knowledge_graph or len(message) < 10:
            return
        try:
            prompt = (
                "Extract 1-3 facts as JSON array. "
                '[{"subject":"Rendy","predicate":"works_at","object":"PT X"}]. '
                "Only extract if explicit. Return [] if nothing."
            )
            msg = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"User: {message}\nAssistant: {response}"},
            ]
            raw = self.fast_llm.chat(msg, max_tokens=300)
            facts = extract_json(raw)
            if isinstance(facts, list):
                for f in facts[:3]:
                    if all(k in f for k in ("subject", "predicate", "object")):
                        self.knowledge_graph.upsert(
                            user_id, f["subject"], f["predicate"], f["object"], 0.7
                        )
        except Exception:
            pass
