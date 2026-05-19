from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def parse_iso(dt) -> datetime:
    if isinstance(dt, datetime):
        return dt
    return datetime.fromisoformat(str(dt))


def generate_token(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass
class Master:
    id: int
    chat_id: int
    name: str
    username: Optional[str]
    phone: Optional[str]
    plan: str
    created_at: datetime


@dataclass
class Appointment:
    id: int
    master_id: int
    client_chat_id: Optional[int]
    client_name: str
    client_phone: Optional[str]
    appointment_time: datetime
    token: str
    status: str
    reminder_2h_sent: bool
    reminder_30m_sent: bool
    created_at: datetime


@dataclass
class Client:
    id: int
    chat_id: int
    name: str
    phone: Optional[str]
    master_id: int
    total_visits: int
    created_at: datetime


class DbSession:
    # Легка обгортка: дозволяє зберегти поточний стиль коду (db.close())
    def __init__(self, pool: asyncpg.Pool, conn: asyncpg.Connection) -> None:
        self._pool = pool
        self._conn = conn

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        return await self._conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Optional[asyncpg.Record]:
        return await self._conn.fetchrow(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        return await self._conn.execute(query, *args)

    async def executemany(self, query: str, args: list[tuple[Any, ...]]) -> None:
        await self._conn.executemany(query, args)

    async def close(self) -> None:
        await self._pool.release(self._conn)


_pool: asyncpg.Pool | None = None


async def init_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10, command_timeout=30)
    return _pool


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool не ініціалізовано. Викличте init_pool() при старті бота.")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def connect(database_url: str | None = None) -> DbSession:
    # Для сумісності з існуючим кодом: connect(config.db_path)
    pool = await (init_pool(database_url) if database_url is not None else get_pool())
    conn = await pool.acquire()
    return DbSession(pool, conn)


async def init_db(db: asyncpg.Pool | DbSession) -> None:
    # Підтримуємо і pool, і session
    if isinstance(db, DbSession):
        exec_fn = db.execute
    else:
        async def exec_fn(q: str) -> str:
            return await db.execute(q)

    # Основні таблиці (як у вимогах)
    await exec_fn(
        """
        CREATE TABLE IF NOT EXISTS masters (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT UNIQUE NOT NULL,
            name TEXT,
            username TEXT,
            phone TEXT,
            plan TEXT DEFAULT 'free',
            appointments_this_month INTEGER DEFAULT 0,
            role TEXT DEFAULT 'master',
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    # Для існуючих БД: додамо колонку role, якщо її не було
    await exec_fn("ALTER TABLE masters ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'master';")
    await exec_fn(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            master_id INTEGER REFERENCES masters(id) ON DELETE CASCADE,
            client_chat_id BIGINT,
            client_name TEXT NOT NULL,
            client_phone TEXT,
            appointment_time TIMESTAMP NOT NULL,
            token TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'pending',
            reminder_2h_sent BOOLEAN DEFAULT FALSE,
            reminder_30m_sent BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    await exec_fn(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT UNIQUE NOT NULL,
            name TEXT,
            phone TEXT,
            master_id INTEGER REFERENCES masters(id) ON DELETE CASCADE,
            total_visits INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

    # Додаткова таблиця під налаштування (потрібна існуючому коду)
    await exec_fn(
        """
        CREATE TABLE IF NOT EXISTS master_settings (
            master_id INTEGER PRIMARY KEY REFERENCES masters(id) ON DELETE CASCADE,
            reminder_2h_enabled BOOLEAN DEFAULT TRUE,
            reminder_30m_enabled BOOLEAN DEFAULT FALSE,
            reminder_text TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

    # Індекси
    await exec_fn("CREATE INDEX IF NOT EXISTS idx_appointments_master_time ON appointments(master_id, appointment_time);")
    await exec_fn("CREATE INDEX IF NOT EXISTS idx_appointments_status_time ON appointments(status, appointment_time);")
    await exec_fn("CREATE INDEX IF NOT EXISTS idx_clients_master ON clients(master_id);")


async def delete_master_by_chat_id(chat_id: int) -> None:
    # Видаляє майстра (та пов'язані записи через ON DELETE CASCADE)
    db = await connect(None)
    try:
        await db.execute("DELETE FROM masters WHERE chat_id = $1", int(chat_id))
    finally:
        await db.close()


def _master_from(r: asyncpg.Record) -> Master:
    return Master(
        id=r["id"],
        chat_id=r["chat_id"],
        name=r["name"] or "",
        username=r["username"],
        phone=r["phone"],
        plan=r["plan"] or "free",
        created_at=r["created_at"],
    )


def _appt_from(r: asyncpg.Record) -> Appointment:
    return Appointment(
        id=r["id"],
        master_id=r["master_id"],
        client_chat_id=r["client_chat_id"],
        client_name=r["client_name"],
        client_phone=r["client_phone"],
        appointment_time=r["appointment_time"].replace(tzinfo=timezone.utc) if r["appointment_time"].tzinfo is None else r["appointment_time"],
        token=r["token"],
        status=r["status"],
        reminder_2h_sent=bool(r["reminder_2h_sent"]),
        reminder_30m_sent=bool(r["reminder_30m_sent"]),
        created_at=r["created_at"],
    )


def _client_from(r: asyncpg.Record) -> Client:
    return Client(
        id=r["id"],
        chat_id=r["chat_id"],
        name=r["name"] or "",
        phone=r["phone"],
        master_id=r["master_id"],
        total_visits=int(r["total_visits"] or 0),
        created_at=r["created_at"],
    )


async def ensure_master(db: DbSession, *, chat_id: int, name: str, username: Optional[str]) -> Master:
    row = await db.fetchrow("SELECT * FROM masters WHERE chat_id = $1", chat_id)
    if row:
        await db.execute("UPDATE masters SET name = $1, username = $2 WHERE chat_id = $3", name, username, chat_id)
        row2 = await db.fetchrow("SELECT * FROM masters WHERE chat_id = $1", chat_id)
        master = _master_from(row2)
        await db.execute(
            """
            INSERT INTO master_settings(master_id)
            VALUES($1)
            ON CONFLICT (master_id) DO NOTHING
            """,
            master.id,
        )
        return master

    row_new = await db.fetchrow(
        "INSERT INTO masters(chat_id, name, username, role) VALUES($1,$2,$3,'master') RETURNING *",
        chat_id,
        name,
        username,
    )
    master = _master_from(row_new)
    await db.execute(
        """
        INSERT INTO master_settings(master_id)
        VALUES($1)
        ON CONFLICT (master_id) DO NOTHING
        """,
        master.id,
    )
    return master


async def create_master(db: DbSession, *, chat_id: int, name: str, username: Optional[str]) -> Master:
    # Створюємо майстра тільки коли користувач явно обрав роль "майстер"
    return await ensure_master(db, chat_id=chat_id, name=name, username=username)


async def get_master_by_chat_id(chat_id: int) -> dict[str, Any] | None:
    # Тільки SELECT (без INSERT). Потрібно для вибору ролі при /start без токена.
    db = await connect(None)
    try:
        row = await db.fetchrow("SELECT id, chat_id, name, username, phone, plan, role, created_at FROM masters WHERE chat_id = $1", int(chat_id))
        return dict(row) if row else None
    finally:
        await db.close()


async def get_master_by_chat(db: DbSession, chat_id: int) -> Optional[Master]:
    row = await db.fetchrow("SELECT * FROM masters WHERE chat_id = $1", chat_id)
    return _master_from(row) if row else None


async def get_master_by_id(db_or_master_id, master_id: int | None = None):
    """
    Сумісний хелпер:
    - get_master_by_id(db, master_id) -> Master | None (старий стиль, використовується в scheduler/handlers)
    - get_master_by_id(master_id) -> dict | None (новий стиль для швидких викликів через глобальний pool)
    """
    if isinstance(db_or_master_id, DbSession):
        db = db_or_master_id
        mid = int(master_id) if master_id is not None else None
        if mid is None:
            raise TypeError("get_master_by_id(db, master_id) потребує master_id")
        row = await db.fetchrow("SELECT * FROM masters WHERE id = $1", mid)
        return _master_from(row) if row else None

    # Виклик без явної сесії: беремо pool з глобального стану
    mid = int(db_or_master_id)
    db = await connect(None)
    try:
        row = await db.fetchrow("SELECT chat_id, name FROM masters WHERE id = $1", mid)
        if not row:
            return None
        return {"chat_id": int(row["chat_id"]), "name": row["name"] or ""}
    finally:
        await db.close()


async def get_master_settings(db: DbSession, master_id: int) -> dict[str, Any]:
    row = await db.fetchrow("SELECT * FROM master_settings WHERE master_id = $1", master_id)
    return dict(row) if row else {}


async def set_master_name(db: DbSession, master_id: int, name: str) -> None:
    await db.execute("UPDATE masters SET name = $1 WHERE id = $2", name, master_id)


async def set_master_phone(db: DbSession, master_id: int, phone: Optional[str]) -> None:
    await db.execute("UPDATE masters SET phone = $1 WHERE id = $2", phone, master_id)


async def set_master_plan(db: DbSession, master_id: int, plan: str) -> None:
    await db.execute("UPDATE masters SET plan = $1 WHERE id = $2", plan, master_id)


async def update_master_settings(
    db: DbSession,
    master_id: int,
    *,
    reminder_2h_enabled: Optional[bool] = None,
    reminder_30m_enabled: Optional[bool] = None,
    reminder_text: Optional[str] = None,
) -> None:
    current = await get_master_settings(db, master_id)
    if not current:
        await db.execute("INSERT INTO master_settings(master_id) VALUES($1) ON CONFLICT (master_id) DO NOTHING", master_id)
        current = await get_master_settings(db, master_id)

    new_2h = reminder_2h_enabled if reminder_2h_enabled is not None else bool(current.get("reminder_2h_enabled", True))
    new_30m = reminder_30m_enabled if reminder_30m_enabled is not None else bool(current.get("reminder_30m_enabled", False))
    new_text = reminder_text if reminder_text is not None else current.get("reminder_text")

    await db.execute(
        """
        UPDATE master_settings
        SET reminder_2h_enabled = $1,
            reminder_30m_enabled = $2,
            reminder_text = $3,
            updated_at = NOW()
        WHERE master_id = $4
        """,
        new_2h,
        new_30m,
        new_text,
        master_id,
    )


async def count_month_appointments(db: DbSession, master_id: int, year: int, month: int) -> int:
    # В БД використовується TIMESTAMP (без TZ), тому в asyncpg передаємо offset-naive datetime (UTC)
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    row = await db.fetchrow(
        "SELECT COUNT(*) AS c FROM appointments WHERE master_id = $1 AND created_at >= $2 AND created_at < $3",
        master_id,
        start,
        end,
    )
    return int(row["c"]) if row else 0


async def create_appointment(
    db: DbSession,
    *,
    master_id: int,
    client_name: str,
    client_phone: Optional[str],
    appointment_time: datetime,
) -> Appointment:
    print(f"Спроба створити запис: master_id={master_id}, time={appointment_time} ({type(appointment_time)})", flush=True)
    token = generate_token(8)
    if appointment_time.tzinfo is None:
        appointment_time = appointment_time.replace(tzinfo=timezone.utc)
    appointment_time = appointment_time.astimezone(timezone.utc).replace(tzinfo=None)

    row = await db.fetchrow(
        """
        INSERT INTO appointments(
            master_id, client_chat_id, client_name, client_phone,
            appointment_time, token, status, reminder_2h_sent, reminder_30m_sent
        )
        VALUES($1,NULL,$2,$3,$4,$5,'pending',FALSE,FALSE)
        RETURNING *
        """,
        master_id,
        client_name.strip(),
        client_phone.strip() if client_phone else None,
        appointment_time,
        token,
    )
    return _appt_from(row)


async def get_appointment_by_id(db: DbSession, appointment_id: int, master_id: int) -> Optional[Appointment]:
    row = await db.fetchrow("SELECT * FROM appointments WHERE id = $1 AND master_id = $2", appointment_id, master_id)
    return _appt_from(row) if row else None


async def get_appointment_by_token(db: DbSession, token: str) -> Optional[Appointment]:
    row = await db.fetchrow("SELECT * FROM appointments WHERE token = $1", token)
    return _appt_from(row) if row else None


async def list_appointments_for_day(db: DbSession, master_id: int, day_start: datetime, day_end: datetime) -> list[Appointment]:
    start = day_start.astimezone(timezone.utc).replace(tzinfo=None) if day_start.tzinfo else day_start
    end = day_end.astimezone(timezone.utc).replace(tzinfo=None) if day_end.tzinfo else day_end
    rows = await db.fetch(
        """
        SELECT * FROM appointments
        WHERE master_id = $1
          AND appointment_time >= $2
          AND appointment_time < $3
        ORDER BY appointment_time ASC
        """,
        master_id,
        start,
        end,
    )
    return [_appt_from(r) for r in rows]


async def list_appointments(
    db: DbSession,
    master_id: int,
    *,
    mode: str,
    now: datetime,
    limit: int,
    offset: int,
) -> list[Appointment]:
    now_ts = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    if mode == "future":
        rows = await db.fetch(
            """
            SELECT * FROM appointments
            WHERE master_id = $1 AND appointment_time >= $2
            ORDER BY appointment_time ASC
            LIMIT $3 OFFSET $4
            """,
            master_id,
            now_ts,
            limit,
            offset,
        )
    elif mode == "past":
        rows = await db.fetch(
            """
            SELECT * FROM appointments
            WHERE master_id = $1 AND appointment_time < $2
            ORDER BY appointment_time ASC
            LIMIT $3 OFFSET $4
            """,
            master_id,
            now_ts,
            limit,
            offset,
        )
    elif mode == "all":
        rows = await db.fetch(
            """
            SELECT * FROM appointments
            WHERE master_id = $1
            ORDER BY appointment_time ASC
            LIMIT $2 OFFSET $3
            """,
            master_id,
            limit,
            offset,
        )
    else:
        raise ValueError("mode must be future/past/all")
    return [_appt_from(r) for r in rows]


async def count_appointments(db: DbSession, master_id: int, *, mode: str, now: datetime) -> int:
    now_ts = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    if mode == "future":
        row = await db.fetchrow("SELECT COUNT(*) AS c FROM appointments WHERE master_id = $1 AND appointment_time >= $2", master_id, now_ts)
    elif mode == "past":
        row = await db.fetchrow("SELECT COUNT(*) AS c FROM appointments WHERE master_id = $1 AND appointment_time < $2", master_id, now_ts)
    elif mode == "all":
        row = await db.fetchrow("SELECT COUNT(*) AS c FROM appointments WHERE master_id = $1", master_id)
    else:
        raise ValueError("mode must be future/past/all")
    return int(row["c"]) if row else 0


async def set_appointment_status(db: DbSession, appointment_id: int, master_id: int, status: str) -> None:
    await db.execute("UPDATE appointments SET status = $1 WHERE id = $2 AND master_id = $3", status, appointment_id, master_id)


async def set_appointment_client_chat(db: DbSession, appointment_id: int, client_chat_id: int) -> None:
    await db.execute("UPDATE appointments SET client_chat_id = $1 WHERE id = $2", client_chat_id, appointment_id)


async def mark_reminder_sent(db: DbSession, appointment_id: int, *, kind: str) -> None:
    if kind == "2h":
        await db.execute("UPDATE appointments SET reminder_2h_sent = TRUE WHERE id = $1", appointment_id)
    elif kind == "30m":
        await db.execute("UPDATE appointments SET reminder_30m_sent = TRUE WHERE id = $1", appointment_id)
    else:
        raise ValueError("kind must be 2h or 30m")


async def upsert_client(db: DbSession, *, chat_id: int, master_id: int, name: str, phone: Optional[str]) -> Client:
    row = await db.fetchrow("SELECT * FROM clients WHERE chat_id = $1", chat_id)
    if row:
        row2 = await db.fetchrow(
            """
            UPDATE clients
            SET name = $1, phone = $2, master_id = $3
            WHERE chat_id = $4
            RETURNING *
            """,
            name,
            phone,
            master_id,
            chat_id,
        )
        return _client_from(row2)

    row_new = await db.fetchrow(
        "INSERT INTO clients(chat_id, name, phone, master_id, total_visits) VALUES($1,$2,$3,$4,0) RETURNING *",
        chat_id,
        name,
        phone,
        master_id,
    )
    return _client_from(row_new)


async def increment_client_visits(db: DbSession, chat_id: int) -> None:
    await db.execute("UPDATE clients SET total_visits = total_visits + 1 WHERE chat_id = $1", chat_id)


async def list_clients(db: DbSession, master_id: int, *, q: Optional[str] = None, limit: int = 30) -> list[Client]:
    if q:
        rows = await db.fetch(
            """
            SELECT * FROM clients
            WHERE master_id = $1 AND lower(name) LIKE $2
            ORDER BY total_visits DESC, created_at ASC
            LIMIT $3
            """,
            master_id,
            f"%{q.lower()}%",
            limit,
        )
    else:
        rows = await db.fetch(
            """
            SELECT * FROM clients
            WHERE master_id = $1
            ORDER BY total_visits DESC, created_at ASC
            LIMIT $2
            """,
            master_id,
            limit,
        )
    return [_client_from(r) for r in rows]


async def list_client_appointments(db: DbSession, master_id: int, client_chat_id: int, limit: int = 50) -> list[Appointment]:
    rows = await db.fetch(
        """
        SELECT * FROM appointments
        WHERE master_id = $1 AND client_chat_id = $2
        ORDER BY appointment_time DESC
        LIMIT $3
        """,
        master_id,
        client_chat_id,
        limit,
    )
    return [_appt_from(r) for r in rows]


async def get_stats_month(db: DbSession, master_id: int, year: int, month: int) -> dict[str, Any]:
    start = datetime(year, month, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)

    row = await db.fetchrow(
        """
        SELECT
          SUM(CASE WHEN status IN ('pending','confirmed') THEN 1 ELSE 0 END) AS total,
          SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
        FROM appointments
        WHERE master_id = $1 AND appointment_time >= $2 AND appointment_time < $3
        """,
        master_id,
        start,
        end,
    )
    total = int(row["total"] or 0) if row else 0
    cancelled = int(row["cancelled"] or 0) if row else 0
    completed = int(row["completed"] or 0) if row else 0

    now_ts = utcnow().replace(tzinfo=None)
    row2 = await db.fetchrow(
        """
        SELECT COUNT(*) AS c
        FROM appointments
        WHERE master_id = $1
          AND appointment_time >= $2 AND appointment_time < $3
          AND appointment_time < $4
          AND status IN ('pending','confirmed')
        """,
        master_id,
        start,
        end,
        now_ts,
    )
    no_show = int(row2["c"] or 0) if row2 else 0

    row3 = await db.fetchrow(
        """
        SELECT to_char(appointment_time, 'YYYY-MM-DD') AS day, COUNT(*) AS c
        FROM appointments
        WHERE master_id = $1 AND appointment_time >= $2 AND appointment_time < $3
        GROUP BY day
        ORDER BY c DESC
        LIMIT 1
        """,
        master_id,
        start,
        end,
    )
    popular_day = row3["day"] if row3 else None

    row4 = await db.fetchrow(
        """
        SELECT to_char(appointment_time, 'HH24') AS hour, COUNT(*) AS c
        FROM appointments
        WHERE master_id = $1 AND appointment_time >= $2 AND appointment_time < $3
        GROUP BY hour
        ORDER BY c DESC
        LIMIT 1
        """,
        master_id,
        start,
        end,
    )
    popular_hour = row4["hour"] if row4 else None

    return {
        "total": total,
        "completed": completed,
        "cancelled": cancelled,
        "no_show": no_show,
        "popular_day": popular_day,
        "popular_hour": popular_hour,
    }


async def due_reminders(db: DbSession, *, now: datetime, kind: str, window_sec: int = 65) -> list[Appointment]:
    if kind == "2h":
        flag_col = "reminder_2h_sent"
        delta = timedelta(hours=2)
    elif kind == "30m":
        flag_col = "reminder_30m_sent"
        delta = timedelta(minutes=30)
    else:
        raise ValueError("kind must be 2h or 30m")

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    target = (now + delta).astimezone(timezone.utc).replace(tzinfo=None)
    end = (now + delta + timedelta(seconds=window_sec)).astimezone(timezone.utc).replace(tzinfo=None)

    rows = await db.fetch(
        f"""
        SELECT * FROM appointments
        WHERE status IN ('pending','confirmed')
          AND client_chat_id IS NOT NULL
          AND appointment_time >= $1
          AND appointment_time < $2
          AND {flag_col} = FALSE
        ORDER BY appointment_time ASC
        """,
        target,
        end,
    )
    return [_appt_from(r) for r in rows]
