import asyncio
import json
import os
import queue
import re
import tempfile
import threading
import time
import urllib.request

from telegram import Update, InputMediaPhoto
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from app.config.settings import settings
from app.lib.events import log_event


class TelegramBot:
    def __init__(self, agent, memory, scheduler, watchers=None):
        self.agent = agent
        self.memory = memory
        self.scheduler = scheduler
        self._watchers = watchers
        self._app = None
        self._user_id = "507090539"  # ponytail: hardcoded biar notifikasi jalan walau belum ada chat masuk

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._process(update, str(update.message.from_user.id), update.message.text)

    def _transcribe(self, path: str) -> str:
        # ponytail: Groq pakai SDK openai via base_url — model Whisper sama, ~9x lebih murah
        from openai import OpenAI
        client = OpenAI(api_key=settings.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
        with open(path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=f,
                language="id",
            )
        return resp.text

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.message.from_user.id)
        voice = update.message.voice or update.message.audio
        await update.message.reply_chat_action(ChatAction.TYPING)

        tmp = os.path.join(tempfile.gettempdir(), f"vn_{voice.file_unique_id}.ogg")
        try:
            tg_file = await voice.get_file()
            await tg_file.download_to_drive(tmp)
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, self._transcribe, tmp)
        except Exception:
            await update.message.reply_text("Maaf, gagal proses suaranya. Coba lagi ya 🙏")
            return
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

        text = (text or "").strip()
        if not text:
            await update.message.reply_text("Hmm, suaranya gak kedengeran. Coba ulang?")
            return

        # Echo hasil transcribe biar keliatan kalau salah denger
        await update.message.reply_text(f'🎤 "{text}"')
        await self._process(update, user_id, text)

    async def _process(self, update: Update, user_id: str, message: str):
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
                # Live preview — cuma sampai batas aman Telegram; sisanya di-split pas final
                if full_text and len(full_text) < 3900 and time.time() - last_update > 1.2:
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
        display = re.sub(r'\[(?:VIDEO|IMAGE|FILE):.*?\]', '', display).strip()
        await self._send_long(sent_msg, update, display or "Maaf, ada error. Coba lagi nanti.")

        # Handle visual outputs FIRST — before memory filter
        await self._send_media(update, full_text)

        # Store in memory — skip visual outputs and fallback
        if "kesulitan memproses" in full_text or "Camera:" in full_text:
            return
        if "[VIDEO:" in full_text or "[IMAGE:" in full_text:
            return
        # File output: simpen teksnya tanpa marker (biar konteks inget udah bikin dok)
        clean = re.sub(r'\[FILE:.*?\]', '', full_text).strip()
        self.memory.add(user_id, "assistant", clean)

    @staticmethod
    def _chunk(text: str, size: int = 4000):
        """Pecah teks jadi potongan <= size, mecah di batas baris. Baris raksasa dipotong paksa."""
        out, cur = [], ""
        for line in text.split("\n"):
            while len(line) > size:  # satu baris kelewat panjang
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(line[:size])
                line = line[size:]
            candidate = line if not cur else cur + "\n" + line
            if len(candidate) > size:
                out.append(cur)
                cur = line
            else:
                cur = candidate
        if cur:
            out.append(cur)
        return out

    async def _send_long(self, sent_msg, update: Update, text: str):
        """Kirim teks yang bisa >4096 char: chunk pertama edit pesan '...', sisanya pesan baru."""
        chunks = self._chunk(text) or ["Maaf, ada error. Coba lagi nanti."]
        try:
            await sent_msg.edit_text(chunks[0])
        except Exception:
            pass
        for c in chunks[1:]:
            try:
                await update.message.reply_text(c)
            except Exception:
                pass

    async def _send_media(self, update: Update, raw: str):
        image_paths = re.findall(r'\[IMAGE:(.*?)\]', raw)
        video_paths = re.findall(r'\[VIDEO:(.*?)\]', raw)
        file_paths = re.findall(r'\[FILE:(.*?)\]', raw)

        valid_imgs = [p for p in image_paths if os.path.exists(p)]
        valid_vids = [p for p in video_paths if os.path.exists(p)]

        for fp in [p for p in file_paths if os.path.exists(p)]:
            try:
                await update.message.reply_document(document=open(fp, 'rb'))
            except Exception:
                pass

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

    def send_proactive(self, text: str):
        """Send an agent-initiated message AND record it, so the conversation
        has context when the user replies to it."""
        self._send_to_user(text)
        self.memory.add(self._user_id, "assistant", text)
        log_event("proactive", text[:120])

    def _on_scheduled(self, user_id: str, message: str):
        log_event("reminder", message[:120])
        if message == "__morning_brief__":
            build = getattr(self.scheduler, "morning_brief", None)
            text = build() if build else None
            if text:
                self.send_proactive(text)
            return
        text = self.agent.phrase(
            f"Reminder '{message}' due sekarang. Ingetin Rendy santai, satu kalimat pendek, "
            "kayak temen yang nepok pundak. Jangan template."
        ) or f"⏰ Eh bro, {message.lower()}! Jangan lupa ya 😄"
        self.send_proactive(text)

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
        self._app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._handle_voice))
        self._app.add_handler(CommandHandler("help", self._handle_help))
        self._app.add_handler(CommandHandler("start", self._handle_help))

        self.scheduler._on_notify = self._on_scheduled
        self.scheduler.start()

        if self._watchers:
            self._watchers.on_alert = self.send_proactive
            self._watchers.start()  # baru start thread setelah on_alert kesambung

        print("Telegram bot running...")
        self._app.run_polling()
