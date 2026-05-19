from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types.error_event import ErrorEvent
import traceback

from config import config
from database import connect, create_master, ensure_master, get_master_by_chat, get_master_by_chat_id
from keyboards import kb_main_menu


router = Router()


def _kb_role_pick():
    kb = InlineKeyboardBuilder()
    kb.button(text="👨‍💼 Я майстер", callback_data="role:master")
    kb.button(text="👤 Я клієнт", callback_data="role:client")
    kb.adjust(2)
    return kb.as_markup()


@router.message(CommandStart())
async def cmd_start_master(message: Message) -> None:
    # /start без параметрів — завжди майстерське меню
    user = message.from_user
    if not user:
        await message.answer("😅 Не бачу ваш профіль. Спробуйте ще раз /start.")
        return

    # Важливо: тут НЕ створюємо майстра автоматично.
    existing = await get_master_by_chat_id(user.id)
    if not existing:
        await message.answer("👋 Вітаю! Хто ви?", reply_markup=_kb_role_pick())
        return

    # Якщо майстер вже існує — оновимо ім'я/юзернейм і покажемо меню
    db = await connect(config.db_path)
    try:
        master = await ensure_master(db, chat_id=user.id, name=user.full_name, username=user.username)
    finally:
        await db.close()

    text = (
        f"👋 Привіт, <b>{master.name}</b>!\n\n"
        "Я — <b>Черга</b> 🙂 Допоможу записувати клієнтів і автоматично нагадувати їм у Telegram.\n\n"
        "Обирайте, що зробимо:"
    )
    await message.answer(text, reply_markup=kb_main_menu(), parse_mode="HTML")


@router.callback_query(F.data == "role:client")
async def cb_role_client(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(
        "👤 Ок!\n\nЩоб записатись, попросіть майстра надіслати вам персональне посилання 👆"
    )


@router.callback_query(F.data == "role:master")
async def cb_role_master(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        await call.message.answer("😅 Спробуйте ще раз /start.")
        return
    db = await connect(config.db_path)
    try:
        master = await create_master(db, chat_id=user.id, name=user.full_name, username=user.username)
    finally:
        await db.close()
    text = (
        f"👋 Привіт, <b>{master.name}</b>!\n\n"
        "Я — <b>Черга</b> 🙂 Допоможу записувати клієнтів і автоматично нагадувати їм у Telegram.\n\n"
        "Обирайте, що зробимо:"
    )
    await call.message.answer(text, reply_markup=kb_main_menu(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🧩 <b>Допомога</b>\n\n"
        "• /start — головне меню\n"
        "• Створити запис: «➕ Новий запис»\n"
        "• Клієнту надішліть посилання з токеном\n\n"
        "Якщо щось пішло не так — просто введіть /start 🙂",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.message(StateFilter(None))
async def fallback_message(message: Message) -> None:
    # Короткий fallback для клієнта після /start TOKEN, якщо він написав текст
    text = (message.text or "").strip()
    if text and len(text) >= 6 and text.isalnum():
        # може бути токен
        from .client import open_token

        await open_token(message, text.upper())
        return

    await message.answer("🤝 Я тут. Натисніть /start, щоб відкрити меню.")


@router.errors()
async def on_error(event: ErrorEvent) -> None:
    # Загальна обробка помилок: не світимо технічні деталі користувачу
    try:
        print(f"ПОМИЛКА (global): {event.exception}", flush=True)
        print("".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__)), flush=True)
    except Exception:
        pass
    try:
        upd = event.update
        if hasattr(upd, "message") and upd.message:
            await upd.message.answer("😕 Щось пішло не так. Спробуйте /start.")
        elif hasattr(upd, "callback_query") and upd.callback_query:
            await upd.callback_query.message.answer("😕 Щось пішло не так. Спробуйте /start.")
    except Exception:
        pass
