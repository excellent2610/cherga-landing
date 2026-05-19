from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery


class LoggingMiddleware(BaseMiddleware):
    # Мінімальне логування: тільки критичні помилки ловимо в bot.py
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        return await handler(event, data)


class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, limit_count: int = 10, window_sec: int = 10) -> None:
        self.limit_count = limit_count
        self.window_sec = window_sec
        self._events: Dict[int, Deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        if isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id is not None:
            now = time.monotonic()
            q = self._events[user_id]
            q.append(now)
            # чистимо старі
            while q and (now - q[0]) > self.window_sec:
                q.popleft()
            if len(q) > self.limit_count:
                # Антиспам-повідомлення тільки для message/callback
                try:
                    if isinstance(event, Message):
                        await event.answer("🐢 Трішки повільніше, будь ласка. Спробуйте ще раз через кілька секунд.")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("🐢 Трішки повільніше 🙂", show_alert=False)
                except Exception:
                    pass
                return None

        return await handler(event, data)
