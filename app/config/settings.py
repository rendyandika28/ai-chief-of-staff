from dotenv import load_dotenv
import os

load_dotenv()


class Settings:
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL")
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")


settings = Settings()