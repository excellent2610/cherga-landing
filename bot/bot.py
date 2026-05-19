from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import config
from database import init_db, init_pool, close_pool, delete_master_by_chat_id
from handlers.admin import router as admin_router
from handlers.client import router as client_router
from handlers.common import router as common_router
from middlewares import AntiSpamMiddleware, LoggingMiddleware
from scheduler import setup_scheduler


async def main() -> None:
    if not config.bot_token or config.bot_token in {"PASTE_YOUR_TOKEN_HERE", "your_token_here"}:
        raise RuntimeError("Вкажіть BOT_TOKEN у config.py або в змінній середовища BOT_TOKEN.")

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    # БД (PostgreSQL pool)
    pool = await init_pool(config.db_path)
    await init_db(pool)

    # Тимчасово для тесту "нового користувача" (видалення 1 раз)
    # Після тесту — прибрати цей блок.
    marker = Path(__file__).resolve().parent / ".delete_master_once_done"
    if not marker.exists():
        await delete_master_by_chat_id(8608243726)
        try:
            marker.write_text("done", encoding="utf-8")
        except Exception:
            pass

    # Middlewares
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    dp.message.middleware(AntiSpamMiddleware(config.spam_limit_count, config.spam_limit_window_sec))
    dp.callback_query.middleware(AntiSpamMiddleware(config.spam_limit_count, config.spam_limit_window_sec))

    # Routers (порядок важливий: FSM майстра -> клієнт -> загальне)
    dp.include_router(admin_router)
    dp.include_router(client_router)
    dp.include_router(common_router)

    # Scheduler
    scheduler = setup_scheduler(bot)
    scheduler.start()

    print("Cherga bot started...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception:
        logging.exception("Critical error")
        raise
    finally:
        with suppress(Exception):
            scheduler.shutdown(wait=False)
        with suppress(Exception):
            await close_pool()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
