import logging
from anthropic import Anthropic

from app.config.settings import settings


class ClaudeLLM:
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        try:
            system = None
            chat_messages = []
            for m in messages:
                if m["role"] == "system":
                    system = m["content"]
                else:
                    chat_messages.append(m)

            kwargs = {"model": settings.ANTHROPIC_MODEL, "max_tokens": max_tokens}
            if system:
                kwargs["system"] = system
            kwargs["messages"] = chat_messages

            # Disable thinking for faster, deterministic output
            kwargs["thinking"] = {"type": "disabled"}

            response = self.client.messages.create(**kwargs)
            # Extract text from content blocks (skip thinking blocks)
            for block in response.content:
                if hasattr(block, 'text'):
                    text = block.text
                    logging.info(f"Claude ok: {len(text)} chars, tokens in={response.usage.input_tokens} out={response.usage.output_tokens}")
                    return text
            return ""
        except Exception as e:
            logging.error(f"Claude API error: {e}")
            raise
