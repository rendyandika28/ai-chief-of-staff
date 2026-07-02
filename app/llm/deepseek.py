from openai import OpenAI
import logging

from app.config.settings import settings


class DeepSeekLLM:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        try:
            response = self.client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60,
            )
            return response.choices[0].message.content
        except Exception as e:
            logging.error(f"DeepSeek API error: {e}")
            raise
