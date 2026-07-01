class Agent:
    def __init__(self, llm):
        self.name = "Chief of Staff"
        self.llm = llm

    def chat(self, message: str) -> str:
        print(f"[Agent] Received: {message}")

        response = self.llm.generate(message)

        print(f"[Agent] LLM Response: {response}")

        return response