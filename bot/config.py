from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


# Підхоплюємо `.env` (якщо він існує) — безпечний no-op, якщо файлу немає
load_dotenv()


@dataclass(frozen=True)
class Config:
    # Telegram bot token
    bot_token: str = os.getenv("BOT_TOKEN", "your_token_here")

    # Юзернейм бота без @ (потрібен для deep links)
    bot_username: str = os.getenv("BOT_USERNAME", "chergaa_bot")

    # PostgreSQL connection string
    database_url: str = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/cherga_db")

    # Таймзона (за замовчуванням — Україна)
    timezone: str = os.getenv("TIMEZONE", "Europe/Kyiv")

    # FREE ліміти (можна змінити без правок коду)
    free_appointments_per_month: int = int(os.getenv("FREE_APPOINTMENTS_PER_MONTH", "30"))

    # Антиспам
    spam_limit_count: int = int(os.getenv("SPAM_LIMIT_COUNT", "10"))
    spam_limit_window_sec: int = int(os.getenv("SPAM_LIMIT_WINDOW_SEC", "10"))


config = Config()

# Backward-compatible alias для існуючого коду (scheduler/handlers)
object.__setattr__(config, "db_path", config.database_url)
