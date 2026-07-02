from openai import OpenAI

from app.config.settings import settings


class DeepSeekLLM:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )

    def chat(self, messages: list) -> str:
        response = self.client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=messages,
        )

        return response.choices[0].message.content
