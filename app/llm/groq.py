"""Groq LLM — drop-in for ClaudeLLM.chat, for the cheap/mechanical work
(extraction, proactive one-liners, job scoring, consolidation).

Groq's free tier is generous and inference is fast; llama-3.3-70b handles JSON
extraction and casual Indonesian fine. Reuses GROQ_API_KEY (already used for
Whisper) via the openai client — message format is identical to ours.

Reliability: on rate-limit/failure/no-key it falls back to the injected model
(Haiku), so proactivity and persona quality never break. Main agent stays on
Claude — only fast_llm moves here, cutting the bulk of recurring Haiku spend.
"""

import logging

from app.config.settings import settings

_MODEL = "llama-3.3-70b-versatile"


class GroqLLM:
    def __init__(self, model: str = _MODEL, api_key: str = None, fallback=None):
        self.model = model
        self._fallback = fallback  # ClaudeLLM (Haiku) used on any Groq failure
        key = settings.GROQ_API_KEY if api_key is None else api_key
        self._client = None
        if key:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
            except Exception as e:
                logging.warning(f"Groq init failed, using fallback: {e}")

    def chat(self, messages: list, max_tokens: int = 4096) -> str:
        if self._client is None:
            return self._fallback.chat(messages, max_tokens) if self._fallback else ""
        try:
            r = self._client.chat.completions.create(
                model=self.model, messages=messages,
                max_tokens=max_tokens, temperature=0.4)
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            logging.warning(f"Groq chat failed, falling back to Haiku: {e}")
            if self._fallback:
                return self._fallback.chat(messages, max_tokens)
            raise


def _demo():
    """Self-check: python -m app.llm.groq — no network."""
    class _Fallback:
        def chat(self, messages, max_tokens=4096):
            return "FALLBACK"

    # no key → fallback
    g = GroqLLM(api_key="", fallback=_Fallback())
    assert g.chat([{"role": "user", "content": "hi"}]) == "FALLBACK", "no-key must fall back"

    # success path via a stub client mimicking openai's shape
    class _Msg:
        content = "  OK-groq  "

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _OK:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    assert kw["messages"], "messages passed through"
                    return _Resp()

    g2 = GroqLLM(api_key="x", fallback=_Fallback())
    g2._client = _OK()
    assert g2.chat([{"role": "user", "content": "hi"}]) == "OK-groq", "success returns trimmed content"

    # client error → fallback
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("429 rate limit")

    g3 = GroqLLM(api_key="x", fallback=_Fallback())
    g3._client = _Boom()
    assert g3.chat([{"role": "user", "content": "hi"}]) == "FALLBACK", "error must fall back"

    print("groq self-check OK")


if __name__ == "__main__":
    _demo()
