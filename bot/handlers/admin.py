from __future__ import annotations

import logging
import traceback
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import config
from database import (
    connect,
    count_appointments,
    count_month_appointments,
    create_appointment,
    get_appointment_by_id,
    get_master_by_chat,
    get_master_settings,
    get_stats_month,
    list_appointments,
    list_appointments_for_day,
    list_clients,
    list_client_appointments,
    set_appointment_status,
    set_master_name,
    update_master_settings,
    utcnow,
)
from keyboards import (
    kb_appt_actions,
    kb_appt_details,
    kb_back,
    kb_cancel_confirm,
    kb_clients_menu,
    kb_confirm_new,
    kb_date_pick,
    kb_filter_pager,
    kb_main_menu,
    kb_pro_menu,
    kb_reminder_settings,
    kb_settings_menu,
    kb_share_and_go,
)


router = Router()


def _local_now() -> datetime:
    return datetime.now(ZoneInfo(config.timezone))


def _to_utc(local_dt: datetime) -> datetime:
    if local_dt.tzinfo is None:
        local_dt = local_dt.replace(tzinfo=ZoneInfo(config.timezone))
    return local_dt.astimezone(timezone.utc)


def _fmt_local(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo(config.timezone)).strftime("%d.%m.%Y о %H:%M")


def _as_dt(value: object) -> datetime:
    # Після переходу на PostgreSQL appointment_time приходить як datetime,
    # але на всякий випадок підтримуємо і старий ISO-рядок.
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Unsupported datetime value: {type(value)}")


def _day_bounds_local(d: datetime) -> tuple[datetime, datetime]:
    day = d.astimezone(ZoneInfo(config.timezone))
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return _to_utc(start), _to_utc(end)


def _status_label(status: str) -> str:
    return {"pending": "⏳ очікує", "confirmed": "✅ підтверджено", "cancelled": "❌ скасовано", "completed": "🏁 завершено"}.get(
        status, f"ℹ️ {status}"
    )


class NewAppt(StatesGroup):
    client_name = State()
    client_phone = State()
    date_choice = State()
    date_other = State()
    time = State()
    confirm = State()


class Settings(StatesGroup):
    name = State()
    reminder_text = State()
    clients_search = State()


