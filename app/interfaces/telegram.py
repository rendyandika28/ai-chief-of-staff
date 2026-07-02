import asyncio
import json
import os
import re
import urllib.request

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

        await self._send_response(update, response)

        # Store in memory — but skip visual tool outputs and fallback
        if "kesulitan memproses" in response:
            return
        if "[VIDEO:" in response or "[IMAGE:" in response:
            return
        if response.startswith(("[cctv]", "[traffic]", "[browser]")):
            return
        self.memory.add(user_id, "assistant", response.strip())

    async def _send_response(self, update: Update, raw: str):
        text = raw
        image_paths = re.findall(r'\[IMAGE:(.*?)\]', raw)
        video_paths = re.findall(r'\[VIDEO:(.*?)\]', raw)
        text = re.sub(r'\[(?:IMAGE|VIDEO):.*?\]', '', text)
        text = re.sub(r'^\[[a-z_]+\]\s*', '', text)  # strip [tool_name] prefix
        text = re.sub(r'\n{3,}', '\n\n', text).strip()

        if text:
            await update.message.reply_text(text)

        valid_imgs = [p for p in image_paths if os.path.exists(p)]
        valid_vids = [p for p in video_paths if os.path.exists(p)]

        if valid_imgs:
            try:
                if len(valid_imgs) == 1:
                    await update.message.reply_photo(photo=open(valid_imgs[0], 'rb'))
                else:
                    media = [InputMediaPhoto(media=open(p, 'rb')) for p in valid_imgs]
                    await update.message.reply_media_group(media=media)
            except Exception:
                pass

        for vp in valid_vids:
            try:
                await update.message.reply_video(video=open(vp, 'rb'), supports_streaming=True)
            except Exception:
                pass

    def _send_to_user(self, text: str):
        if self._app is None or self._user_id is None:
            return
        try:
            token = settings.TELEGRAM_BOT_TOKEN
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({"chat_id": int(self._user_id), "text": text}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
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

        self.scheduler._on_notify = lambda uid, msg: (
            self._send_to_user(f"⏰ Eh bro, {msg.lower()}! Jangan lupa ya 😄")
        )
        self.scheduler.start()

        if self._bus:
            self._bus.on("watcher.alert", lambda payload, bus: self._send_to_user(f"\U0001f514 {payload['message']}"))

        print("Telegram bot running...")
        self._app.run_polling()
