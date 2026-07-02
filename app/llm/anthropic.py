import logging
from anthropic import Anthropic

from app.config.settings import settings


class ClaudeLLM:
    def __init__(self, model: str = None):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = model or settings.ANTHROPIC_MODEL

    def chat(self, messages: list, max_tokens: int = 4096) -> str:
        try:
            kwargs = self._build_kwargs(messages, max_tokens)
            response = self.client.messages.create(**kwargs)
            return self._extract_text(response)
        except Exception as e:
            logging.error(f"Claude API error: {e}")
            raise

    def stream(self, messages: list, max_tokens: int = 4096):
        try:
            kwargs = self._build_kwargs(messages, max_tokens)
            kwargs["stream"] = True
            with self.client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield text
        except Exception as e:
            logging.error(f"Claude stream error: {e}")
            yield ""

    def _build_kwargs(self, messages: list, max_tokens: int) -> dict:
        system = None
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat_messages.append(m)

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
