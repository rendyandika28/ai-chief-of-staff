from app.app import create_core
from app.interfaces.telegram import TelegramBot


def main():
    agent, memory, scheduler, watchers = create_core()
    bot = TelegramBot(agent, memory, scheduler, watchers)
    bot.run()


if __name__ == "__main__":
    main()
