"""Cat-themed Telegram group moderation bot (Python standard library only)."""

from __future__ import annotations

import calendar
import html
import json
import logging
import os
import random
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import game
import social_game

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "cat_moderator.sqlite3")))
TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ROOT = f"https://api.telegram.org/bot{TOKEN}/"
MAX_DURATION_SECONDS = 365 * 24 * 60 * 60
MAX_WORKOFF_PROBLEMS = 50
WARN_THRESHOLD = 3
WARN_TTL_SECONDS = 7 * 24 * 60 * 60
WARN_BAN_SECONDS = 7 * 24 * 60 * 60
INACTIVE_DEFAULT_DAYS = 7
INACTIVE_MAX_DAYS = 365
AUTO_DELETE_DELAY_SECONDS = 40
INACTIVE_REVIEW_TTL_SECONDS = 5 * 60
AUTO_DELETE_DB: sqlite3.Connection | None = None

MUTE_PERMISSIONS = {
    "can_send_messages": False,
    "can_send_audios": False,
    "can_send_documents": False,
    "can_send_photos": False,
    "can_send_videos": False,
    "can_send_video_notes": False,
    "can_send_voice_notes": False,
    "can_send_polls": False,
    "can_send_other_messages": False,
    "can_add_web_page_previews": False,
    "can_react_to_messages": False,
}

CAT_MUTE_LINES = [
    "🙀 Котику {name} назначена тихая охота на {duration}. Лапки отдыхают.",
    "🐾 {name} отправляется в кошачий режим тишины на {duration}.",
    "😼 Мяу-предупреждение: {name} молчит следующие {duration}.",
]
CAT_BAN_LINES = [
    "😾 Зловещий котик {name} отправлен за закрытую дверцу на {duration}.",
    "🚪 Котик {name} временно покидает чат на {duration}. Дверца откроется позже.",
    "🐈‍⬛ {name} получил временный кошачий бан на {duration}.",
]
CAT_PERMANENT_MUTE_LINES = [
    "🙀 Котику {name} включён бессрочный режим чтения: всё видит, лапками не пишет.",
    "🐱 {name} остаётся с нами, но отправляется на вечную беззвучную охоту.",
    "🤫 {name} теперь читательский котик: сообщения видны, отправка закрыта навсегда.",
]


class TelegramError(RuntimeError):
    pass


