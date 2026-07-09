import logging
import time
from anthropic import (Anthropic, APIConnectionError, APITimeoutError,
                       APIStatusError, InternalServerError, RateLimitError)

from app.config.settings import settings

_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _transient(e) -> bool:
    """Error yg layak di-retry: koneksi/timeout, rate limit, 5xx, overloaded (529)."""
    if isinstance(e, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
        return True
    if isinstance(e, APIStatusError):
        return getattr(e, "status_code", None) in _RETRY_STATUS
    return False


class ClaudeLLM:
    def __init__(self, model: str = None):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = model or settings.ANTHROPIC_MODEL

    def chat(self, messages: list, max_tokens: int = 4096) -> str:
        kwargs = self._build_kwargs(messages, max_tokens)
        for attempt in range(3):
            try:
                return self._extract_text(self.client.messages.create(**kwargs))
            except Exception as e:
                if attempt == 2 or not _transient(e):
                    logging.error(f"Claude API error: {e}")
                    raise
                logging.warning(f"Claude retry {attempt+1}/2: {e}")
                time.sleep(1.5 * (attempt + 1))

    def stream_with_tools(self, messages: list, tools: list, runner, max_tokens: int = 4096):
        """Native tool-use loop: streams the reply in-persona, calling tools as the
        model requests them. `runner(name, input_dict) -> str` executes a tool."""
        system, chat = self._split(messages)
        try:
            while True:
                kwargs = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": chat,
                    "thinking": {"type": "disabled"},
                }
                if system:
                    kwargs["system"] = system
                if tools:
                    kwargs["tools"] = tools

                # Retry buka stream pada error transient — TAPI cuma selama belum ada
                # token kekirim di call ini (gak bisa nge-un-yield yg udah lewat).
                attempt = 0
                while True:
                    produced = False
                    try:
                        with self.client.messages.stream(**kwargs) as stream:
                            for text in stream.text_stream:
                                produced = True
                                yield text
                            final = stream.get_final_message()
                        break
                    except Exception as e:
                        attempt += 1
                        if produced or attempt > 2 or not _transient(e):
                            raise
                        logging.warning(f"stream retry {attempt}/2: {e}")
                        time.sleep(1.5 * attempt)

                if final.stop_reason != "tool_use":
                    return

                chat.append({"role": "assistant", "content": final.content})
                results = []
                for block in final.content:
                    if block.type == "tool_use":
                        out = runner(block.name, block.input)
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": out,
                        })
                chat.append({"role": "user", "content": results})
        except Exception as e:
            logging.error(f"Claude tool-stream error: {e}")
            # Jangan diem — kasih tau user ada gangguan (nyambung ke teks yg mungkin udah kekirim).
            yield "\n\n⚠️ Waduh, lagi ada gangguan pas mroses. Coba lagi bentar ya."

    def _split(self, messages: list):
        system = None
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat_messages.append(m)
        return system, chat_messages

    def _build_kwargs(self, messages: list, max_tokens: int) -> dict:
        system, chat_messages = self._split(messages)
        kwargs = {"model": self.model, "max_tokens": max_tokens}
        if system:
            kwargs["system"] = system
        kwargs["messages"] = chat_messages
        kwargs["thinking"] = {"type": "disabled"}
        return kwargs

    def _extract_text(self, response) -> str:
        for block in response.content:
            if hasattr(block, 'text'):
                text = block.text
                logging.info(f"Claude({self.model[:12]}...): {len(text)} chars, "
                           f"in={response.usage.input_tokens} out={response.usage.output_tokens}")
                return text
        return ""
