import asyncio
import re

from telegram import Update, InputMediaPhoto
from telegram.constants import ChatAction
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

        await update.message.reply_chat_action(ChatAction.TYPING)
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(None, self.agent.chat, user_id, message)
        except Exception as e:
            response = "Maaf, ada error. Coba lagi nanti."
            print(f"Agent error: {e}")

        self.memory.add(user_id, "assistant", response)

        await self._send_response(update, response)

    async def _send_response(self, update: Update, raw: str):
        # Strip [IMAGE:path] markers from text, send them as photos
        text = raw
        image_paths = re.findall(r'\[IMAGE:(.*?)\]', raw)
        text = re.sub(r'\[IMAGE:.*?\]', '', text).strip()
        text = re.sub(r'\n{3,}', '\n\n', text)

        if text:
            await update.message.reply_text(text)

        valid_paths = []
        for path in image_paths:
            import os
            if os.path.exists(path):
                valid_paths.append(path)

        if len(valid_paths) == 1:
            try:
                await update.message.reply_photo(photo=open(valid_paths[0], 'rb'))
            except Exception:
                pass
        elif len(valid_paths) > 1:
            try:
                media = [InputMediaPhoto(media=open(p, 'rb')) for p in valid_paths]
                await update.message.reply_media_group(media=media)
            except Exception:
                pass

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
        async def _post_init(app):
            await app.bot.set_my_commands([
                ("help", "Lihat fitur & panduan"),
                ("start", "Mulai ulang bot"),
            ])

        self._app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .post_init(_post_init)
            .build()
        )
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self._app.add_handler(CommandHandler("help", self._handle_help))
        self._app.add_handler(CommandHandler("start", self._handle_help))

        self.scheduler._on_notify = lambda uid, msg: self._send_to_user(f"\u23f0 Pengingat: {msg}")
        self.scheduler.start()

        if self._bus:
            self._bus.on("watcher.alert", lambda payload, bus: self._send_to_user(f"\U0001f514 {payload['message']}"))

        print("Telegram bot running...")
        self._app.run_polling()
