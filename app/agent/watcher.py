"""Watcher system — periodic background monitoring agents."""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class WatcherManager:
    def __init__(self, on_alert=None):
        self.on_alert = on_alert  # set later by the interface (e.g. TelegramBot)
        self._running = True  # start immediately

    def register(self, watcher, interval_seconds: int):
        t = threading.Thread(target=self._loop, args=(watcher, interval_seconds), daemon=True)
        t.start()

    def stop(self):
        self._running = False

    def _loop(self, watcher, interval_seconds: int):
        while self._running:
            try:
                result = watcher()
                if result and self.on_alert:
                    self.on_alert(result)
            except Exception as e:
                logger.error(f"Watcher error: {e}")
            time.sleep(interval_seconds)
