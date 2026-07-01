from app.llm.deepseek import LLM


class Agent:
    def __init__(self):
        self.name = "Chief of Staff"
        self.llm = LLM()

    def chat(self, message: str) -> str:
        print(f"[Agent] Received: {message}")

        response = self.llm.generate(message)

        print(f"[Agent] LLM Response: {response}")

        return response