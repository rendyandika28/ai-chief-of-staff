from app.app import create_core
from app.interfaces.telegram import TelegramBot


def main():
    agent, memory, scheduler, event_bus, goal_manager, watchers = create_core()
    bot = TelegramBot(agent, memory, scheduler, event_bus, watchers)
    bot.run()


if __name__ == "__main__":
    main()
