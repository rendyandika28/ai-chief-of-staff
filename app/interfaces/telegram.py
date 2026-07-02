import asyncio
import json
import os
import queue
import re
import threading
import time
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
        sent_msg = await update.message.reply_text("...")

        # Stream tokens via thread-safe queue
        token_queue = queue.Queue()
        error_ref = []

        def run_agent():
            try:
                for token in self.agent.chat_stream(user_id, message):
                    token_queue.put(token)
                token_queue.put(None)  # sentinel
            except Exception as e:
                error_ref.append(str(e))
                token_queue.put(None)

        loop = asyncio.get_running_loop()
        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()

        full_text = ""
        last_update = time.time()
        last_action = time.time()
        while True:
            try:
                token = token_queue.get(timeout=0.5)
            except queue.Empty:
                # Keep typing animation alive
                if time.time() - last_action > 4:
                    await update.message.reply_chat_action(ChatAction.TYPING)
                    last_action = time.time()
                # Update message periodically
                if full_text and time.time() - last_update > 0.3:
                    try:
                        await sent_msg.edit_text(full_text + " ✍️")
                        last_update = time.time()
                    except Exception:
                        pass
                continue

            if token is None:
                break
            full_text += token

        # Final update — strip markers from display text
        display = full_text
        if error_ref:
            display = "Maaf, ada error. Coba lagi nanti."
        display = re.sub(r'\[(?:VIDEO|IMAGE):.*?\]', '', display).strip()
        await sent_msg.edit_text(display or "Maaf, ada error. Coba lagi nanti.")

        # Handle visual outputs FIRST — before memory filter
        await self._send_media(update, full_text)

        # Store in memory — skip visual outputs and fallback
        if "kesulitan memproses" in full_text or "Camera:" in full_text:
            return
        if "[VIDEO:" in full_text or "[IMAGE:" in full_text:
            return
        self.memory.add(user_id, "assistant", full_text.strip())

    async def _send_media(self, update: Update, raw: str):
        image_paths = re.findall(r'\[IMAGE:(.*?)\]', raw)
        video_paths = re.findall(r'\[VIDEO:(.*?)\]', raw)

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
            "- CCTV Jogja (pantau lalu lintas)\n"
            "- Job hunt (cari lowongan multi-platform)\n"
            "- Reminder (pengingat otomatis)\n\n"
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
