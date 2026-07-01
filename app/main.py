from app.agent.agent import Agent
from app.llm.factory import create_llm

def main():
    llm = create_llm()
    agent = Agent(llm)

    response = agent.chat("Perkenalkan diri kamu dalam 1 kata.")

    print(response)


if __name__ == "__main__":
    main()