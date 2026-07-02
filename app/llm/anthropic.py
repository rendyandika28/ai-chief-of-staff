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

            response = self.client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            logging.error(f"Claude API error: {e}")
            raise
