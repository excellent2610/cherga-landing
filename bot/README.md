# Черга — Telegram CRM-бот

MVP CRM-бот для малого бізнесу: майстер створює запис → надсилає deep link клієнту → клієнт підтверджує/скасовує → бот надсилає нагадування.

## Стек

- Python 3.11+
- aiogram 3.x (async)
- aiosqlite (SQLite)
- APScheduler (нагадування)

## Структура

```
bot/
  bot.py
  config.py
  database.py
  scheduler.py
  keyboards.py
  middlewares.py
  handlers/
    admin.py
    client.py
    common.py
```

## Запуск

1) Підготуйте PostgreSQL (локально або хмарно):

- Локально: встановіть PostgreSQL і створіть БД `cherga_db` командою `createdb cherga_db`
- Або безкоштовний PostgreSQL: Railway.app / Supabase / Neon.tech

2) Створіть venv і встановіть залежності:

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

3) Створіть `.env`:

- Скопіюйте `.env.example` → `.env`
- Заповніть `BOT_TOKEN` та `DATABASE_URL`

4) Запуск:

```bash
python bot.py
```

## Нотатки по MVP

- FREE: до 30 записів/місяць, 1 нагадування (за 2 години), без статистики та кастомного тексту.
- PRO: необмежено, 2 нагадування (2 год + 30 хв), статистика, кастомний текст.
- Платіжна кнопка — заглушка (`https://example.com/pay`).

## Деплой на Railway

- `DATABASE_URL` Railway підставляє автоматично, якщо підключено Railway PostgreSQL
- `BOT_TOKEN` додайте вручну в змінні середовища проєкту
