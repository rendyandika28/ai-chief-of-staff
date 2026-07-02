import os


class Settings:
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL")
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


settings = Settings()