def api(method: str, payload: dict[str, Any] | None = None) -> Any:
    body = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        API_ROOT + method,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("description", "API error")
        except Exception:
            detail = "Telegram API request failed"
        raise TelegramError(f"{method}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TelegramError(f"{method}: network error ({exc})") from None
    if not result.get("ok"):
        raise TelegramError(f"{method}: {result.get('description', 'API error')}")
    return result.get("result")


def backfill_activity_from_game_stats(db: sqlite3.Connection, chat_id: int | None = None) -> None:
    query = (
        "SELECT u.chat_id,u.user_id,MAX(d.day) AS last_day "
        "FROM known_users u JOIN cats c ON c.chat_id=u.chat_id AND c.user_id=u.user_id "
        "JOIN cat_days d ON d.cat_id=c.cat_id "
        "WHERE u.last_message_at=0 AND d.message_count>0"
    )
    params: tuple[int, ...] = ()
    if chat_id is not None:
        query += " AND u.chat_id=?"
        params = (chat_id,)
    query += " GROUP BY u.chat_id,u.user_id"
    historical_activity = db.execute(query, params).fetchall()
    local_timezone = ZoneInfo("Asia/Yekaterinburg")
    for row in historical_activity:
        try:
            last_day = datetime.strptime(row["last_day"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        # Use the end of the recorded day so day-level history never marks someone inactive early.
        estimated_activity = datetime.combine(
            last_day + timedelta(days=1), datetime.min.time(), tzinfo=local_timezone
        )
        db.execute(
            "UPDATE known_users SET last_message_at=? WHERE chat_id=? AND user_id=? AND last_message_at=0",
            (int(estimated_activity.timestamp()), row["chat_id"], row["user_id"]),
        )
    db.commit()


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS known_users (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT NOT NULL,
            last_activity INTEGER NOT NULL DEFAULT 0,
            last_message_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS known_users_username
            ON known_users(chat_id, username);
        CREATE TABLE IF NOT EXISTS private_chats (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS moderators (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT NOT NULL,
            assigned_by INTEGER NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS warnings (
            warn_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            issued_by INTEGER NOT NULL,
            issued_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            revoked_by INTEGER,
            revoked_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS warnings_active_user
            ON warnings(chat_id,user_id,active,warn_id);
        CREATE TABLE IF NOT EXISTS workoff (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            total INTEGER NOT NULL,
            solved INTEGER NOT NULL DEFAULT 0,
            current_answer INTEGER,
            started INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_workoff_per_user
            ON workoff(user_id) WHERE active = 1;
        CREATE TABLE IF NOT EXISTS inactivity_reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            requested_by INTEGER NOT NULL,
            days INTEGER NOT NULL,
            candidate_user_ids TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS auto_delete_messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            delete_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id,message_id)
        );
        """
    )
    known_user_columns = {row["name"] for row in db.execute("PRAGMA table_info(known_users)").fetchall()}
    if "last_activity" not in known_user_columns:
        db.execute("ALTER TABLE known_users ADD COLUMN last_activity INTEGER NOT NULL DEFAULT 0")
    if "last_message_at" not in known_user_columns:
        db.execute("ALTER TABLE known_users ADD COLUMN last_message_at INTEGER NOT NULL DEFAULT 0")
    db.execute(
        "CREATE INDEX IF NOT EXISTS known_users_activity ON known_users(chat_id,last_activity)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS known_users_message_activity ON known_users(chat_id,last_message_at)"
    )
    warning_columns = {row["name"] for row in db.execute("PRAGMA table_info(warnings)").fetchall()}
    if "expires_at" not in warning_columns:
        db.execute("ALTER TABLE warnings ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0")
        db.execute(
            "UPDATE warnings SET expires_at=issued_at+? WHERE expires_at=0 AND issued_at>0",
            (WARN_TTL_SECONDS,),
        )
    expire_warnings(db, now=int(time.time()))
    game.setup_db(db)
    social_game.setup_db(db)
    backfill_activity_from_game_stats(db)
    db.commit()
    return db


def send_message(chat_id: int, text: str, **extra: Any) -> Any:
    auto_delete = bool(extra.pop("auto_delete", True))
    delete_after = int(extra.pop("auto_delete_after", AUTO_DELETE_DELAY_SECONDS))
    result = api("sendMessage", {"chat_id": chat_id, "text": text, **extra})
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if auto_delete and message_id and AUTO_DELETE_DB is not None:
        schedule_auto_delete(AUTO_DELETE_DB, int(chat_id), int(message_id), delete_after)
    return result


def schedule_auto_delete(
    db: sqlite3.Connection, chat_id: int, message_id: int, delete_after: int
) -> None:
    if int(chat_id) >= 0 or int(message_id) <= 0 or int(delete_after) <= 0:
        return
    db.execute(
        "INSERT OR IGNORE INTO auto_delete_messages(chat_id,message_id,delete_at) VALUES(?,?,?)",
        (int(chat_id), int(message_id), int(time.time()) + int(delete_after)),
    )
    db.commit()


def process_due_auto_deletes(db: sqlite3.Connection, now: int | None = None) -> None:
    now = int(time.time()) if now is None else int(now)
    rows = db.execute(
        "SELECT chat_id,message_id,attempts FROM auto_delete_messages WHERE delete_at<=? "
        "ORDER BY delete_at LIMIT 100",
        (now,),
    ).fetchall()
    for row in rows:
        try:
            api("deleteMessage", {"chat_id": row["chat_id"], "message_id": row["message_id"]})
        except TelegramError as exc:
            description = str(exc).lower()
            retryable = "network error" in description or "too many requests" in description or "429" in description
            attempts = int(row["attempts"])
            if retryable and attempts < 4:
                db.execute(
                    "UPDATE auto_delete_messages SET attempts=attempts+1,delete_at=? "
                    "WHERE chat_id=? AND message_id=?",
                    (now + 60, row["chat_id"], row["message_id"]),
                )
            else:
                logging.warning(
                    "Could not auto-delete message %s in chat %s: %s",
                    row["message_id"], row["chat_id"], exc,
                )
                db.execute(
                    "DELETE FROM auto_delete_messages WHERE chat_id=? AND message_id=?",
                    (row["chat_id"], row["message_id"]),
                )
        else:
            db.execute(
                "DELETE FROM auto_delete_messages WHERE chat_id=? AND message_id=?",
                (row["chat_id"], row["message_id"]),
            )
    db.commit()


def remember_user(
    db: sqlite3.Connection,
    chat_id: int,
    user: dict[str, Any],
    activity_at: int | None = None,
) -> None:
    user_id = user.get("id")
    if not user_id:
        return
    username = user.get("username", "").lower() or None
    message_at = 0 if activity_at is None else max(0, int(activity_at))
    db.execute(
        "INSERT INTO known_users(chat_id,user_id,username,first_name,last_activity,last_message_at) "
        "VALUES(?,?,?,?,0,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name,last_message_at=CASE WHEN excluded.last_message_at>0 "
        "THEN MAX(known_users.last_message_at,excluded.last_message_at) ELSE known_users.last_message_at END",
        (chat_id, user_id, username, user.get("first_name", "котик"), message_at),
    )
    db.commit()


def remember_private_chat(db: sqlite3.Connection, user_id: int, chat_id: int) -> None:
    db.execute(
        "INSERT INTO private_chats(user_id,chat_id) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id",
        (user_id, chat_id),
    )
    db.commit()


def mention(user: dict[str, Any]) -> str:
    name = html.escape(user.get("first_name", "котик"))
    return f'<a href="tg://user?id={int(user["id"])}">{name}</a>'


def parse_command(text: str) -> tuple[str, list[str]] | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    head = parts[0][1:].split("@", 1)[0].lower()
    return head, parts[1:]


def parse_duration(value: str) -> tuple[int, str] | None:
    match = re.fullmatch(r"(\d+)([hdm]?)", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2) or "h"  # A bare number means hours.
    if unit == "m":
        now = datetime.now(timezone.utc)
        month_index = now.year * 12 + now.month - 1 + amount
        year, month_index = divmod(month_index, 12)
        month = month_index + 1
        day = min(now.day, calendar.monthrange(year, month)[1])
        expiry = now.replace(year=year, month=month, day=day)
        seconds = int(expiry.timestamp()) - int(now.timestamp())
    else:
        seconds = amount * {"h": 3600, "d": 86400}[unit]
    if seconds < 30 or seconds > MAX_DURATION_SECONDS:
        return None
    if unit == "m":
        last_two = amount % 100
        last_one = amount % 10
        month_word = "месяц" if last_one == 1 and last_two != 11 else (
            "месяца" if 2 <= last_one <= 4 and not 12 <= last_two <= 14 else "месяцев"
        )
        label = f"{amount} {month_word}"
    elif unit == "d":
        label = f"{amount} дн." if amount != 1 else "1 день"
    else:
        label = f"{amount} ч." if amount != 1 else "1 час"
    return seconds, label


def resolve_target(
    db: sqlite3.Connection, chat_id: int, message: dict[str, Any], args: list[str]
) -> dict[str, Any] | None:
    reply = message.get("reply_to_message", {}).get("from")
    if reply and not reply.get("is_bot"):
        return reply

    for entity in message.get("entities", []):
        if entity.get("type") == "text_mention" and entity.get("user"):
            user = entity["user"]
            if not user.get("is_bot"):
                return user

    for arg in args:
        if not arg.startswith("@"):
            continue
        row = db.execute(
            "SELECT user_id, username, first_name FROM known_users "
            "WHERE chat_id=? AND username=?",
            (chat_id, arg[1:].lower()),
        ).fetchone()
        if row:
            return {
                "id": row["user_id"],
                "username": row["username"],
                "first_name": row["first_name"],
            }
    return None


def get_duration_argument(args: list[str]) -> tuple[int, str] | None:
    # Accept both compact forms (2H, 3D, 1M) and spaced forms (2 H, 3 D, 1 M).
    for index in range(len(args) - 1, 0, -1):
        if args[index].lower() in {"h", "d", "m"} and args[index - 1].isdigit():
            parsed = parse_duration(args[index - 1] + args[index])
            if parsed:
                return parsed
    for arg in reversed(args):
        parsed = parse_duration(arg)
        if parsed:
            return parsed
    return None


def is_admin(chat_id: int, user_id: int) -> bool:
    member = api("getChatMember", {"chat_id": chat_id, "user_id": user_id})
    return member.get("status") in ("creator", "administrator")


def is_moderator(db: sqlite3.Connection, chat_id: int, user_id: int) -> bool:
    return db.execute(
        "SELECT 1 FROM moderators WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone() is not None


def check_target_member(chat_id: int, user: dict[str, Any]) -> bool:
    if user.get("is_bot"):
        return False
    member = api("getChatMember", {"chat_id": chat_id, "user_id": user["id"]})
    return member.get("status") in ("member", "restricted")


def prepare_inactive_review(
    db: sqlite3.Connection,
    chat_id: int,
    requester_id: int,
    args: list[str],
) -> None:
    if len(args) > 1:
        send_message(chat_id, "Формат: <code>/inactive</code> или <code>/inactive 7</code>.", parse_mode="HTML")
        return
    try:
        days = int(args[0]) if args else INACTIVE_DEFAULT_DAYS
    except ValueError:
        days = 0
    if days < 1 or days > INACTIVE_MAX_DAYS:
        send_message(
            chat_id,
            f"Укажи срок от 1 до {INACTIVE_MAX_DAYS} дней, например <code>/inactive 7</code>.",
            parse_mode="HTML",
        )
        return

    now = int(time.time())
    backfill_activity_from_game_stats(db, chat_id)
    cutoff = now - days * 24 * 60 * 60
    recent_count = int(
        db.execute(
            "SELECT COUNT(*) FROM known_users WHERE chat_id=? AND last_message_at>?",
            (chat_id, cutoff),
        ).fetchone()[0]
    )
    rows = db.execute(
        "SELECT u.user_id,u.username,u.first_name,u.last_message_at "
        "FROM known_users u LEFT JOIN moderators m ON m.chat_id=u.chat_id AND m.user_id=u.user_id "
        "WHERE u.chat_id=? AND u.last_message_at>0 AND u.last_message_at<=? AND m.user_id IS NULL "
        "ORDER BY u.last_message_at ASC",
        (chat_id, cutoff),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    check_errors = 0
    for row in rows:
        try:
            member = api("getChatMember", {"chat_id": chat_id, "user_id": row["user_id"]})
        except TelegramError as exc:
            check_errors += 1
            logging.warning("Could not check inactive user %s in chat %s: %s", row["user_id"], chat_id, exc)
            continue
        status = member.get("status")
        member_info = member.get("user", {})
        if member_info.get("is_bot") or status not in ("member", "restricted"):
            continue
        if status == "restricted" and not member.get("is_member", True):
            continue
        candidates.append(
            {
                "user_id": int(row["user_id"]),
                "username": row["username"],
                "first_name": row["first_name"],
                "last_message_at": int(row["last_message_at"]),
            }
        )

    if not candidates:
        if check_errors:
            send_message(
                chat_id,
                "Не удалось надёжно проверить участников. Убедись, что бот — администратор беседы "
                "с правом ограничивать участников, и повтори команду.",
            )
        else:
            tracked_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM known_users WHERE chat_id=? AND last_message_at>0",
                    (chat_id,),
                ).fetchone()[0]
            )
            send_message(
                chat_id,
                f"🐾 За последние {days} дней написали хотя бы одно сообщение {recent_count} пользователей. "
                f"Неактивных участников для удаления не нашёл. Учтена история {tracked_count} пользователей; "
                "/stats показывает накопленное число сообщений за всё время.",
            )
        return

    cursor = db.execute(
        "INSERT INTO inactivity_reviews(chat_id,requested_by,days,candidate_user_ids,created_at,expires_at,status) "
        "VALUES(?,?,?,?,?,?, 'pending')",
        (
            chat_id,
            requester_id,
            days,
            json.dumps([candidate["user_id"] for candidate in candidates]),
            now,
            now + INACTIVE_REVIEW_TTL_SECONDS,
        ),
    )
    review_id = int(cursor.lastrowid)
    db.commit()

    display_limit = 20
    lines = []
    for candidate in candidates[:display_limit]:
        user_id = candidate["user_id"]
        first_name = html.escape(candidate["first_name"] or "участник")
        username = f" (@{html.escape(candidate['username'])})" if candidate["username"] else ""
        inactive_days = max(0, (now - candidate["last_message_at"]) // (24 * 60 * 60))
        lines.append(
            f"• <a href=\"tg://user?id={user_id}\">{first_name}</a>{username} — {inactive_days} дн."
        )
    if len(candidates) > display_limit:
        lines.append(f"…и ещё {len(candidates) - display_limit} участника.")
    if check_errors:
        lines.append(f"⚠️ Не удалось проверить ещё {check_errors} записей; они не включены в список.")

    keyboard = {
        "inline_keyboard": [[
            {"text": f"Удалить и разблокировать ({len(candidates)})", "callback_data": f"inactive:confirm:{review_id}"},
            {"text": "Отмена", "callback_data": f"inactive:cancel:{review_id}"},
        ]]
    }
    send_message(
        chat_id,
        f"😴 <b>Проверка неактивности: {days} дн.</b>\n"
        f"За последние {days} дней написали хотя бы одно сообщение {recent_count} пользователей.\n"
        f"Неактивные кандидаты на удаление ({len(candidates)}): не писали {days} дней или дольше.\n"
        + "\n".join(lines)
        + "\n\nПосле подтверждения бот удалит их из беседы и снимет блокировку. "
        "Они смогут вернуться по ссылке-приглашению. Подтверждение действует 5 минут. "
        "Сохранённая игровая статистика учтена; для остальных отсчёт начинается после установки этой версии.",
        parse_mode="HTML",
        reply_markup=keyboard,
        auto_delete_after=INACTIVE_REVIEW_TTL_SECONDS,
    )


def handle_inactive_callback(
    db: sqlite3.Connection,
    callback: dict[str, Any],
) -> bool:
    data = str(callback.get("data", ""))
    if not data.startswith("inactive:"):
        return False
    query_id = str(callback.get("id", ""))
    actor_id = int(callback.get("from", {}).get("id", 0))
    message = callback.get("message", {})
    chat = message.get("chat", {})
    chat_id = int(chat.get("id", 0))

    def answer(text: str, alert: bool = False) -> None:
        api(
            "answerCallbackQuery",
            {"callback_query_id": query_id, "text": text[:190], "show_alert": alert},
        )

    try:
        _, action, review_text = data.split(":", 2)
        review_id = int(review_text)
    except (ValueError, TypeError):
        answer("Не удалось прочитать кнопку.", True)
        return True

    review = db.execute(
        "SELECT * FROM inactivity_reviews WHERE review_id=? AND chat_id=?",
        (review_id, chat_id),
    ).fetchone()
    if not review or review["status"] != "pending":
        answer("Эта проверка уже закрыта.")
        return True
    if int(review["requested_by"]) != actor_id:
        answer("Подтвердить или отменить проверку может только запустивший её администратор.", True)
        return True
    try:
        if not is_admin(chat_id, actor_id):
            answer("Подтвердить удаление может только администратор беседы.", True)
            return True
    except TelegramError:
        answer("Не удалось проверить права администратора.", True)
        return True

    now = int(time.time())
    if int(review["expires_at"]) < now:
        db.execute("UPDATE inactivity_reviews SET status='expired' WHERE review_id=?", (review_id,))
        db.commit()
        answer("Время подтверждения истекло. Запусти /inactive ещё раз.", True)
        return True
    if action == "cancel":
        db.execute("UPDATE inactivity_reviews SET status='cancelled' WHERE review_id=?", (review_id,))
        db.commit()
        answer("Проверка отменена.")
        return True
    if action != "confirm":
        answer("Неизвестное действие.", True)
        return True

    db.execute("UPDATE inactivity_reviews SET status='processing' WHERE review_id=?", (review_id,))
    db.commit()
    answer("Проверяю список и удаляю неактивных участников…")

    days = int(review["days"])
    cutoff = now - days * 24 * 60 * 60
    try:
        user_ids = [int(user_id) for user_id in json.loads(review["candidate_user_ids"])]
    except (ValueError, TypeError, json.JSONDecodeError):
        user_ids = []
    removed = 0
    skipped = 0
    ban_errors = 0
    unban_errors = 0
    for user_id in user_ids:
        last = db.execute(
            "SELECT last_message_at FROM known_users WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if not last or int(last["last_message_at"]) <= 0 or int(last["last_message_at"]) > cutoff:
            skipped += 1
            continue
        if db.execute(
            "SELECT 1 FROM moderators WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone():
            skipped += 1
            continue
        try:
            member = api("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        except TelegramError as exc:
            logging.warning("Could not recheck inactive user %s in chat %s: %s", user_id, chat_id, exc)
            skipped += 1
            continue
        status = member.get("status")
        if (
            member.get("user", {}).get("is_bot")
            or status not in ("member", "restricted")
            or (status == "restricted" and not member.get("is_member", True))
        ):
            skipped += 1
            continue
        try:
            api("banChatMember", {"chat_id": chat_id, "user_id": user_id})
        except TelegramError as exc:
            logging.warning("Could not remove inactive user %s from chat %s: %s", user_id, chat_id, exc)
            ban_errors += 1
            continue
        try:
            api("unbanChatMember", {"chat_id": chat_id, "user_id": user_id})
            removed += 1
        except TelegramError as exc:
            logging.error("Inactive user %s remains banned in chat %s: %s", user_id, chat_id, exc)
            unban_errors += 1

    db.execute("UPDATE inactivity_reviews SET status='done' WHERE review_id=?", (review_id,))
    db.commit()
    summary = f"✅ Удалено из беседы и разблокировано: {removed}. Пропущено: {skipped}."
    if ban_errors:
        summary += f" Не удалось удалить: {ban_errors}."
    if unban_errors:
        summary += f" ⚠️ Не удалось снять блокировку у {unban_errors} участника(ов); проверь права бота."
    send_message(chat_id, summary)
    return True


def restore_permissions(chat_id: int, user_id: int) -> None:
    chat = api("getChat", {"chat_id": chat_id})
    permissions = chat.get("permissions")
    if not isinstance(permissions, dict):
        raise TelegramError("getChat: default group permissions are unavailable")
    permissions = {key: value for key, value in permissions.items() if isinstance(value, bool)}
    api(
        "restrictChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "permissions": permissions,
            "use_independent_chat_permissions": True,
        },
    )


def create_exercise() -> tuple[str, int]:
    operator = random.choice(("+", "−", "×"))
    if operator == "+":
        left, right = random.randint(1, 20), random.randint(1, 20)
        answer = left + right
    elif operator == "−":
        left, right = sorted((random.randint(1, 20), random.randint(1, 20)), reverse=True)
        answer = left - right
    else:
        left, right = random.randint(2, 10), random.randint(2, 10)
        answer = left * right
    return f"{left} {operator} {right} = ?", answer


def send_next_exercise(db: sqlite3.Connection, session: sqlite3.Row, private_chat_id: int) -> None:
    question, answer = create_exercise()
    db.execute(
        "UPDATE workoff SET current_answer=?, started=1 WHERE chat_id=? AND user_id=?",
        (answer, session["chat_id"], session["user_id"]),
    )
    db.commit()
    index = int(session["solved"]) + 1
    send_message(
        private_chat_id,
        f"🐾 Отработка: пример {index} из {session['total']}\n\n{question}\n\n"
        "Пришли ответ числом. Ошибочный ответ не засчитывается — попробуешь ещё раз.",
    )


def start_workoff(db: sqlite3.Connection, session: sqlite3.Row, private_chat_id: int) -> None:
    db.execute(
        "UPDATE workoff SET started=1 WHERE chat_id=? AND user_id=?",
        (session["chat_id"], session["user_id"]),
    )
    db.commit()
    updated = db.execute(
        "SELECT * FROM workoff WHERE chat_id=? AND user_id=?",
        (session["chat_id"], session["user_id"]),
    ).fetchone()
    send_next_exercise(db, updated, private_chat_id)


def start_private_chat(db: sqlite3.Connection, message: dict[str, Any], bot_username: str) -> None:
    user = message.get("from", {})
    user_id = user.get("id")
    private_chat_id = message["chat"]["id"]
    remember_private_chat(db, user_id, private_chat_id)
    text = message.get("text", "")
    parts = text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if payload.startswith("work_"):
        try:
            group_id = int(payload[5:])
        except ValueError:
            group_id = 0
        session = db.execute(
            "SELECT * FROM workoff WHERE chat_id=? AND user_id=? AND active=1",
            (group_id, user_id),
        ).fetchone()
        if session:
            start_workoff(db, session, private_chat_id)
            return

    pending = db.execute(
        "SELECT * FROM workoff WHERE user_id=? AND active=1 AND started=0",
        (user_id,),
    ).fetchone()
    if pending:
        start_workoff(db, pending, private_chat_id)
        return

    send_message(
        private_chat_id,
        f"Привет, {html.escape(user.get('first_name', 'котик'))}! 🐱\n"
        "Я помощник модераторов котиковой беседы. Если тебя отправили на Cat practice, "
        "решай примеры прямо здесь.",
    )


def handle_workoff_answer(db: sqlite3.Connection, message: dict[str, Any]) -> bool:
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "").strip()
    if not user_id or not re.fullmatch(r"[-+]?\d+", text):
        return False
    session = db.execute(
        "SELECT * FROM workoff WHERE user_id=? AND active=1 AND started=1",
        (user_id,),
    ).fetchone()
    if not session:
        return False

    if int(text) != int(session["current_answer"]):
        send_message(message["chat"]["id"], "😿 Not quite. Try again — it's the same example.")
        return True

    solved = int(session["solved"]) + 1
    if solved >= int(session["total"]):
        try:
            restore_permissions(int(session["chat_id"]), int(user_id))
        except TelegramError as exc:
            logging.warning("Could not restore member permissions: %s", exc)
            send_message(
                message["chat"]["id"],
                "Correct! The examples are done, but I couldn't restore your chat permissions. "
                "Please contact a group admin.",
            )
            return True
        db.execute(
            "UPDATE workoff SET active=0, solved=?, current_answer=NULL "
            "WHERE chat_id=? AND user_id=?",
            (solved, session["chat_id"], user_id),
        )
        db.commit()
        send_message(
            message["chat"]["id"],
            f"🎉 Perfect! You solved all {solved} examples. Your chat permissions are back.",
        )
        send_message(
            int(session["chat_id"]),
            f"🐈 {mention(message.get('from', {}))} passed Cat practice and can write again. "
            "The math cat approves!",
            parse_mode="HTML",
        )
        return True

    db.execute(
        "UPDATE workoff SET solved=? WHERE chat_id=? AND user_id=?",
        (solved, session["chat_id"], user_id),
    )
    db.commit()
    refreshed = db.execute(
        "SELECT * FROM workoff WHERE chat_id=? AND user_id=?",
        (session["chat_id"], user_id),
    ).fetchone()
    send_next_exercise(db, refreshed, message["chat"]["id"])
    return True


def target_or_help(
    db: sqlite3.Connection, chat_id: int, message: dict[str, Any], args: list[str]
) -> dict[str, Any] | None:
    user = resolve_target(db, chat_id, message, args)
    if not user:
        send_message(
            chat_id,
            "Укажи котика через @username или ответь на его сообщение. "
            "Для @username бот должен был уже видеть его в беседе.",
        )
    return user


def warning_count(db: sqlite3.Connection, chat_id: int, user_id: int) -> int:
    expire_warnings(db, chat_id, user_id)
    row = db.execute(
        "SELECT COUNT(*) AS count FROM warnings WHERE chat_id=? AND user_id=? AND active=1",
        (chat_id, user_id),
    ).fetchone()
    return int(row["count"])


def expire_warnings(
    db: sqlite3.Connection,
    chat_id: int | None = None,
    user_id: int | None = None,
    now: int | None = None,
) -> None:
    now = int(time.time()) if now is None else int(now)
    where = ["active=1", "expires_at>0", "expires_at<=?"]
    params: list[int] = [now]
    if chat_id is not None:
        where.append("chat_id=?")
        params.append(int(chat_id))
    if user_id is not None:
        where.append("user_id=?")
        params.append(int(user_id))
    db.execute(f"UPDATE warnings SET active=0 WHERE {' AND '.join(where)}", params)
    db.commit()


def handle_stats(
    db: sqlite3.Connection,
    chat_id: int,
    message: dict[str, Any],
    args: list[str],
) -> str:
    expire_warnings(db, chat_id)
    target = resolve_target(db, chat_id, message, args) if args or message.get("reply_to_message") else None
    if (args or message.get("reply_to_message")) and not target:
        return "Не нашёл участника. Ответь на его сообщение или укажи @username, которого бот уже видел."
    if target:
        user_id = int(target["id"])
        cat = game._current_cat(db, chat_id, user_id)
        if not cat:
            return f"У {html.escape(target.get('first_name', 'участника'))} пока нет котика в этой беседе."
        activity = db.execute(
            "SELECT COALESCE(SUM(message_count),0) AS messages, "
            "COALESCE(SUM(task_done),0) AS tasks FROM cat_days WHERE cat_id=?",
            (cat["cat_id"],),
        ).fetchone()
        return (
            f"📊 <b>Статистика {html.escape(str(cat['name']))}</b>\n"
            f"Уровень: {cat['level']} · XP: {cat['total_xp']} всего\n"
            f"Сообщений учтено: {activity['messages']}\n"
            f"Заданий по уходу выполнено: {activity['tasks']}\n"
            f"Активных предупреждений: {warning_count(db, chat_id, user_id)}"
        )

    current_cats = (
        "SELECT c.* FROM cats c WHERE c.chat_id=? AND c.cat_id=("
        "SELECT MAX(c2.cat_id) FROM cats c2 WHERE c2.chat_id=c.chat_id AND c2.user_id=c.user_id)"
    )
    summary = db.execute(
        f"SELECT COUNT(*) AS cats, "
        f"COALESCE(SUM((SELECT COALESCE(SUM(d.message_count),0) FROM cat_days d WHERE d.cat_id=c.cat_id)),0) AS messages, "
        f"COALESCE(SUM((SELECT COALESCE(SUM(d.task_done),0) FROM cat_days d WHERE d.cat_id=c.cat_id)),0) AS tasks "
        f"FROM ({current_cats}) c",
        (chat_id,),
    ).fetchone()
    active_warns = db.execute(
        "SELECT COUNT(*) FROM warnings WHERE chat_id=? AND active=1", (chat_id,)
    ).fetchone()[0]
    top = db.execute(
        f"SELECT c.name,c.user_id,COALESCE(SUM(d.message_count),0) AS messages "
        f"FROM ({current_cats}) c LEFT JOIN cat_days d ON d.cat_id=c.cat_id "
        f"GROUP BY c.cat_id ORDER BY messages DESC,c.name COLLATE NOCASE LIMIT 5",
        (chat_id,),
    ).fetchall()
    lines = [
        "📊 <b>Статистика беседы</b>",
        f"Котиков: {summary['cats']}",
        f"Сообщений учтено в игре: {summary['messages']}",
        f"Заданий по уходу выполнено: {summary['tasks']}",
        f"Активных WARN: {active_warns}",
    ]
    if top:
        lines.append("\n🐾 <b>Самые разговорчивые котики</b>")
        lines.extend(
            f"{index}. {html.escape(str(row['name']))} — {row['messages']} сообщений"
            for index, row in enumerate(top, 1)
        )
    lines.append("\nЛичная статистика: ответь на сообщение участника командой /stats.")
    return "\n".join(lines)


def send_warnings(db: sqlite3.Connection, chat_id: int, target: dict[str, Any]) -> str:
    user_id = int(target["id"])
    expire_warnings(db, chat_id, user_id)
    rows = db.execute(
        "SELECT warn_id,reason,issued_by,issued_at,expires_at FROM warnings "
        "WHERE chat_id=? AND user_id=? AND active=1 ORDER BY warn_id DESC LIMIT 10",
        (chat_id, user_id),
    ).fetchall()
    name = html.escape(target.get("first_name") or target.get("username") or "участника")
    if not rows:
        return f"⚠️ У {name} нет активных предупреждений."
    lines = [f"⚠️ <b>Активные предупреждения {name}: {warning_count(db, chat_id, user_id)}</b>"]
    for row in rows:
        reason = html.escape(row["reason"] or "Без указания причины")
        stamp = time.strftime("%d.%m.%Y", time.gmtime(int(row["issued_at"])))
        expires = time.strftime("%d.%m.%Y", time.gmtime(int(row["expires_at"])))
        lines.append(f"• #{row['warn_id']} · {stamp} · до {expires} · {reason}")
    return "\n".join(lines)


def handle_group_command(
    db: sqlite3.Connection,
    message: dict[str, Any],
    command: str,
    args: list[str],
    bot_username: str,
) -> None:
    chat = message["chat"]
    chat_id = int(chat["id"])
    actor = message.get("from", {})
    actor_id = actor.get("id")
    if not actor_id:
        return

    if game.handle_command(db, message, command, args, send_message):
        return
    if social_game.handle_command(db, message, command, args, send_message):
        return

    if command in ("help", "start"):
        send_message(
            chat_id,
            "🐱 <b>Кошачья модерация</b>\n"
            "Ответь командой на сообщение участника или укажи @username.\n\n"
            "<code>/mute 24H</code> — мут на 24 часа; <code>H</code> — часы, <code>D</code> — дни, <code>M</code> — месяцы.\n"
            "<code>/mute forever</code> — постоянный мут: читать можно, писать нельзя.\n"
            "<code>/mute @username 1M</code> — мут на месяц; можно также ответить командой на сообщение участника.\n"
            "<code>/ban @username 2H</code> — временный бан; срок задаётся так же через H, D или M.\n"
            "<code>/inactive</code> или <code>/inactive 7</code> — показать активных за период и кандидатов, не писавших 7 дней; удаление — после подтверждения (только админ).\n"
            "Команды и сообщения бота удаляются примерно через 40 секунд; вопросы викторины и подтверждение /inactive остаются на время действия.\n"
            "<code>/unmute</code> — снять мут с участника.\n"
            "<code>/practice 5</code> — отправить участника решать 5 лёгких примеров; "
            "при ответе на сообщение команда выбирает участника из этого сообщения.\n\n"
            "<code>/warn причина</code> — выдать WARN ответом на сообщение или через @username. WARN действует 7 дней; третий активный WARN за этот срок приводит к бану на 7 дней.\n"
            "<code>/warns</code> — посмотреть свои предупреждения; модератор или админ может указать другого участника. <code>/unwarn</code> — снять последнее, <code>/warnclear</code> — снять все (только админ).\n"
            "<code>/stats</code> — статистика беседы; ответь командой на сообщение или укажи @username для личной статистики.\n\n"
            "🐱 <code>/cat Имя</code> — завести котика с именем или посмотреть своего.\n"
            "<code>/rename Имя</code> — переименовать котика; цена — 10 камушков. <code>/task</code> — ежедневное задание.\n"
            "<code>/feed</code> — потратить рыбку и повысить настроение на 1; активный мини-котёнок средней редкости даёт ещё +1.\n"
            "<code>/fishing</code> — раз в день попробовать поймать камушек: шанс находки 70%, шанс пустой рыбалки — 30%.\n"
            "<code>/brush</code>, <code>/clean</code> — уход; <code>/train сила</code> — тренировка за камушки.\n"
            "\n"
            "<b>Отношения</b>\n"
            "<code>/kiss @username</code> — взаимная симпатия; пара создаётся после трёх взаимных дней.\n"
            "<code>/relationship</code>, <code>/jealous</code> — статус и доверие. <code>/hug @партнёр</code> — укрепить доверие.\n"
            "<code>/breakup</code> — расстаться. Поцелуй на стороне вызывает ревность.\n\n"
            "<b>Брак и мини-котята</b>\n"
            "<code>/marry @партнёр</code> — предложение после 7 дней в паре и доверия 70/100; свадьба стоит по 20 камушков каждому.\n"
            "<code>/acceptmarriage</code> или <code>/declinemarriage</code> — ответить; <code>/divorce</code> — развестись; <code>/family</code> — семья и мини-котята.\n"
            "В браке взаимные поцелуи дают раз в неделю шанс 30% на мини-котёнка. Его нужно назвать: <code>/namekitten Имя</code>, при нескольких — <code>/namekitten ID Имя</code>. Активная способность выбирается через <code>/activekitten ID</code>.\n\n"
            "<b>Предметы</b>\n"
            "<code>/shop</code>, <code>/buy предмет</code>, <code>/items</code>, <code>/use предмет</code>.\n"
            "Есть автокормушка, игрушечная мышка и палочка нежности.\n\n"
            "<b>Мини-игра</b>\n"
            "Ответь командой <code>/ttt</code> или <code>/race</code> на сообщение соперника, чтобы выбрать ставку кнопкой или отправить её числом. Можно также сразу указать <code>@username</code> и сумму.\n"
            "<code>/accept</code> или <code>/decline</code>, отменить своё приглашение — <code>/cancelgame</code>.\n"
            "Ходы — <code>/move 1</code>…<code>/move 9</code>; выйти — <code>/resign</code>.\n\n"
            "<b>Недельные задания и викторина</b>\n"
            "<code>/weekly</code> — личное задание недели; три общих вопроса викторины выходят в 10:00, 15:00 и 20:00. Отвечай кнопкой или командами <code>/answer 1</code>, <code>/answer 2</code>, <code>/answer 3</code>. <code>/quiz</code> покажет расписание и твой прогресс.\n"
            "<code>/leaderboard</code> — рейтинг котиков за неделю. Характер виден в профиле и влияет на небольшой бонус.\n\n"
            "<code>/modadd</code> и <code>/modremove</code> — выдать или снять роль модератора; "
            "модератор может мутить, снимать мут и выдавать WARN.\n"
            "Администратору доступны все команды модерации. Модераторам — mute, unmute, warn и unwarn; любой участник может посмотреть собственные WARN. "
            "Примеры приходят в личку с ботом.",
            parse_mode="HTML",
        )
        return

    if command == "stats":
        send_message(chat_id, handle_stats(db, chat_id, message, args), parse_mode="HTML")
        return

    supported = {
        "mute", "ban", "unmute", "practice", "training", "modadd", "modremove", "mods",
        "warn", "warns", "unwarn", "warnclear", "inactive",
    }
    if command not in supported:
        return
    if chat.get("type") != "supergroup":
        send_message(chat_id, "Для модерации и Cat practice нужна супергруппа Telegram.")
        return

    admin = is_admin(chat_id, actor_id)
    moderator = is_moderator(db, chat_id, actor_id)
    if command == "warns":
        target = resolve_target(db, chat_id, message, args) if args or message.get("reply_to_message") else actor
        if not target:
            send_message(chat_id, "Не нашёл участника. Ответь на его сообщение или укажи @username, которого бот уже видел.")
            return
        if int(target["id"]) != int(actor_id) and not (admin or moderator):
            send_message(chat_id, "Чужие предупреждения могут посмотреть только администратор или модератор.")
            return
        send_message(chat_id, send_warnings(db, chat_id, target), parse_mode="HTML")
        return

    if command in ("mute", "unmute", "warn", "unwarn"):
        allowed = admin or moderator
    else:
        allowed = admin
    if not allowed:
        send_message(chat_id, "😼 Для этой команды нужны права администратора или назначенная роль модератора.")
        return

    if command == "mods":
        rows = db.execute(
            "SELECT user_id,username,first_name FROM moderators WHERE chat_id=? ORDER BY first_name",
            (chat_id,),
        ).fetchall()
        if not rows:
            send_message(chat_id, "Пока ни одному котику не выдана роль модератора.")
        else:
            entries = []
            for row in rows:
                display = f"@{row['username']}" if row["username"] else row["first_name"]
                entries.append(f"• <a href=\"tg://user?id={row['user_id']}\">{html.escape(display)}</a>")
            send_message(chat_id, "😼 <b>Модераторы беседы</b>\n" + "\n".join(entries), parse_mode="HTML")
        return

    if command == "inactive":
        prepare_inactive_review(db, chat_id, int(actor_id), args)
        return

    if command in ("modadd", "modremove"):
        target = target_or_help(db, chat_id, message, args)
        if not target:
            return
        if command == "modadd":
            if not check_target_member(chat_id, target):
                send_message(chat_id, "Назначить модератором можно участника беседы, не администратора и не бота.")
                return
            db.execute(
                "INSERT INTO moderators(chat_id,user_id,username,first_name,assigned_by) VALUES(?,?,?,?,?) "
                "ON CONFLICT(chat_id,user_id) DO UPDATE SET username=excluded.username, "
                "first_name=excluded.first_name, assigned_by=excluded.assigned_by",
                (chat_id, target["id"], target.get("username"), target.get("first_name", "котик"), actor_id),
            )
            db.commit()
            send_message(chat_id, f"🎖️ {mention(target)} получил роль модератора. Ему доступны mute, unmute, warn и unwarn.", parse_mode="HTML")
        else:
            cursor = db.execute(
                "DELETE FROM moderators WHERE chat_id=? AND user_id=?",
                (chat_id, target["id"]),
            )
            db.commit()
            if cursor.rowcount:
                send_message(chat_id, f"🐾 Роль модератора у {mention(target)} снята.", parse_mode="HTML")
            else:
                send_message(chat_id, "У этого котика не было роли модератора.")
        return

    if command in {"warn", "unwarn", "warnclear"}:
        target = target_or_help(db, chat_id, message, args)
        if not target:
            return
        if not check_target_member(chat_id, target):
            send_message(chat_id, "Предупреждение можно выдать участнику беседы, не боту или администратору.")
            return
        name = mention(target)
        expire_warnings(db, chat_id, int(target["id"]))
        if command == "warn":
            reason = " ".join(arg for arg in args if not arg.startswith("@")).strip()[:300]
            now = int(time.time())
            cursor = db.execute(
                "INSERT INTO warnings(chat_id,user_id,username,first_name,reason,issued_by,issued_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    chat_id,
                    target["id"],
                    target.get("username"),
                    target.get("first_name", "котик"),
                    reason,
                    actor_id,
                    now,
                    now + WARN_TTL_SECONDS,
                ),
            )
            db.commit()
            count = warning_count(db, chat_id, int(target["id"]))
            reason_text = f" Причина: {html.escape(reason)}." if reason else " Причина не указана."
            if count >= WARN_THRESHOLD:
                until = now + WARN_BAN_SECONDS
                try:
                    api(
                        "banChatMember",
                        {"chat_id": chat_id, "user_id": target["id"], "until_date": until},
                    )
                except TelegramError:
                    logging.exception("Automatic WARN ban failed for user %s in chat %s", target["id"], chat_id)
                    send_message(
                        chat_id,
                        f"⚠️ {name} получил WARN #{cursor.lastrowid}.{reason_text} Это {count}-й активный WARN, но бот не смог выдать бан. Проверь права бота.",
                        parse_mode="HTML",
                    )
                else:
                    db.execute(
                        "UPDATE workoff SET active=0 WHERE chat_id=? AND user_id=? AND active=1",
                        (chat_id, target["id"]),
                    )
                    db.commit()
                    duration = "7 дней"
                    ban_text = random.choice(CAT_BAN_LINES).format(name=name, duration=duration)
                    send_message(
                        chat_id,
                        f"⚠️ WARN #{cursor.lastrowid}.{reason_text} 🚨 Порог в три активных WARN за 7 дней достигнут. {ban_text}",
                        parse_mode="HTML",
                    )
            else:
                send_message(
                    chat_id,
                    f"⚠️ {name} получил WARN #{cursor.lastrowid}.{reason_text} Активных предупреждений: {count}/{WARN_THRESHOLD}. Каждый WARN действует 7 дней.",
                    parse_mode="HTML",
                )
            return

        if command == "warnclear":
            cursor = db.execute(
                "UPDATE warnings SET active=0,revoked_by=?,revoked_at=? "
                "WHERE chat_id=? AND user_id=? AND active=1",
                (actor_id, int(time.time()), chat_id, target["id"]),
            )
            db.commit()
            send_message(chat_id, f"🧹 У {name} снято предупреждений: {cursor.rowcount}.", parse_mode="HTML")
            return

        warn_id = next((int(arg) for arg in args if arg.isdigit()), None)
        if warn_id is None:
            row = db.execute(
                "SELECT warn_id FROM warnings WHERE chat_id=? AND user_id=? AND active=1 ORDER BY warn_id DESC LIMIT 1",
                (chat_id, target["id"]),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT warn_id FROM warnings WHERE chat_id=? AND user_id=? AND warn_id=? AND active=1",
                (chat_id, target["id"], warn_id),
            ).fetchone()
        if not row:
            send_message(chat_id, f"У {name} нет такого активного предупреждения.", parse_mode="HTML")
            return
        db.execute(
            "UPDATE warnings SET active=0,revoked_by=?,revoked_at=? WHERE chat_id=? AND user_id=? AND warn_id=?",
            (actor_id, int(time.time()), chat_id, target["id"], row["warn_id"]),
        )
        db.commit()
        send_message(chat_id, f"✅ WARN #{row['warn_id']} для {name} снят.", parse_mode="HTML")
        return

    target = target_or_help(db, chat_id, message, args)
    if not target:
        return
    if int(target["id"]) == int(actor_id) and command not in ("practice", "training"):
        send_message(chat_id, "😺 Себя в кошачий карцер отправить нельзя.")
        return
    if not check_target_member(chat_id, target):
        send_message(chat_id, "Этот котик не состоит в беседе или не может быть ограничен.")
        return

    name = mention(target)
    if command == "mute":
        permanent = any(arg.lower() in {"forever", "permanent", "perm", "навсегда", "бессрочно"} for arg in args)
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "user_id": target["id"],
            "permissions": MUTE_PERMISSIONS,
            "use_independent_chat_permissions": True,
        }
        if permanent:
            payload["until_date"] = 0
            api("restrictChatMember", payload)
            db.execute(
                "UPDATE workoff SET active=0 WHERE chat_id=? AND user_id=? AND active=1",
                (chat_id, target["id"]),
            )
            db.commit()
            send_message(chat_id, random.choice(CAT_PERMANENT_MUTE_LINES).format(name=name), parse_mode="HTML")
        else:
            duration = get_duration_argument(args)
            if not duration:
                send_message(chat_id, "Укажи срок: <code>2H</code> (часы), <code>3D</code> (дни), <code>1M</code> (месяц) или <code>forever</code>.", parse_mode="HTML")
                return
            seconds, label = duration
            payload["until_date"] = int(time.time()) + seconds
            api("restrictChatMember", payload)
            db.execute(
                "UPDATE workoff SET active=0 WHERE chat_id=? AND user_id=? AND active=1",
                (chat_id, target["id"]),
            )
            db.commit()
            send_message(chat_id, random.choice(CAT_MUTE_LINES).format(name=name, duration=label), parse_mode="HTML")
        return

    if command == "ban":
        duration = get_duration_argument(args)
        if not duration:
            send_message(chat_id, "Укажи срок бана: <code>2H</code> (часы), <code>3D</code> (дни) или <code>1M</code> (месяц).", parse_mode="HTML")
            return
        seconds, label = duration
        api(
            "banChatMember",
            {
                "chat_id": chat_id,
                "user_id": target["id"],
                "until_date": int(time.time()) + seconds,
            },
        )
        send_message(chat_id, random.choice(CAT_BAN_LINES).format(name=name, duration=label), parse_mode="HTML")
        return

    if command == "unmute":
        restore_permissions(chat_id, int(target["id"]))
        db.execute(
            "UPDATE workoff SET active=0 WHERE chat_id=? AND user_id=? AND active=1",
            (chat_id, target["id"]),
        )
        db.commit()
        send_message(chat_id, f"🐾 {name} снова может мяукать в чате.", parse_mode="HTML")
        return

    count = next((int(arg) for arg in reversed(args) if arg.isdigit()), None)
    if count is None or count < 1 or count > MAX_WORKOFF_PROBLEMS:
        send_message(chat_id, f"Укажи число примеров от 1 до {MAX_WORKOFF_PROBLEMS}, например <code>/practice 5</code>.", parse_mode="HTML")
        return
    existing = db.execute(
        "SELECT chat_id FROM workoff WHERE user_id=? AND active=1",
        (target["id"],),
    ).fetchone()
    if existing:
        send_message(chat_id, "Этот котик уже проходит Cat practice.")
        return

    db.execute(
        "INSERT INTO workoff(chat_id,user_id,total,solved,started,active) VALUES(?,?,?,0,0,1) "
        "ON CONFLICT(chat_id,user_id) DO UPDATE SET total=excluded.total, solved=0, "
        "current_answer=NULL, started=0, active=1",
        (chat_id, target["id"], count),
    )
    db.commit()
    try:
        api(
            "restrictChatMember",
            {
                "chat_id": chat_id,
                "user_id": target["id"],
                "permissions": MUTE_PERMISSIONS,
                "use_independent_chat_permissions": True,
            },
        )
    except TelegramError:
        db.execute(
            "UPDATE workoff SET active=0 WHERE chat_id=? AND user_id=?",
            (chat_id, target["id"]),
        )
        db.commit()
        raise

    private = db.execute(
        "SELECT chat_id FROM private_chats WHERE user_id=?", (target["id"],)
    ).fetchone()
    if private:
        session = db.execute(
            "SELECT * FROM workoff WHERE chat_id=? AND user_id=?",
            (chat_id, target["id"]),
        ).fetchone()
        send_message(chat_id, f"📚 {name} отправлен на Cat practice: нужно решить {count} лёгких примеров.", parse_mode="HTML")
        start_workoff(db, session, int(private["chat_id"]))
    else:
        link = f"https://t.me/{bot_username}?start=work_{chat_id}"
        keyboard = {"inline_keyboard": [[{"text": "🐾 Open Cat practice", "url": link}]]}
        send_message(
            chat_id,
            f"📚 {name} отправлен на Cat practice: нужно решить {count} лёгких примеров. "
            "Нажми кнопку и запусти бота в личке.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )


def handle_update(db: sqlite3.Connection, update: dict[str, Any], bot_username: str) -> None:
    callback = update.get("callback_query")
    if callback:
        answer_callback = lambda query_id, text: api(
            "answerCallbackQuery",
            {"callback_query_id": query_id, "text": text, "show_alert": False},
        )
        handled = social_game.handle_quiz_callback(
            db,
            callback,
            send_message,
            answer_callback,
        )
        if not handled:
            handled = social_game.handle_game_stake_callback(db, callback, send_message, answer_callback)
        if not handled:
            handle_inactive_callback(db, callback)
        return
    message = update.get("message")
    if not message:
        return
    chat = message.get("chat", {})
    user = message.get("from", {})
    text = message.get("text", "")
    if chat.get("type") == "private":
        remember_private_chat(db, int(user.get("id", 0)), int(chat["id"]))
        parsed = parse_command(text)
        if parsed and parsed[0] == "start":
            start_private_chat(db, message, bot_username)
            return
        if handle_workoff_answer(db, message):
            return
        if parsed and parsed[0] == "help":
            send_message(chat["id"], "To start Cat practice, tap the button in the group and send your answers here.")
        return

    if chat.get("type") not in ("group", "supergroup"):
        return
    parsed = parse_command(text)
    if not parsed and social_game.handle_pending_game_stake(db, message, send_message):
        if user:
            remember_user(db, int(chat["id"]), user, None)
        schedule_auto_delete(
            db,
            int(chat["id"]),
            int(message.get("message_id", 0)),
            AUTO_DELETE_DELAY_SECONDS,
        )
        return
    if user:
        activity_at = None
        if game.is_activity_message(message):
            try:
                activity_at = int(message.get("date", time.time()))
            except (TypeError, ValueError):
                activity_at = int(time.time())
        remember_user(db, int(chat["id"]), user, activity_at)
    if parsed:
        command_head = (text or "").strip().split(maxsplit=1)[0][1:]
        target_bot = command_head.split("@", 1)[1].casefold() if "@" in command_head else ""
        try:
            handle_group_command(db, message, parsed[0], parsed[1], bot_username)
        finally:
            if not target_bot or target_bot == bot_username.casefold():
                schedule_auto_delete(
                    db,
                    int(chat["id"]),
                    int(message.get("message_id", 0)),
                    AUTO_DELETE_DELAY_SECONDS,
                )
    else:
        game.record_group_message(db, message, send_message)


def main() -> None:
    global AUTO_DELETE_DB
    if not TOKEN:
        raise SystemExit("Set BOT_TOKEN before starting the bot.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = connect_db()
    AUTO_DELETE_DB = db
    me = api("getMe")
    bot_username = me.get("username", "")
    if not bot_username:
        raise SystemExit("Telegram did not return a bot username.")
    api(
        "setMyCommands",
        {
            "commands": [
                {"command": "help", "description": "Справка по командам"},
                {"command": "inactive", "description": "Проверить неактивных участников (админ)"},
                {"command": "mute", "description": "Временно ограничить сообщения"},
                {"command": "ban", "description": "Временно заблокировать участника"},
                {"command": "unmute", "description": "Снять ограничение сообщений"},
                {"command": "practice", "description": "Cat practice: простые примеры"},
                {"command": "warn", "description": "Выдать участнику предупреждение"},
                {"command": "warns", "description": "Посмотреть активные предупреждения"},
                {"command": "unwarn", "description": "Снять последнее или указанное предупреждение"},
                {"command": "warnclear", "description": "Снять все предупреждения (админ)"},
                {"command": "stats", "description": "Статистика котиков и сообщений"},
                {"command": "cat", "description": "Завести котика с именем или посмотреть статус"},
                {"command": "rename", "description": "Переименовать котика (10 камушков)"},
                {"command": "kiss", "description": "Выразить симпатию взаимно"},
                {"command": "hug", "description": "Укрепить доверие в паре"},
                {"command": "relationship", "description": "Посмотреть статус отношений"},
                {"command": "jealous", "description": "Проверить ревность и доверие"},
                {"command": "breakup", "description": "Завершить отношения"},
                {"command": "family", "description": "Семья и мини-котята"},
                {"command": "marry", "description": "Сделать предложение партнёру"},
                {"command": "acceptmarriage", "description": "Принять предложение"},
                {"command": "declinemarriage", "description": "Отклонить предложение"},
                {"command": "cancelmarriage", "description": "Отменить своё предложение"},
                {"command": "divorce", "description": "Расторгнуть брак"},
                {"command": "namekitten", "description": "Назвать мини-котёнка"},
                {"command": "activekitten", "description": "Выбрать активного котёнка"},
                {"command": "shop", "description": "Магазин предметов"},
                {"command": "buy", "description": "Купить предмет за камушки"},
                {"command": "items", "description": "Посмотреть вещи котика"},
                {"command": "use", "description": "Использовать предмет"},
                {"command": "ttt", "description": "Ответом на сообщение выбрать ставку в крестиках-ноликах"},
                {"command": "race", "description": "Ответом на сообщение выбрать ставку в котогонке"},
                {"command": "accept", "description": "Принять вызов в игру"},
                {"command": "decline", "description": "Отклонить вызов в игру"},
                {"command": "cancelgame", "description": "Отменить своё приглашение"},
                {"command": "move", "description": "Сделать ход в крестики-нолики"},
                {"command": "resign", "description": "Завершить партию досрочно"},
                {"command": "weekly", "description": "Показать недельное задание котика"},
                {"command": "quiz", "description": "Расписание и прогресс общей викторины"},
                {"command": "answer", "description": "Ответить на вопрос викторины"},
                {"command": "leaderboard", "description": "Таблица лидеров за неделю"},
                {"command": "task", "description": "Показать ежедневное задание котика"},
                {"command": "feed", "description": "Покормить котика рыбкой"},
                {"command": "fishing", "description": "Попробовать поймать камушек раз в день"},
                {"command": "brush", "description": "Расчесать котика"},
                {"command": "clean", "description": "Убрать лоток или сюрприз"},
                {"command": "train", "description": "Прокачать характеристику за камушки"},
                {"command": "modadd", "description": "Назначить модератора (ответом на сообщение)"},
                {"command": "modremove", "description": "Снять роль модератора"},
                {"command": "mods", "description": "Показать список модераторов"},
            ]
        },
    )
    logging.info("Cat moderator started as @%s", bot_username)
    offset = 0
    while True:
        try:
            process_due_auto_deletes(db)
            try:
                social_game.publish_due_quiz_questions(db, send_message)
            except Exception:
                logging.exception("Could not publish scheduled quiz questions")
            updates = api(
                "getUpdates",
                {"offset": offset, "timeout": 5, "allowed_updates": ["message", "callback_query"]},
            )
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                try:
                    handle_update(db, update, bot_username)
                except TelegramError as exc:
                    logging.warning("Telegram action failed: %s", exc)
                    message = update.get("message") or update.get("callback_query", {}).get("message", {})
                    if message.get("chat", {}).get("id"):
                        try:
                            send_message(
                                int(message["chat"]["id"]),
                                "😿 Не получилось выполнить команду. Проверь права бота и параметры команды.",
                            )
                        except TelegramError:
                            pass
                except Exception:
                    logging.exception("Could not process update %s", update.get("update_id"))
        except TelegramError as exc:
            logging.warning("Polling failed: %s", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
