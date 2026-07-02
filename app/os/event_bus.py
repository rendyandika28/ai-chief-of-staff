"""In-process async event bus. Pub/sub, priority-ordered handlers, error isolation."""

import asyncio
import logging
from collections import defaultdict
from typing import Callable

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[tuple[int, Callable]]] = defaultdict(list)

    def on(self, event_type: str, handler: Callable, priority: int = 0):
        self._handlers[event_type].append((priority, handler))
        self._handlers[event_type].sort(key=lambda x: x[0])

    def emit(self, event_type: str, payload: dict, user_id: str = ""):
        handlers = list(self._handlers.get(event_type, []))

        async def _run():
            for _, handler in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(payload, self)
                    else:
                        handler(payload, self)
                except Exception as e:
                    logger.error(f"Handler error for {event_type}: {e}")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_run())
        except RuntimeError:
            for _, handler in handlers:
                try:
                    handler(payload, self)
                except Exception as e:
                    logger.error(f"Handler error for {event_type}: {e}")
