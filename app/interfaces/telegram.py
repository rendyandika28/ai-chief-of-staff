import asyncio

from telegram import Update, BotCommand
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from app.config.settings import settings


class TelegramBot:
    def __init__(self, agent, memory, scheduler, event_bus=None, watchers=None):
        self.agent = agent
        self.memory = memory
        self.scheduler = scheduler
        self._bus = event_bus
        self._watchers = watchers
        self._app = None
        self._user_id = None

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.message.from_user.id)
        message = update.message.text

        if self._user_id is None:
            self._user_id = user_id

        self.memory.add(user_id, "user", message)
        response = self.agent.chat(user_id, message)
        self.memory.add(user_id, "assistant", response)

        await update.message.reply_text(response)

    def _send_to_user(self, text: str):
        if self._app is None or self._user_id is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._app.bot.send_message(chat_id=int(self._user_id), text=text),
                asyncio.get_event_loop(),
            )
        except Exception:
            pass

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "AI Chief of Staff — asisten pribadi Rendy.\n\n"
            "Bisa ngobrol santai, bantu cari info, eksekusi tools.\n\n"
            "Fitur:\n"
            "- Chat natural (ngobrol biasa)\n"
            "- Weather (cuaca kota)\n"
            "- Browser (browsing web)\n"
            "- Job hunt (cari lowongan multi-platform)\n"
            "- CCTV Jogja (pantau lalu lintas)\n"
            "- Reminder (pengingat otomatis)\n"
            "- File management\n"
            "- HTTP requests\n"
            "- Calculator\n"
            "- Auto-apply job\n\n"
            "Commands: /help /start"
        )
        await update.message.reply_text(text)

    def run(self):
        self._app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self._app.add_handler(CommandHandler("help", self._handle_help))
        self._app.add_handler(CommandHandler("start", self._handle_help))

        self.scheduler._on_notify = lambda uid, msg: self._send_to_user(f"\u23f0 Pengingat: {msg}")
        self.scheduler.start()

        # Register command suggestions (Telegram auto-complete)
        async def _set_commands():
            await self._app.bot.set_my_commands([
                BotCommand("help", "Lihat fitur & panduan"),
                BotCommand("start", "Mulai ulang bot"),
            ])
        asyncio.run(_set_commands())

        if self._bus:
            self._bus.on("watcher.alert", lambda payload, bus: self._send_to_user(f"\U0001f514 {payload['message']}"))

        print("Telegram bot running...")
        self._app.run_polling()
