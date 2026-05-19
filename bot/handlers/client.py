from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import Message, CallbackQuery

from config import config
from database import connect, get_appointment_by_token, get_master_by_id, increment_client_visits, set_appointment_client_chat, set_appointment_status, upsert_client
from keyboards import kb_client_cancel_confirm, kb_client_confirm
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from database import parse_iso


router = Router()


def _fmt_local(dt) -> str:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    kyiv = ZoneInfo("Europe/Kyiv")
    if dt.tzinfo is not None:
        dt = dt.astimezone(kyiv)
    return dt.strftime("%d.%m.%Y о %H:%M")


@router.message(CommandStart(deep_link=True))
async def client_start(message: Message, command: CommandObject) -> None:
    # Важливо: deep link обробляємо тільки якщо справді є args
    # Інакше цей хендлер не має перехоплювати звичайні /start або будь-які повідомлення у FSM
    if not command.args or len(command.args.strip()) < 4:
        return
    await open_token(message, command.args.strip())


async def open_token(message: Message, token: str) -> None:
    token = token.strip().upper()
    db = await connect(config.db_path)
    try:
        appt = await get_appointment_by_token(db, token)
        if not appt:
            await message.answer("❌ Посилання недійсне. Попросіть майстра надіслати нове.")
            return
        master = await get_master_by_id(db, appt.master_id)
        if not master:
            await message.answer("😕 Не знайшов майстра для цього запису. Попросіть майстра створити запис заново.")
            return
        when = _fmt_local(appt.appointment_time)
        status = appt.status
        status_emoji = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌", "completed": "🏁"}.get(status, "ℹ️")
        text = (
            f"👋 Привіт, <b>{appt.client_name}</b>!\n\n"
            f"Ваш запис до майстра <b>{master.name}</b>:\n"
            f"🗓️ <b>{when}</b>\n"
            f"Статус: {status_emoji} <b>{status}</b>\n\n"
            "Підтверджуєте візит?"
        )
        await message.answer(text, reply_markup=kb_client_confirm(token), parse_mode="HTML")
    finally:
        await db.close()


@router.callback_query(F.data.startswith("client:open:"))
async def cb_open(call: CallbackQuery) -> None:
    token = (call.data or "").split(":", 2)[2]
    await call.answer()
    msg = call.message
    if isinstance(msg, Message):
        await open_token(msg, token)


@router.callback_query(F.data.startswith("client:confirm:"))
async def cb_confirm(call: CallbackQuery) -> None:
    token = (call.data or "").split(":", 2)[2].strip().upper()
    user = call.from_user
    if not user:
        await call.answer("😅 Спробуйте ще раз.", show_alert=True)
        return

    db = await connect(config.db_path)
    try:
        appt = await get_appointment_by_token(db, token)
        if not appt:
            await call.message.answer("❌ Посилання недійсне.")
            return
        master = await get_master_by_id(db, appt.master_id)
        if not master:
            await call.message.answer("😕 Не знайшов майстра.")
            return

        if appt.status == "cancelled":
            await call.message.answer("❌ Цей запис вже скасовано.")
            await call.answer("Вже скасовано")
            return

        if appt.status != "confirmed":
            # Прив'язуємо клієнта
            await set_appointment_client_chat(db, appt.id, user.id)
            await set_appointment_status(db, appt.id, appt.master_id, "confirmed")
            await upsert_client(db, chat_id=user.id, master_id=appt.master_id, name=user.full_name, phone=None)
            # Для MVP рахуємо «візити» як підтвердження
            await increment_client_visits(db, user.id)

        when = _fmt_local(appt.appointment_time)
        await call.message.answer(
            "✅ <b>Запис підтверджено!</b>\n\n"
            f"Майстер: <b>{master.name}</b>\n"
            f"Час: <b>{when}</b>\n\n"
            "Нагадуємо за 2 години 😊",
            parse_mode="HTML",
        )
        await call.answer("Підтверджено ✅")

        # Сповіщаємо майстра
        await call.bot.send_message(
            master.chat_id,
            f"✅ Клієнт <b>{appt.client_name}</b> підтвердив запис на <b>{when}</b>.",
            parse_mode="HTML",
        )
    finally:
        await db.close()


@router.callback_query(F.data.startswith("client:cancel:"))
async def cb_cancel(call: CallbackQuery) -> None:
    token = (call.data or "").split(":", 2)[2].strip().upper()
    await call.answer()
    await call.message.answer("❓ Точно скасувати запис?", reply_markup=kb_client_cancel_confirm(token))


@router.callback_query(F.data.startswith("client:cancel_yes:"))
async def cb_cancel_yes(call: CallbackQuery) -> None:
    token = (call.data or "").split(":", 2)[2].strip().upper()
    db = await connect(config.db_path)
    try:
        appt = await get_appointment_by_token(db, token)
        if not appt:
            await call.message.answer("❌ Посилання недійсне.")
            return
        master = await get_master_by_id(db, appt.master_id)
        if master:
            when = _fmt_local(appt.appointment_time)
            await call.bot.send_message(
                master.chat_id,
                f"❌ Клієнт <b>{appt.client_name}</b> скасував запис на <b>{when}</b>.",
                parse_mode="HTML",
            )
        await set_appointment_status(db, appt.id, appt.master_id, "cancelled")
        await call.message.answer("❌ Запис скасовано. Дякуємо, що попередили!")
        await call.answer("Скасовано ❌")
    finally:
        await db.close()
