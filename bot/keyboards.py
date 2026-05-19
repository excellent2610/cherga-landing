from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Новий запис", callback_data="admin:new")
    kb.button(text="📋 Записи на сьогодні", callback_data="admin:today")
    kb.button(text="📅 Всі записи", callback_data="admin:all:future:0")
    kb.button(text="👥 База клієнтів", callback_data="admin:clients")
    kb.button(text="⚙️ Налаштування", callback_data="admin:settings")
    kb.button(text="💎 Pro план", callback_data="admin:pro")
    kb.adjust(1, 2, 1, 1, 1)
    return kb.as_markup()


def kb_back(to: str = "admin:menu") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data=to)
    return kb.as_markup()


def kb_date_pick() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📍 Сьогодні", callback_data="admin:new:date:today")
    kb.button(text="➡️ Завтра", callback_data="admin:new:date:tomorrow")
    kb.button(text="🗓️ Інша дата", callback_data="admin:new:date:other")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def kb_confirm_new() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Підтвердити", callback_data="admin:new:confirm")
    kb.button(text="✏️ Змінити", callback_data="admin:new:edit")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(2, 1)
    return kb.as_markup()


def kb_share_and_go(share_url: str, appt_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 Поділитись з клієнтом", url=share_url)
    kb.button(text="📋 До записів", callback_data="admin:today")
    kb.button(text="🏠 Меню", callback_data="admin:menu")
    kb.adjust(1, 2)
    return kb.as_markup()


def kb_appt_actions(appt_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎 Деталі", callback_data=f"admin:appt:{appt_id}")
    kb.button(text="❌ Скасувати", callback_data=f"admin:cancel:{appt_id}")
    kb.adjust(2)
    return kb.as_markup()


def kb_appt_details(appt_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Скасувати", callback_data=f"admin:cancel:{appt_id}")
    kb.button(text="◀️ Назад", callback_data="admin:today")
    kb.adjust(1, 1)
    return kb.as_markup()


def kb_cancel_confirm(appt_id: int, back_to: str = "admin:today") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Так, скасувати ❌", callback_data=f"admin:cancel_yes:{appt_id}:{back_to}")
    kb.button(text="Ні, залишити ✅", callback_data=back_to)
    kb.adjust(1, 1)
    return kb.as_markup()


def kb_filter_pager(mode: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Майбутні", callback_data=f"admin:all:future:0")
    kb.button(text="Минулі", callback_data=f"admin:all:past:0")
    kb.button(text="Всі", callback_data=f"admin:all:all:0")

    prev_page = max(0, page - 1)
    next_page = min(total_pages - 1, page + 1)
    kb.button(text="⬅️", callback_data=f"admin:all:{mode}:{prev_page}")
    kb.button(text=f"{page+1}/{max(1,total_pages)}", callback_data="noop")
    kb.button(text="➡️", callback_data=f"admin:all:{mode}:{next_page}")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(3, 3, 1)
    return kb.as_markup()


def kb_clients_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎 Пошук", callback_data="admin:clients:search")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(1, 1)
    return kb.as_markup()


def kb_settings_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Змінити ім'я", callback_data="admin:settings:name")
    kb.button(text="⏰ Нагадування", callback_data="admin:settings:reminders")
    kb.button(text="📝 Текст нагадування", callback_data="admin:settings:text")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(1, 1, 1, 1)
    return kb.as_markup()


def kb_reminder_settings(is_pro: bool, enabled_2h: bool, enabled_30m: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=("✅ За 2 год" if enabled_2h else "☐ За 2 год"),
        callback_data=f"admin:settings:rem:2h:{1 if not enabled_2h else 0}",
    )
    if is_pro:
        kb.button(
            text=("✅ За 30 хв" if enabled_30m else "☐ За 30 хв"),
            callback_data=f"admin:settings:rem:30m:{1 if not enabled_30m else 0}",
        )
    else:
        kb.button(text="🔒 За 30 хв (Pro)", callback_data="admin:pro")
    kb.button(text="◀️ Назад", callback_data="admin:settings")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def kb_pro_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📈 Статистика (Pro)", callback_data="admin:stats")
    kb.button(text="💎 Отримати Pro", url="https://example.com/pay")
    kb.button(text="◀️ Назад", callback_data="admin:menu")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def kb_client_confirm(token: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Підтверджую", callback_data=f"client:confirm:{token}")
    kb.button(text="❌ Скасувати запис", callback_data=f"client:cancel:{token}")
    kb.adjust(1, 1)
    return kb.as_markup()


def kb_client_cancel_confirm(token: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Так, скасувати ❌", callback_data=f"client:cancel_yes:{token}")
    kb.button(text="Ні, назад ✅", callback_data=f"client:open:{token}")
    kb.adjust(1, 1)
    return kb.as_markup()
