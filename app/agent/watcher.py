"""Watcher system — periodic background monitoring agents."""

import logging
import threading
import time

from app.lib.events import log_event

logger = logging.getLogger(__name__)


class WatcherManager:
    def __init__(self, on_alert=None):
        self.on_alert = on_alert  # set later by the interface (e.g. TelegramBot)
        self.on_invite = None     # structured invite→card+buttons sender (set by interface)
        self._running = True
        self._pending = []  # (watcher, interval) — dijalanin pas start()

    def register(self, watcher, interval_seconds: int):
        # Simpen dulu; jangan start thread sampe on_alert kesambung (lihat start()).
        self._pending.append((watcher, interval_seconds))

    def start(self):
        """Dipanggil interface SETELAH on_alert di-set, biar tick pertama gak
        ilang gara-gara alert dibuang (on_alert masih None)."""
        for watcher, interval in self._pending:
            threading.Thread(target=self._loop, args=(watcher, interval), daemon=True).start()
        self._pending = []

    def stop(self):
        self._running = False

    def _loop(self, watcher, interval_seconds: int):
        name = getattr(watcher, "__name__", "watcher")
        last_err = None  # dedup: don't spam events.db when the same error repeats
        while self._running:
            try:
                result = watcher()
                if result and self.on_alert:
                    self.on_alert(result)
                last_err = None  # recovered → next distinct error logs again
            except Exception as e:
                logger.error(f"Watcher {name} error: {e}")
                msg = f"{name}: {e}"
                if msg != last_err:  # surface silent failures, once per distinct error
                    log_event("error", f"watcher {msg}")
                    last_err = msg
            time.sleep(interval_seconds)
