from app.config.settings import settings
from app.llm.deepseek import DeepSeekLLM


def create_llm():
    if settings.LLM_PROVIDER == "deepseek":
        return DeepSeekLLM()

    raise ValueError(f"Unknown LLM provider: {settings.LLM_PROVIDER}")