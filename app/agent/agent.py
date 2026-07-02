from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import re
from app.agent.profile import Profile
from app.prompt.builder import PromptBuilder
from app.tools.init import load_tools
from app.schema import extract_json, validate, validate_verdict, prompt_instructions

MAX_ITERATIONS = 10


class Planner:
    """Decides action (chat/tool/chain). No personality — just tool selection."""

    def __init__(self, llm, profile, tools):
        self.llm = llm
        self.profile = profile
        self.tools = tools
        self.builder = PromptBuilder()
        self._prompt = Path("prompts/planner.md").read_text(encoding="utf-8")

    def _tool_exists(self, name: str) -> bool:
        return self.tools.get(name) is not None

    def plan(self, message: str, history: list, feedback: str = "",
             memories: Optional[list] = None) -> Optional[dict]:
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
        raw = self.llm.chat(messages, temperature=0.0)
        data = extract_json(raw)
        if data is None or validate(data, self._tool_exists) is not None:
            logging.warning(f"Planner invalid response: {raw[:200]}")
            return None
        return data


class Executor:
    """Crafts natural language response from tool results. Uses persona + tone."""

    def __init__(self, llm, profile):
        self.llm = llm
        self.profile = profile
        self.builder = PromptBuilder()
        self._prompt = Path("prompts/system.md").read_text(encoding="utf-8")

    def respond(self, user_message: str, history: list, tool_results: str) -> Optional[str]:
        summary_msg = (
            f"User tadi minta: \"{user_message}\"\n\n"
            f"Berikut hasil dari tool yang dijalankan:\n{tool_results}\n\n"
            "Balas user dengan bahasa natural merangkum hasilnya. "
            "Gunakan format JSON chat."
        )
        messages = self.builder.build(
            system_prompt=self._prompt,
            profile=self.profile.load(),
            history=history,
            message=summary_msg,
        )
        raw = self.llm.chat(messages, temperature=0.7)
        data = extract_json(raw)
        if data is None or data.get("action") != "chat":
            logging.warning(f"Executor invalid response: {raw[:200]}")
            return None
        return data.get("message")


class Reflector:
    """Evaluates whether the response adequately answers the user's question."""

    def __init__(self, llm):
        self.llm = llm
        self._prompt = Path("prompts/reflector.md").read_text(encoding="utf-8")

    def reflect(self, user_message: str, response: str, tool_results: str = "") -> Optional[dict]:
        eval_msg = (
            f"Pertanyaan user: \"{user_message}\"\n\n"
            f"Hasil tool:\n{tool_results if tool_results else '(tidak ada — jawaban langsung)'}\n\n"
            f"Respons yang akan dikirim ke user:\n{response}"
        )
        messages = [
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": eval_msg},
        ]
        raw = self.llm.chat(messages, temperature=0.0)
        data = extract_json(raw)
        if data is None or validate_verdict(data) is not None:
            return {"verdict": "good", "feedback": "validation failed, defaulting to good"}
        return data


class Agent:
    """Orchestrator: memory → Planner → tools → Executor → Reflector loop."""

    def __init__(self, llm, memory, scheduler=None, long_term_memory=None,
                 knowledge_graph=None):
        self.memory = memory
        self.long_term = long_term_memory
        self.knowledge_graph = knowledge_graph
        self.profile = Profile()
        self.tools = load_tools(scheduler, self.profile)
        self.planner = Planner(llm, self.profile, self.tools)
        self.executor = Executor(llm, self.profile)
        self.reflector = Reflector(llm)

    def _execute(self, tool_name: str, tool_input: str = "", user_id: str = "") -> str:
        tool = self.tools.get(tool_name)
        try:
            return tool.run(tool_input, user_id=user_id)
        except TypeError:
            return tool.run(tool_input)

    def _execute_tools(self, data: dict, user_id: str = "") -> str:
        if data["action"] == "tool":
            result = self._execute(data["tool"], data.get("input", ""), user_id)
            return f"[{data['tool']}] {result}"
        if data["action"] == "chain":
            results = []
            prev_result = ""
            for step in data["steps"]:
                inp = step.get("input", "")
                if prev_result and "{prev}" in inp:
                    inp = inp.replace("{prev}", prev_result)
                result = self._execute(step["tool"], inp, user_id)
                results.append(f"[{step['tool']}] {result}")
                prev_result = result
            return "\n".join(results)
        return ""

    def chat(self, user_id: str, message: str) -> str:
        try:
            return self._do_chat(user_id, message)
        except Exception as e:
            logging.error(f"Agent crash: {e}", exc_info=True)
            return "Maaf, ada error internal."

    def _do_chat(self, user_id: str, message: str) -> str:
        history = self.memory.get(user_id)
        # Filter out fallback responses from history — they confuse the LLM
        history = [h for h in history if "kesulitan memproses" not in h.get("content", "")]
        feedback = ""
        last_response = "Maaf, aku kesulitan memproses permintaan itu."

        memories = []
        if self.long_term:
            memories = self.long_term.search(user_id, message, k=3)

        if self.knowledge_graph:
            kg = self.knowledge_graph.context_for(user_id, message)
            if kg:
                memories = list(memories) if memories else []
                memories.insert(0, {"user": "", "assistant": kg})

        for _ in range(MAX_ITERATIONS):
            data = self.planner.plan(message, history, feedback, memories)
            if data is None:
                return last_response

            if data["action"] == "chat":
                response = data["message"]
            else:
                tool_results = self._execute_tools(data, user_id)
                # Visual tools: skip Executor LLM, return raw tool output directly
                tool_name = data.get("tool", "")
                if tool_name in ("cctv", "traffic", "browser"):
                    response = tool_results
                else:
                    response = self.executor.respond(message, history, tool_results)
                    if response is None:
                        response = tool_results
                    # Preserve [IMAGE/VIDEO] markers from tool results
                    markers = re.findall(r'\[(?:IMAGE|VIDEO):.*?\]', tool_results)
                    if markers:
                        response += "\n" + "\n".join(markers)

            last_response = response

            # Skip reflection for visual outputs (images/videos) — nothing to "improve"
            if re.search(r'\[(IMAGE|VIDEO):', response):
                return response

            verdict = self.reflector.reflect(message, response, tool_results if data["action"] != "chat" else "")
            if verdict is None:
                return response

            if verdict["verdict"] == "good":
                if self.long_term:
                    self.long_term.add(user_id, message, response)
                return response

            feedback = verdict["feedback"]

        return last_response
