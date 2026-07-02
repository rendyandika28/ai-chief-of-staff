"""Simplified agent — streaming-only, natural language, 30-message context."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
import json
import logging
import re
from app.agent.profile import Profile
from app.prompt.builder import PromptBuilder
from app.tools.init import load_tools
from app.schema import extract_json, validate, validate_verdict, prompt_instructions

MAX_HISTORY = 30
logger = logging.getLogger(__name__)


class Planner:
    """Decides action (chat/tool). Natural language first — JSON is optional."""

    def __init__(self, llm, profile, tools):
        self.llm = llm
        self.profile = profile
        self.tools = tools
        self.builder = PromptBuilder()
        self._prompt = Path("prompts/planner.md").read_text(encoding="utf-8")

    def _tool_exists(self, name: str) -> bool:
        return self.tools.get(name) is not None

    def plan(self, message: str, history: list, feedback: str = "",
             memories: Optional[list] = None) -> dict:
        now = datetime.now(timezone(timedelta(hours=7)))
        date_info = f"HARI INI: {now.strftime('%A, %d %B %Y jam %H:%M WIB')}"
        prompt = self._prompt + "\n\n" + date_info
        prompt += "\n\n" + prompt_instructions(self.tools.describe())

        if memories:
            mem_text = "\n".join(
                f"- User: {m['user']}\n  Assistant: {m['assistant']}"
                for m in memories
            )
            prompt += f"\n\n## Context:\n{mem_text}"

        if feedback:
            prompt += f"\n\n[FEEDBACK ITERASI SEBELUMNYA]\n{feedback}"

        messages = self.builder.build(
            system_prompt=prompt,
            profile=self.profile.load(),
            history=history,
            message=message,
        )
        raw = self.llm.chat(messages)
        raw = raw.strip()
        if "```" in raw:
            raw = re.sub(r'```(?:json)?\s*', '', raw).strip()

        # Try parse JSON. If Claude sends multiple JSON blocks, take only the first.
        data = extract_json(raw)
        if data is None and "{" in raw:
            # Grab just the first JSON object
            start = raw.find("{")
            depth = 0
            end = start
            for i in range(start, len(raw)):
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > start:
                try:
                    data = json.loads(raw[start:end])
                except json.JSONDecodeError:
                    data = None

        if data is None or validate(data, self._tool_exists) is not None:
            # Auto-correct: if action field is a known tool name, convert
            if data and data.get("action") and self._tool_exists(data["action"]):
                data = {
                    "action": "tool",
                    "tool": data["action"],
                    "input": data.get("input", data.get("command", "")),
                }
            else:
                clean = re.sub(r'\{.*?\}', '', raw).strip()
                logger.info(f"Planner natural language: {clean[:100]}")
                return {"action": "chat", "message": clean if clean else raw.strip()}
        return data


class Executor:
    """Crafts natural language from tool results. Streaming support."""

    def __init__(self, llm, profile):
        self.llm = llm
        self.profile = profile
        self.builder = PromptBuilder()
        self._prompt = Path("prompts/system.md").read_text(encoding="utf-8")

    def respond_stream(self, user_message: str, history: list, tool_results: str):
        summary_msg = (
            f"User tadi minta: \"{user_message}\"\n\n"
            f"Berikut hasil dari tool yang dijalankan:\n{tool_results}\n\n"
            "Balas user dengan bahasa natural. Langsung aja."
        )
        messages = self.builder.build(
            system_prompt=self._prompt,
            profile=self.profile.load(),
            history=history,
            message=summary_msg,
        )
        for token in self.llm.stream(messages, max_tokens=512):
            yield token


class Agent:
    """Orchestrator: memory → Planner → tools → Executor (streaming)."""

    def __init__(self, fast_llm, smart_llm, memory, scheduler=None,
                 long_term_memory=None, knowledge_graph=None):
        self.fast_llm = fast_llm      # Haiku — planner, compression, facts
        self.smart_llm = smart_llm    # Sonnet — executor natural language
        self.memory = memory
        self.long_term = long_term_memory
        self.knowledge_graph = knowledge_graph
        self.profile = Profile()
        self.tools = load_tools(scheduler, self.profile)
        self.planner = Planner(fast_llm, self.profile, self.tools)
        self.executor = Executor(smart_llm, self.profile)

    def _execute(self, tool_name: str, tool_input: str = "", user_id: str = "") -> str:
        tool = self.tools.get(tool_name)
        try:
            return tool.run(tool_input, user_id=user_id)
        except TypeError:
            return tool.run(tool_input)

    def _execute_tools(self, data: dict, user_id: str = "") -> str:
        if data["action"] == "tool":
            result = self._execute(data["tool"], data.get("input", ""), user_id)
            return result
        if data["action"] == "chain":
            results = []
            prev_result = ""
            for step in data["steps"]:
                inp = step.get("input", "")
                if prev_result and "{prev}" in inp:
                    inp = inp.replace("{prev}", prev_result)
                result = self._execute(step["tool"], inp, user_id)
                results.append(result)
                prev_result = result
            return "\n".join(results)
        return ""

    def chat(self, user_id: str, message: str) -> str:
        """Legacy sync interface — joins streaming tokens."""
        return "".join(list(self.chat_stream(user_id, message)))

    def chat_stream(self, user_id: str, message: str):
        """Live typing streaming — single pass, no reflection loop."""
        try:
            yield from self._process(user_id, message)
        except Exception as e:
            logger.error(f"Agent crash: {e}", exc_info=True)
            yield "Maaf, ada error internal."

    def _process(self, user_id: str, message: str):
        history = self.memory.get(user_id) or []
        history = [h for h in history if "kesulitan memproses" not in h.get("content", "")]
        history = history[-MAX_HISTORY:]

        # Context compression
        if len(history) > 20:
            compressed = self._compress_history(history[:-10])
            history = [{"role": "system", "content": f"Ringkasan: {compressed}"}] + history[-10:]

        # Build context
        memories = []
        if self.long_term:
            memories = self.long_term.search(user_id, message, k=3)
        if self.knowledge_graph:
            kg = self.knowledge_graph.context_for(user_id, message)
            if kg:
                memories = (memories or [])
                memories.insert(0, {"user": "", "assistant": kg})

        # Plan (non-streaming)
        data = self.planner.plan(message, history, "", memories)

        if data["action"] == "chat":
            text = data.get("message", "")
            if text:
                for char in text:
                    yield char
                if self.long_term:
                    self.long_term.add(user_id, message, text)
                self._extract_facts(user_id, message, text)
            return

        # Tool execution
        tool_results = self._execute_tools(data, user_id)

        # Stream Executor for all tools — always natural language summary
        full_text = ""
        for token in self.executor.respond_stream(message, history, tool_results):
            full_text += token
            yield token

        if self.long_term:
            self.long_term.add(user_id, message, full_text)
        self._extract_facts(user_id, message, full_text)

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
