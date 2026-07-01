from app.agent.agent import Agent


def main():
    agent = Agent()

    response = agent.chat("Perkenalkan diri kamu dalam 10 kata.")

    print(response)


if __name__ == "__main__":
    main()