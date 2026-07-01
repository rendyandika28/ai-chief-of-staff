from openai import OpenAI

from app.config.settings import settings

from app.llm.base import BaseLLM

class DeepSeekLLM(BaseLLM):
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )

    def generate(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        return response.choices[0].message.content