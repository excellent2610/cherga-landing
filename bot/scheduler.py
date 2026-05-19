from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import config
from database import connect, due_reminders, get_master_by_id, get_master_settings, mark_reminder_sent, parse_iso


def _fmt_dt_local(iso_dt: str, tz_name: str) -> str:
    dt = parse_iso(iso_dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz_name))
    return local.strftime("%d.%m.%Y о %H:%M")


async def _send_reminder(bot: Bot, *, appt, kind: str) -> None:
    db = await connect(config.db_path)
    try:
        master = await get_master_by_id(db, appt.master_id)
        if not master:
            return
        settings = await get_master_settings(db, master.id)
        tz_name = config.timezone
        when = _fmt_dt_local(appt.appointment_time, tz_name)

        # Текст для клієнта (кастомний доступний у Pro)
        reminder_text = settings.get("reminder_text")
        if master.plan != "pro":
            reminder_text = None

        if reminder_text:
            text = reminder_text.format(client=appt.client_name, when=when, master=master.name)
        else:
            text = (
                f"🔔 <b>Нагадування</b>\n\n"
                f"Привіт, <b>{appt.client_name}</b>! У вас запис до <b>{master.name}</b> {when}.\n"
                f"Якщо плани змінились — напишіть майстру, будь ласка."
            )

        await bot.send_message(appt.client_chat_id, text, parse_mode="HTML")
        await mark_reminder_sent(db, appt.id, kind=kind)

        # Підтвердження майстру
        k = "за 2 години" if kind == "2h" else "за 30 хв"
        await bot.send_message(
            master.chat_id,
            f"✅ Нагадування <b>{k}</b> надіслано клієнту <b>{appt.client_name}</b> ({when}).",
            parse_mode="HTML",
        )
    finally:
        await db.close()


async def _warn_unconfirmed(bot: Bot) -> None:
    # Якщо клієнт не підключився (client_chat_id IS NULL) і до візиту лишилось ~2:10, попередимо майстра.
    db = await connect(config.db_path)
    try:
        now = datetime.now(timezone.utc)
        target = now + timedelta(hours=2, minutes=10)
        end = target + timedelta(seconds=65)
        # PostgreSQL синтаксис: $1, $2 + fetch замість execute
        rows = await db.fetch(
            """
            SELECT * FROM appointments
            WHERE status = 'pending'
              AND client_chat_id IS NULL
              AND appointment_time >= $1
              AND appointment_time < $2
            ORDER BY appointment_time ASC
            """,
            target.replace(tzinfo=None),
            end.replace(tzinfo=None),
        )
        for r in rows:
            appt = type("A", (), dict(r))  # простий об'єкт
            master = await get_master_by_id(db, appt.master_id)
            if not master:
                continue
            when = _fmt_dt_local(appt.appointment_time, config.timezone)
            await bot.send_message(
                master.chat_id,
                (
                    f"⚠️ Клієнт <b>{appt.client_name}</b> ще не підтвердив запис на {when}.\n"
                    f"Можливо варто зателефонувати 🙂"
                ),
                parse_mode="HTML",
            )
    finally:
        await db.close()


async def check_reminders(bot: Bot) -> None:
    # Запускається щохвилини
    now = datetime.now(timezone.utc)
    db = await connect(config.db_path)
    try:
        # 2 години — для всіх планів, якщо увімкнено
        due_2h = await due_reminders(db, now=now, kind="2h")
        for appt in due_2h:
            master = await get_master_by_id(db, appt.master_id)
            if not master:
                continue
            settings = await get_master_settings(db, master.id)
            if int(settings.get("reminder_2h_enabled", 1)) != 1:
                continue
            await _send_reminder(bot, appt=appt, kind="2h")

        # 30 хв — тільки Pro, якщо увімкнено
        due_30m = await due_reminders(db, now=now, kind="30m")
        for appt in due_30m:
            master = await get_master_by_id(db, appt.master_id)
            if not master or master.plan != "pro":
                continue
            settings = await get_master_settings(db, master.id)
            if int(settings.get("reminder_30m_enabled", 0)) != 1:
                continue
            await _send_reminder(bot, appt=appt, kind="30m")
    finally:
        await db.close()

    # Окремо — попередження, якщо клієнт ще не підключився
    await _warn_unconfirmed(bot)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(check_reminders, "interval", minutes=1, args=(bot,), id="check_reminders", max_instances=1)
    return scheduler