@router.callback_query(F.data == "admin:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.answer("🏠 <b>Головне меню</b>", reply_markup=kb_main_menu(), parse_mode="HTML")


@router.callback_query(F.data == "admin:new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(NewAppt.client_name)
    await call.message.answer("➕ <b>Новий запис</b>\n\nВведіть ім'я клієнта:", parse_mode="HTML", reply_markup=kb_back())


@router.message(NewAppt.client_name)
async def st_client_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("😅 Ім'я виглядає занадто коротким. Спробуйте ще раз:")
        return
    await state.update_data(client_name=name)
    await state.set_state(NewAppt.client_phone)
    await message.answer("📞 Введіть телефон клієнта (або /skip):", reply_markup=kb_back())


@router.message(NewAppt.client_phone, Command("skip"))
async def st_phone_skip(message: Message, state: FSMContext) -> None:
    await state.update_data(client_phone=None)
    await state.set_state(NewAppt.date_choice)
    await message.answer("🗓️ Оберіть дату:", reply_markup=kb_date_pick())


@router.message(NewAppt.client_phone)
async def st_client_phone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    if phone and len(phone) < 6:
        await message.answer("📞 Телефон виглядає дивно. Введіть ще раз або /skip:")
        return
    await state.update_data(client_phone=phone)
    await state.set_state(NewAppt.date_choice)
    await message.answer("🗓️ Оберіть дату:", reply_markup=kb_date_pick())


@router.callback_query(NewAppt.date_choice, F.data.startswith("admin:new:date:"))
async def cb_date_pick(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    choice = (call.data or "").split(":")[-1]
    if choice == "today":
        d = _local_now().date()
        await state.update_data(date=str(d))
        await state.set_state(NewAppt.time)
        await call.message.answer("⏰ Введіть час у форматі <b>ГГ:ХХ</b> (наприклад 18:30):", parse_mode="HTML", reply_markup=kb_back())
        return
    if choice == "tomorrow":
        d = (_local_now() + timedelta(days=1)).date()
        await state.update_data(date=str(d))
        await state.set_state(NewAppt.time)
        await call.message.answer("⏰ Введіть час у форматі <b>ГГ:ХХ</b>:", parse_mode="HTML", reply_markup=kb_back())
        return
    if choice == "other":
        await state.set_state(NewAppt.date_other)
        await call.message.answer("🗓️ Введіть дату у форматі <b>ДД.ММ.РРРР</b> (наприклад 21.05.2026):", parse_mode="HTML", reply_markup=kb_back())
        return


@router.message(NewAppt.date_other)
async def st_date_other(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        d = datetime.strptime(raw, "%d.%m.%Y").date()
    except Exception:
        await message.answer("❌ Невірний формат. Спробуйте ще раз: <b>ДД.ММ.РРРР</b>", parse_mode="HTML")
        return
    await state.update_data(date=str(d))
    await state.set_state(NewAppt.time)
    await message.answer("⏰ Введіть час у форматі <b>ГГ:ХХ</b>:", parse_mode="HTML", reply_markup=kb_back())


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@router.message(NewAppt.time)
async def st_time(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    m = _TIME_RE.match(raw)
    if not m:
        await message.answer("❌ Невірний формат часу. Введіть як <b>18:30</b>:", parse_mode="HTML")
        return
    hh = int(m.group(1))
    mm = int(m.group(2))
    data = await state.get_data()
    date_s = data.get("date")
    if not date_s:
        await message.answer("😅 Дата загубилась. Почнімо заново: /start")
        await state.clear()
        return
    y, mo, d = map(int, date_s.split("-"))
    local_dt = datetime(y, mo, d, hh, mm, tzinfo=ZoneInfo(config.timezone))
    appt_utc = _to_utc(local_dt)
    if appt_utc <= utcnow():
        await message.answer("⚠️ Це час у минулому. Виберіть інший час, будь ласка:")
        return
    await state.update_data(time=raw, appointment_time_utc=appt_utc.isoformat())
    await state.set_state(NewAppt.confirm)

    client_name = data.get("client_name")
    phone = data.get("client_phone")
    when = local_dt.strftime("%d.%m.%Y о %H:%M")
    txt = (
        "🧾 <b>Перевіримо дані</b>\n\n"
        f"👤 Клієнт: <b>{client_name}</b>\n"
        f"📞 Телефон: <b>{phone or '—'}</b>\n"
        f"🗓️ Час: <b>{when}</b>\n\n"
        "Підтверджуємо?"
    )
    await message.answer(txt, reply_markup=kb_confirm_new(), parse_mode="HTML")


@router.callback_query(NewAppt.confirm, F.data == "admin:new:edit")
async def cb_new_edit(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(NewAppt.client_name)
    await call.message.answer("✏️ Ок, введіть ім'я клієнта ще раз:", reply_markup=kb_back())


@router.callback_query(NewAppt.confirm, F.data == "admin:new:confirm")
async def cb_new_confirm(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    try:
        user = call.from_user
        if not user:
            await call.message.answer("😅 Не бачу ваш профіль. Спробуйте /start.")
            return

        data = await state.get_data()
        client_name = data.get("client_name")
        client_phone = data.get("client_phone")
        appt_utc_iso = data.get("appointment_time_utc")
        if not client_name or not appt_utc_iso:
            await call.message.answer("😕 Дані загубились. Спробуйте /start і створіть запис знову.")
            await state.clear()
            return

        appt_time = datetime.fromisoformat(appt_utc_iso)
        db = await connect(config.db_path)
        try:
            master = await get_master_by_chat(db, user.id)
            if not master:
                await call.message.answer("😕 Не знайшов ваш профіль. Введіть /start ще раз.")
                return

            # Ліміт FREE
            local = _local_now()
            print(f"DBG: count_month_appointments master_id={master.id} y={local.year} m={local.month}", flush=True)
            count = await count_month_appointments(db, master.id, local.year, local.month)
            print(f"DBG: month_count={count}", flush=True)
            if master.plan != "pro" and count >= config.free_appointments_per_month:
                await call.message.answer(
                    "🚫 Ліміт FREE плану вичерпано.\n\n"
                    "У Free — до 30 записів на місяць. У Pro — без обмежень.",
                    reply_markup=kb_pro_menu(),
                )
                return

            try:
                # існуючий код збереження
                appt = await create_appointment(
                    db,
                    master_id=master.id,
                    client_name=client_name,
                    client_phone=client_phone,
                    appointment_time=appt_time,
                )
            except Exception as e:
                print(f"ПОМИЛКА: {e}", flush=True)
                print(traceback.format_exc(), flush=True)
                await call.message.answer(f"Помилка: {e}")
                return
        finally:
            await db.close()
    except Exception as e:
        print(f"ПОМИЛКА (cb_new_confirm): {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        await call.message.answer(f"Помилка: {e}")
        return

    # Посилання і текст для шерингу
    deep_link = f"https://t.me/{config.bot_username}?start={appt.token}"
    when = _fmt_local(appt_time)
    share_text = (
        f"Привіт! Це підтвердження запису ✅\n\n"
        f"🗓️ {when}\n"
        f"Щоб підтвердити або скасувати, відкрийте: {deep_link}"
    )
    share_url = "tg://msg?text=" + __import__("urllib.parse").parse.quote(share_text)

    await state.clear()
    await call.message.answer(
        "✅ Запис створено!\n\n"
        f"🔗 Посилання для клієнта:\n{deep_link}\n\n"
        "Натисніть «Поділитись» — і відправте клієнту в Telegram.",
        reply_markup=kb_share_and_go(share_url, appt.id),
    )


@router.callback_query(F.data == "admin:today")
async def cb_today(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        await call.message.answer("😅 Спробуйте /start.")
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            await call.message.answer("😅 Спробуйте /start.")
            return
        start, end = _day_bounds_local(_local_now())
        appts = await list_appointments_for_day(db, master.id, start, end)
    finally:
        await db.close()

    if not appts:
        await call.message.answer("📋 На сьогодні записів немає 🙂", reply_markup=kb_main_menu())
        return

    lines = ["📋 <b>Записи на сьогодні</b>\n"]
    for a in appts:
        when = _fmt_local(_as_dt(a.appointment_time))
        lines.append(f"• <b>{when}</b> — {a.client_name} ({_status_label(a.status)})")
    await call.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb_main_menu())

    # Окремо — кнопки по кожному запису
    for a in appts:
        when = _fmt_local(_as_dt(a.appointment_time))
        await call.message.answer(
            f"🧾 <b>{when}</b>\n👤 {a.client_name}\nСтатус: {_status_label(a.status)}",
            parse_mode="HTML",
            reply_markup=kb_appt_actions(a.id),
        )


@router.callback_query(F.data.startswith("admin:appt:"))
async def cb_appt_details(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        return
    appt_id = int((call.data or "").split(":")[-1])
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        appt = await get_appointment_by_id(db, appt_id, master.id)
    finally:
        await db.close()
    if not appt:
        await call.message.answer("😕 Не знайшов цей запис. Спробуйте оновити /start")
        return
    when = _fmt_local(_as_dt(appt.appointment_time))
    deep_link = f"https://t.me/{config.bot_username}?start={appt.token}"
    txt = (
        f"🔎 <b>Деталі запису</b>\n\n"
        f"👤 Клієнт: <b>{appt.client_name}</b>\n"
        f"📞 Телефон: <b>{appt.client_phone or '—'}</b>\n"
        f"🗓️ Час: <b>{when}</b>\n"
        f"Статус: <b>{appt.status}</b>\n"
        f"🔗 Посилання: {deep_link}"
    )
    await call.message.answer(txt, parse_mode="HTML", reply_markup=kb_appt_details(appt.id))


@router.callback_query(F.data.startswith("admin:cancel:"))
async def cb_cancel(call: CallbackQuery) -> None:
    await call.answer()
    appt_id = int((call.data or "").split(":")[-1])
    await call.message.answer("❓ Точно скасувати запис?", reply_markup=kb_cancel_confirm(appt_id, "admin:today"))


@router.callback_query(F.data.startswith("admin:cancel_yes:"))
async def cb_cancel_yes(call: CallbackQuery) -> None:
    await call.answer()
    parts = (call.data or "").split(":")
    appt_id = int(parts[2])
    back_to = parts[3] if len(parts) > 3 else "admin:today"
    user = call.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        await set_appointment_status(db, appt_id, master.id, "cancelled")
    finally:
        await db.close()
    await call.message.answer("❌ Запис скасовано.", reply_markup=kb_main_menu())


@router.callback_query(F.data.startswith("admin:all:"))
async def cb_all(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        return
    parts = (call.data or "").split(":")  # admin all mode page
    mode = parts[2] if len(parts) > 2 else "future"
    page = int(parts[3]) if len(parts) > 3 else 0
    per_page = 5
    offset = page * per_page

    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        now = utcnow()
        total = await count_appointments(db, master.id, mode=mode, now=now)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        appts = await list_appointments(db, master.id, mode=mode, now=now, limit=per_page, offset=page * per_page)
    finally:
        await db.close()

    title = {"future": "📅 Майбутні записи", "past": "🕘 Минулі записи", "all": "📚 Всі записи"}.get(mode, "📅 Записи")
    if not appts:
        await call.message.answer(f"{title}\n\nПоки що порожньо 🙂", reply_markup=kb_filter_pager(mode, page, 1))
        return
    lines = [f"<b>{title}</b>\n"]
    for a in appts:
        when = _fmt_local(_as_dt(a.appointment_time))
        lines.append(f"• <b>{when}</b> — {a.client_name} ({_status_label(a.status)})")
    await call.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb_filter_pager(mode, page, total_pages))


@router.callback_query(F.data == "admin:clients")
async def cb_clients(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    user = call.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        clients = await list_clients(db, master.id, q=None, limit=30)
    finally:
        await db.close()
    if not clients:
        await call.message.answer("👥 База клієнтів поки порожня 🙂", reply_markup=kb_clients_menu())
        return
    lines = ["👥 <b>Клієнти</b>\n"]
    for c in clients[:30]:
        lines.append(f"• <b>{c.name}</b> — {c.total_visits} візит(и)")
    lines.append("\nЩоб знайти — натисніть «Пошук».")
    await call.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb_clients_menu())


@router.callback_query(F.data == "admin:clients:search")
async def cb_clients_search(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Settings.clients_search)
    await call.message.answer("🔎 Введіть ім'я (або частину імені) для пошуку:", reply_markup=kb_back("admin:clients"))


@router.message(Settings.clients_search)
async def st_clients_search(message: Message, state: FSMContext) -> None:
    q = (message.text or "").strip()
    if len(q) < 2:
        await message.answer("😅 Введіть хоча б 2 символи:")
        return
    user = message.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        clients = await list_clients(db, master.id, q=q, limit=30)
    finally:
        await db.close()
    if not clients:
        await message.answer("🙈 Нічого не знайшов. Спробуйте інший запит:", reply_markup=kb_back("admin:clients"))
        return
    lines = [f"🔎 <b>Знайдено</b> за «{q}»:\n"]
    for c in clients:
        lines.append(f"• <b>{c.name}</b> — {c.total_visits} візит(и) • chat_id: <code>{c.chat_id}</code>")
    lines.append("\nПоки що це MVP: профіль клієнта відкриваємо через chat_id (скоро буде кнопка).")
    await state.clear()
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb_clients_menu())


@router.callback_query(F.data == "admin:settings")
async def cb_settings(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.answer("⚙️ <b>Налаштування</b>", parse_mode="HTML", reply_markup=kb_settings_menu())


@router.callback_query(F.data == "admin:settings:name")
async def cb_settings_name(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Settings.name)
    await call.message.answer("✏️ Введіть нове ім'я майстра:", reply_markup=kb_back("admin:settings"))


@router.message(Settings.name)
async def st_settings_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("😅 Закоротко. Спробуйте ще раз:")
        return
    user = message.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        await set_master_name(db, master.id, name)
    finally:
        await db.close()
    await state.clear()
    await message.answer(f"✅ Готово! Тепер ви: <b>{name}</b>", parse_mode="HTML", reply_markup=kb_settings_menu())


@router.callback_query(F.data == "admin:settings:reminders")
async def cb_settings_reminders(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        settings = await get_master_settings(db, master.id)
    finally:
        await db.close()
    enabled_2h = bool(int(settings.get("reminder_2h_enabled", 1)))
    enabled_30m = bool(int(settings.get("reminder_30m_enabled", 0)))
    is_pro = master.plan == "pro"
    await call.message.answer(
        "⏰ <b>Нагадування</b>\n\nУвімкніть/вимкніть потрібні варіанти:",
        parse_mode="HTML",
        reply_markup=kb_reminder_settings(is_pro, enabled_2h, enabled_30m),
    )


@router.callback_query(F.data.startswith("admin:settings:rem:"))
async def cb_settings_rem_toggle(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        return
    parts = (call.data or "").split(":")  # admin settings rem 2h 1/0
    kind = parts[3]
    value = bool(int(parts[4]))
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        if kind == "30m" and master.plan != "pro":
            await call.message.answer("🔒 Нагадування за 30 хв доступне в Pro.", reply_markup=kb_pro_menu())
            return
        kwargs = {"reminder_2h_enabled": value} if kind == "2h" else {"reminder_30m_enabled": value}
        await update_master_settings(db, master.id, **kwargs)
        settings = await get_master_settings(db, master.id)
    finally:
        await db.close()
    enabled_2h = bool(int(settings.get("reminder_2h_enabled", 1)))
    enabled_30m = bool(int(settings.get("reminder_30m_enabled", 0)))
    await call.message.answer(
        "✅ Оновив налаштування.",
        reply_markup=kb_reminder_settings(master.plan == "pro", enabled_2h, enabled_30m),
    )


@router.callback_query(F.data == "admin:settings:text")
async def cb_settings_text(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
    finally:
        await db.close()
    if not master:
        return
    if master.plan != "pro":
        await call.message.answer("🔒 Кастомний текст нагадування доступний у Pro.", reply_markup=kb_pro_menu())
        return
    await state.set_state(Settings.reminder_text)
    await call.message.answer(
        "📝 Введіть текст нагадування.\n\n"
        "Підказка: можна використати змінні:\n"
        "• {client} — ім'я клієнта\n"
        "• {when} — дата/час\n"
        "• {master} — ім'я майстра\n\n"
        "Наприклад:\n"
        "🔔 {client}, нагадую про запис {when} 🙂",
        reply_markup=kb_back("admin:settings"),
    )


@router.message(Settings.reminder_text)
async def st_settings_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer("😅 Замало тексту. Напишіть трохи детальніше:")
        return
    user = message.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master or master.plan != "pro":
            await message.answer("🔒 Це доступно в Pro.", reply_markup=kb_pro_menu())
            return
        await update_master_settings(db, master.id, reminder_text=text)
    finally:
        await db.close()
    await state.clear()
    await message.answer("✅ Зберіг текст нагадування.", reply_markup=kb_settings_menu())


@router.callback_query(F.data == "admin:pro")
async def cb_pro(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(
        "💎 <b>Pro план</b>\n\n"
        "• Необмежені записи\n"
        "• 2 нагадування (2 год + 30 хв)\n"
        "• Статистика\n"
        "• Кастомний текст нагадувань\n\n"
        "Оплата — заглушка, але кнопка вже тут 🙂",
        parse_mode="HTML",
        reply_markup=kb_pro_menu(),
    )


@router.callback_query(F.data == "admin:stats")
async def cb_stats(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if not user:
        return
    db = await connect(config.db_path)
    try:
        master = await get_master_by_chat(db, user.id)
        if not master:
            return
        if master.plan != "pro":
            await call.message.answer("🔒 Статистика доступна лише в Pro.", reply_markup=kb_pro_menu())
            return
        local = _local_now()
        stats = await get_stats_month(db, master.id, local.year, local.month)
    finally:
        await db.close()

    month = _local_now().strftime("%m.%Y")
    txt = (
        f"📈 <b>Статистика за {month}</b>\n\n"
        f"👥 Клієнтів/записів: <b>{stats['total']}</b>\n"
        f"🏁 Завершено: <b>{stats['completed']}</b>\n"
        f"❌ Скасовано: <b>{stats['cancelled']}</b>\n"
        f"🙈 No-show (приблизно): <b>{stats['no_show']}</b>\n\n"
        f"⭐️ Популярний день: <b>{stats['popular_day'] or '—'}</b>\n"
        f"⏰ Популярний час: <b>{(stats['popular_hour'] + ':00') if stats['popular_hour'] else '—'}</b>"
    )
    await call.message.answer(txt, parse_mode="HTML", reply_markup=kb_pro_menu())
