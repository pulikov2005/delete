"""First-pass cat-care, activity, and training game for Telegram groups."""

from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo


GameSender = Callable[..., Any]
MAX_LEVEL = 20
MAX_FISH = 12
MAX_MOOD = 10
FEED_NOTICE_DELETE_SECONDS = 10
TASK_CHANCE = 0.35
RENAME_COST = 10
PERSONALITIES = ("Любознательный", "Игривый", "Ласковый", "Шустрый", "Упорный")
PERSONALITY_HINTS = {
    "Любознательный": "получает +1 XP, если первым выигрывает все три вопроса дня",
    "Игривый": "лучше восстанавливает настроение игрушечной мышкой",
    "Ласковый": "сильнее укрепляет доверие объятиями",
    "Шустрый": "получает небольшой бонус в котогонке",
    "Упорный": "получает дополнительную награду за недельное задание",
}

COLORS = ("рыжий", "серый", "чёрный", "белый", "полосатый", "трёхцветный", "кремовый")
LEGACY_NAMES = {
    "рыжий": ("Рыжик", "Персик", "Апельсин"),
    "серый": ("Дымок", "Графит", "Облачко"),
    "чёрный": ("Уголёк", "Пиксель", "Ночь"),
    "белый": ("Снежок", "Ирис", "Зефир"),
    "полосатый": ("Тигрик", "Кекс", "Полосатик"),
    "трёхцветный": ("Клякса", "Мармелад", "Пятнышко"),
    "кремовый": ("Бисквит", "Сливка", "Вафля"),
}
TASKS = {
    "brush": "расчесать котика",
    "litter": "убрать лоток",
    "vomit": "убрать блювотину",
    "feed": "покормить котика",
}
STAT_NAMES = {
    "strength": ("сила", "💪"),
    "agility": ("ловкость", "💨"),
    "endurance": ("выносливость", "❤️"),
    "luck": ("удача", "🍀"),
}
STAT_ALIASES = {
    "strength": "strength", "сила": "strength",
    "agility": "agility", "ловкость": "agility",
    "endurance": "endurance", "выносливость": "endurance",
    "luck": "luck", "удача": "luck",
}


def setup_db(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS cats (
            cat_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            serial INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL,
            personality TEXT NOT NULL DEFAULT 'Любознательный',
            age_days INTEGER NOT NULL DEFAULT 0,
            mood INTEGER NOT NULL DEFAULT 6,
            xp INTEGER NOT NULL DEFAULT 0,
            total_xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            stones INTEGER NOT NULL DEFAULT 0,
            fish INTEGER NOT NULL DEFAULT 0,
            feeder_food INTEGER NOT NULL DEFAULT 0,
            strength INTEGER NOT NULL DEFAULT 1,
            agility INTEGER NOT NULL DEFAULT 1,
            endurance INTEGER NOT NULL DEFAULT 1,
            luck INTEGER NOT NULL DEFAULT 1,
            adopted_on TEXT NOT NULL,
            UNIQUE(chat_id, user_id, serial)
        );
        CREATE INDEX IF NOT EXISTS cats_current
            ON cats(chat_id, user_id, cat_id);
        CREATE TABLE IF NOT EXISTS cat_days (
            cat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            task TEXT,
            task_done INTEGER NOT NULL DEFAULT 0,
            fed INTEGER NOT NULL DEFAULT 0,
            message_count INTEGER NOT NULL DEFAULT 0,
            fish_earned INTEGER NOT NULL DEFAULT 0,
            finalized INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(cat_id, day)
        );
        """
    )
    columns = {row["name"] for row in db.execute("PRAGMA table_info(cats)").fetchall()}
    if "name" not in columns:
        db.execute("ALTER TABLE cats ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    personality_was_missing = "personality" not in columns
    if personality_was_missing:
        db.execute("ALTER TABLE cats ADD COLUMN personality TEXT NOT NULL DEFAULT 'Любознательный'")
    if "mood" not in columns:
        db.execute("ALTER TABLE cats ADD COLUMN mood INTEGER NOT NULL DEFAULT 6")
        if "hp" in columns:
            db.execute("UPDATE cats SET mood=MIN(10,MAX(0,hp))")
    if "feeder_food" not in columns:
        db.execute("ALTER TABLE cats ADD COLUMN feeder_food INTEGER NOT NULL DEFAULT 0")
    # Give earlier unnamed cats stable names and revive cats from the old lifecycle.
    rows = db.execute(
        "SELECT cat_id,color,serial FROM cats WHERE name IS NULL OR name=''"
    ).fetchall()
    for row in rows:
        names = LEGACY_NAMES.get(row["color"], ("Котик",))
        name = names[(int(row["serial"]) - 1) % len(names)]
        db.execute("UPDATE cats SET name=? WHERE cat_id=?", (name, row["cat_id"]))
    old_personalities = db.execute(
        "SELECT cat_id FROM cats" if personality_was_missing
        else "SELECT cat_id FROM cats WHERE personality IS NULL OR personality=''"
    ).fetchall()
    for row in old_personalities:
        db.execute(
            "UPDATE cats SET personality=? WHERE cat_id=?",
            (random.choice(PERSONALITIES), row["cat_id"]),
        )
    if "status" in columns:
        db.execute("UPDATE cats SET status='alive'")
        if "died_on" in columns:
            db.execute("UPDATE cats SET died_on=NULL")
        if "death_reason" in columns:
            db.execute("UPDATE cats SET death_reason=NULL")
    db.commit()


def _today() -> date:
    try:
        return datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def _task_for_day() -> str | None:
    if random.random() >= TASK_CHANCE:
        return None
    return random.choice(tuple(TASKS))


def _daily(db: sqlite3.Connection, cat_id: int, day: date) -> sqlite3.Row:
    day_text = day.isoformat()
    row = db.execute(
        "SELECT * FROM cat_days WHERE cat_id=? AND day=?", (cat_id, day_text)
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO cat_days(cat_id,day,task) VALUES(?,?,?)",
            (cat_id, day_text, _task_for_day()),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM cat_days WHERE cat_id=? AND day=?", (cat_id, day_text)
        ).fetchone()
    return row


def _current_cat(db: sqlite3.Connection, chat_id: int, user_id: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM cats WHERE chat_id=? AND user_id=? ORDER BY cat_id DESC LIMIT 1",
        (chat_id, user_id),
    ).fetchone()


def _new_cat(
    db: sqlite3.Connection, chat_id: int, user_id: int, adopted_on: date, name: str
) -> sqlite3.Row:
    serial = db.execute(
        "SELECT COALESCE(MAX(serial),0)+1 FROM cats WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()[0]
    color = random.choice(COLORS)
    personality = random.choice(PERSONALITIES)
    cursor = db.execute(
        "INSERT INTO cats(chat_id,user_id,serial,name,color,personality,adopted_on) VALUES(?,?,?,?,?,?,?)",
        (chat_id, user_id, serial, name, color, personality, adopted_on.isoformat()),
    )
    cat_id = int(cursor.lastrowid)
    _daily(db, cat_id, adopted_on)
    db.commit()
    return db.execute("SELECT * FROM cats WHERE cat_id=?", (cat_id,)).fetchone()


def _cat_name(args: list[str]) -> str | None:
    name = " ".join(" ".join(args).split())
    if not name or len(name) > 24 or any(ord(char) < 32 for char in name):
        return None
    return name


def _finish_day(db: sqlite3.Connection, cat: sqlite3.Row, day: date) -> int:
    daily = _daily(db, int(cat["cat_id"]), day)
    if daily["finalized"]:
        return 0
    from social_game import finalize_auto_feed

    daily = finalize_auto_feed(db, cat, daily)
    missed_feed = not bool(daily["fed"])
    missed_task = bool(daily["task"]) and not bool(daily["task_done"])
    if daily["task"] == "feed":
        missed_task = False  # One missed feeding is one missed care action.
    mood_loss = int(missed_feed) + int(missed_task)
    if mood_loss:
        current_mood = db.execute(
            "SELECT mood FROM cats WHERE cat_id=?", (cat["cat_id"],)
        ).fetchone()["mood"]
        db.execute(
            "UPDATE cats SET mood=MAX(0,mood-?) WHERE cat_id=?",
            (mood_loss, cat["cat_id"]),
        )
        mood_lost = min(mood_loss, int(current_mood))
    else:
        mood_lost = 0
    db.execute(
        "UPDATE cat_days SET finalized=1 WHERE cat_id=? AND day=?",
        (cat["cat_id"], day.isoformat()),
    )
    db.commit()
    return mood_lost


def _prepare_cat(
    db: sqlite3.Connection,
    chat_id: int,
    user_id: int,
    create: bool = False,
    new_name: str | None = None,
) -> tuple[sqlite3.Row | None, sqlite3.Row | None, list[str]]:
    today = _today()
    cat = _current_cat(db, chat_id, user_id)
    notices: list[str] = []
    if cat is None:
        if not create or not new_name:
            return None, None, notices
        cat = _new_cat(db, chat_id, user_id, today, new_name)
        return cat, _daily(db, int(cat["cat_id"]), today), notices

    last_row = db.execute(
        "SELECT day FROM cat_days WHERE cat_id=? ORDER BY day DESC LIMIT 1",
        (cat["cat_id"],),
    ).fetchone()
    if last_row is None:
        daily = _daily(db, int(cat["cat_id"]), today)
        return cat, daily, notices

    last_day = date.fromisoformat(last_row["day"])
    lost_mood_total = 0
    while last_day < today:
        lost_mood_total += _finish_day(db, cat, last_day)
        next_day = last_day + timedelta(days=1)
        cat = db.execute("SELECT * FROM cats WHERE cat_id=?", (cat["cat_id"],)).fetchone()
        age = int(cat["age_days"]) + 1
        db.execute("UPDATE cats SET age_days=? WHERE cat_id=?", (age, cat["cat_id"]))
        cat = db.execute("SELECT * FROM cats WHERE cat_id=?", (cat["cat_id"],)).fetchone()
        _daily(db, int(cat["cat_id"]), next_day)
        last_day = next_day

    if lost_mood_total:
        notices.append(f"За время отсутствия настроение котика снизилось на {lost_mood_total} пунктов.")
    daily = _daily(db, int(cat["cat_id"]), today)
    return cat, daily, notices


def _max_mood(cat: sqlite3.Row) -> int:
    return min(MAX_MOOD, 5 + int(cat["endurance"]))


def _xp_needed(level: int) -> int:
    return 10 + 2 * (level - 1)


def _add_progress(db: sqlite3.Connection, cat: sqlite3.Row, xp: int, stones: int) -> tuple[int, int]:
    level = int(cat["level"])
    current_xp = int(cat["xp"]) + xp
    levels_gained = 0
    while level < MAX_LEVEL and current_xp >= _xp_needed(level):
        current_xp -= _xp_needed(level)
        level += 1
        levels_gained += 1
    db.execute(
        "UPDATE cats SET xp=?,total_xp=total_xp+?,level=?,stones=stones+? WHERE cat_id=?",
        (current_xp, xp, level, stones, cat["cat_id"]),
    )
    db.commit()
    return level, levels_gained


def _task_text(daily: sqlite3.Row) -> str:
    if not daily["task"]:
        return "Сегодня специальное задание не выпало."
    if daily["task_done"]:
        return f"Задание на сегодня выполнено: {TASKS[daily['task']]}."
    action = {
        "brush": "/brush",
        "litter": "/clean",
        "vomit": "/clean",
        "feed": "/feed",
    }[daily["task"]]
    return f"Задание: {TASKS[daily['task']]}. Выполни командой {action}."


def _send_notices(send: GameSender, chat_id: int, notices: list[str]) -> None:
    for notice in notices:
        send(chat_id, notice)


def _status_text(cat: sqlite3.Row, daily: sqlite3.Row) -> str:
    max_mood = _max_mood(cat)
    mood_note = " 😿 Котику нужно немного заботы." if int(cat["mood"]) == 0 else ""
    next_xp = "MAX" if int(cat["level"]) >= MAX_LEVEL else str(_xp_needed(int(cat["level"])) - int(cat["xp"]))
    return (
        f"🐱 {cat['name']} — твой {cat['color']} котик №{cat['serial']}\n"
        f"Характер: {cat['personality']} — {PERSONALITY_HINTS.get(cat['personality'], 'особый нрав')}\n"
        f"Уровень: {cat['level']} | XP: {cat['xp']} (до уровня: {next_xp})\n"
        f"Вы вместе: {cat['age_days']} дней\n"
        f"Настроение: {cat['mood']}/{max_mood} | 🐟 Рыбки: {cat['fish']} | 🪨 Камушки: {cat['stones']}\n"
        f"💪 Сила {cat['strength']} · 💨 Ловкость {cat['agility']} · "
        f"❤️ Выносливость {cat['endurance']} · 🍀 Удача {cat['luck']}\n"
        f"{_task_text(daily)}{mood_note}\n\n"
        "Команды: /task, /feed, /brush, /clean, /train, /rename"
    )


def _complete_task(
    db: sqlite3.Connection,
    cat: sqlite3.Row,
    daily: sqlite3.Row,
    expected: tuple[str, ...],
) -> tuple[bool, int, int, int, bool]:
    if not daily["task"] or daily["task"] not in expected:
        return False, 0, 0, 0, False
    if daily["task_done"]:
        return False, 0, 0, 0, False
    db.execute(
        "UPDATE cat_days SET task_done=1 WHERE cat_id=? AND day=?",
        (cat["cat_id"], daily["day"]),
    )
    from social_game import task_bonus_for_cat

    extra_xp, extra_stones = task_bonus_for_cat(db, cat)
    xp_earned = 3 + extra_xp
    stones_earned = 2 + extra_stones
    _, levels_gained = _add_progress(db, cat, xp=xp_earned, stones=stones_earned)
    from social_game import record_weekly_event

    weekly_completed = record_weekly_event(db, cat, "care")
    return True, levels_gained, xp_earned, stones_earned, weekly_completed


def _handle_feed(db: sqlite3.Connection, chat_id: int, user_id: int, send: GameSender) -> None:
    cat, daily, notices = _prepare_cat(db, chat_id, user_id)
    _send_notices(send, chat_id, notices)
    if not cat:
        send(chat_id, "Сначала заведи котика командой /cat.", auto_delete_after=FEED_NOTICE_DELETE_SECONDS)
        return
    if daily["fed"]:
        send(chat_id, "🐟 Сегодня котик уже покормлен.", auto_delete_after=FEED_NOTICE_DELETE_SECONDS)
        return
    if int(cat["fish"]) < 1:
        send(
            chat_id,
            "😿 Нет рыбки. Каждые 5 сообщений дают одну; максимум — 3 за день.",
            auto_delete_after=FEED_NOTICE_DELETE_SECONDS,
        )
        return
    from social_game import mood_bonus_for_cat

    mood_bonus = mood_bonus_for_cat(db, cat)
    mood_room = max(0, _max_mood(cat) - int(cat["mood"]))
    mood_added = min(mood_room, 1 + mood_bonus)
    db.execute("UPDATE cats SET fish=fish-1,mood=mood+? WHERE cat_id=?", (mood_added, cat["cat_id"]))
    db.execute(
        "UPDATE cat_days SET fed=1,task_done=CASE WHEN task='feed' THEN 1 ELSE task_done END "
        "WHERE cat_id=? AND day=?",
        (cat["cat_id"], daily["day"]),
    )
    completed, levels_gained, xp_earned, stones_earned, weekly_completed = _complete_task(db, cat, daily, ("feed",))
    db.commit()
    text = f"😺 {cat['name']} съел рыбку."
    if mood_added:
        text += f" Настроение улучшилось на {mood_added}."
    if completed:
        text += f" Задание выполнено: +{xp_earned} XP и +{stones_earned} камушка."
    if weekly_completed:
        text += " 📅 Недельное задание выполнено! Награда добавлена."
    if levels_gained:
        text += f" 🎉 Новый уровень: {int(cat['level']) + levels_gained}!"
    send(chat_id, text, auto_delete_after=FEED_NOTICE_DELETE_SECONDS)


def _handle_care(
    db: sqlite3.Connection,
    chat_id: int,
    user_id: int,
    action: str,
    send: GameSender,
) -> None:
    cat, daily, notices = _prepare_cat(db, chat_id, user_id)
    _send_notices(send, chat_id, notices)
    if not cat:
        send(chat_id, "Сначала заведи котика командой /cat.")
        return
    expected = ("brush",) if action == "brush" else ("litter", "vomit")
    completed, levels_gained, xp_earned, stones_earned, weekly_completed = _complete_task(db, cat, daily, expected)
    db.commit()
    if not completed:
        send(chat_id, "Сегодня такого задания нет или оно уже выполнено. Посмотреть задание: /task")
        return
    message = f"🐾 Отличная забота! +{xp_earned} XP и +{stones_earned} камушка. Задание «{TASKS[daily['task']]}» выполнено."
    if levels_gained:
        message += f" 🎉 Новый уровень: {int(cat['level']) + levels_gained}!"
    if weekly_completed:
        message += " 📅 Недельное задание выполнено! Награда добавлена."
    send(chat_id, message)


def _handle_train(
    db: sqlite3.Connection, chat_id: int, user_id: int, args: list[str], send: GameSender
) -> None:
    cat, daily, notices = _prepare_cat(db, chat_id, user_id)
    _send_notices(send, chat_id, notices)
    if not cat:
        send(chat_id, "Сначала заведи котика командой /cat.")
        return
    if not args or args[0].lower() not in STAT_ALIASES:
        send(chat_id, "Укажи характеристику: /train сила, /train ловкость, /train выносливость или /train удача.")
        return
    stat = STAT_ALIASES[args[0].lower()]
    current = int(cat[stat])
    stat_cap = min(10, 1 + int(cat["level"]) // 2)
    if stat == "endurance":
        stat_cap = min(5, stat_cap)
    if current >= stat_cap:
        send(chat_id, f"Для этого уровня предел характеристики — {stat_cap}. Сначала прокачай уровень котика.")
        return
    cost = 10 + 5 * (current - 1)
    if int(cat["stones"]) < cost:
        send(chat_id, f"Для тренировки нужно {cost} камушков, у котика сейчас {cat['stones']}.")
        return
    db.execute(
        f"UPDATE cats SET {stat}={stat}+1,stones=stones-? WHERE cat_id=?",
        (cost, cat["cat_id"]),
    )
    db.commit()
    display_name, emoji = STAT_NAMES[stat]
    new_value = current + 1
    extra = " Максимальное настроение увеличилось на 1." if stat == "endurance" else ""
    send(chat_id, f"🏋️ {emoji} {display_name.capitalize()}: {current} → {new_value}. Потрачено {cost} камушков.{extra}")


def handle_command(
    db: sqlite3.Connection,
    message: dict[str, Any],
    command: str,
    args: list[str],
    send: GameSender,
) -> bool:
    if command not in {"cat", "rename", "task", "feed", "brush", "clean", "train"}:
        return False
    chat_id = int(message["chat"]["id"])
    user_id = int(message.get("from", {}).get("id", 0))
    if not user_id:
        return True

    if command == "cat":
        requested_name = _cat_name(args)
        cat, daily, notices = _prepare_cat(
            db, chat_id, user_id, create=True, new_name=requested_name
        )
        _send_notices(send, chat_id, notices)
        if not cat:
            if requested_name is None and _current_cat(db, chat_id, user_id) is None:
                send(chat_id, "Чтобы завести котика, укажи имя: /cat Снежок (до 24 символов).")
            else:
                send(chat_id, "Не получилось создать котика. Попробуй /cat Снежок.")
            return True
        send(chat_id, _status_text(cat, daily))
    elif command == "rename":
        name = _cat_name(args)
        if name is None:
            send(chat_id, "Укажи новое имя: /rename Снежок (до 24 символов).")
            return True
        cat, _, notices = _prepare_cat(db, chat_id, user_id)
        _send_notices(send, chat_id, notices)
        if not cat:
            send(chat_id, "Сначала заведи котика командой /cat Имя.")
            return True
        if name.casefold() == str(cat["name"]).casefold():
            send(chat_id, f"🐱 Котика и так зовут {cat['name']}.")
            return True
        if int(cat["stones"]) < RENAME_COST:
            send(chat_id, f"Переименование стоит {RENAME_COST} камушков. Сейчас у котика {cat['stones']}.")
            return True
        db.execute(
            "UPDATE cats SET name=?,stones=stones-? WHERE cat_id=?",
            (name, RENAME_COST, cat["cat_id"]),
        )
        db.commit()
        send(chat_id, f"✨ Теперь котика зовут {name}. Потрачено {RENAME_COST} камушков.")
    elif command == "task":
        cat, daily, notices = _prepare_cat(db, chat_id, user_id)
        _send_notices(send, chat_id, notices)
        if cat:
            send(chat_id, f"{_task_text(daily)}\nКормление на сегодня: {'выполнено' if daily['fed'] else 'нужно покормить'}.")
        else:
            send(chat_id, "Сначала заведи котика командой /cat.")
    elif command == "feed":
        _handle_feed(db, chat_id, user_id, send)
    elif command in ("brush", "clean"):
        _handle_care(db, chat_id, user_id, command, send)
    elif command == "train":
        _handle_train(db, chat_id, user_id, args, send)
    return True


def is_activity_message(message: dict[str, Any]) -> bool:
    user = message.get("from", {})
    if not user or user.get("is_bot"):
        return False
    text = message.get("text", "") or ""
    if text.lstrip().startswith("/"):
        return False
    return bool(
        text.strip()
        or (message.get("caption", "") or "").strip()
        or any(
            key in message
            for key in ("photo", "video", "audio", "voice", "document", "sticker", "animation", "video_note")
        )
    )


def record_group_message(
    db: sqlite3.Connection, message: dict[str, Any], send: GameSender
) -> None:
    if not is_activity_message(message):
        return
    user = message.get("from", {})
    chat_id = int(message["chat"]["id"])
    user_id = int(user["id"])
    cat, _, notices = _prepare_cat(db, chat_id, user_id)
    _send_notices(send, chat_id, notices)
    if not cat:
        return
    daily = _daily(db, int(cat["cat_id"]), _today())
    new_count = int(daily["message_count"]) + 1
    fish_limit = min(3, new_count // 5)
    earned = int(daily["fish_earned"])
    if fish_limit > earned:
        add_count = fish_limit - earned
        stored = min(add_count, max(0, MAX_FISH - int(cat["fish"])))
        db.execute(
            "UPDATE cats SET fish=fish+? WHERE cat_id=?",
            (stored, cat["cat_id"]),
        )
        db.execute(
            "UPDATE cat_days SET message_count=?,fish_earned=? WHERE cat_id=? AND day=?",
            (new_count, fish_limit, cat["cat_id"], daily["day"]),
        )
        db.commit()
        if stored:
            send(chat_id, f"🐟 {cat['color'].capitalize()} котик нашёл рыбку! В запасе: {int(cat['fish']) + stored}/{MAX_FISH}.")
        else:
            send(chat_id, f"🐟 Запас рыбок у {cat['name']} полон ({MAX_FISH}); новая рыбка не поместилась.")
    else:
        db.execute(
            "UPDATE cat_days SET message_count=? WHERE cat_id=? AND day=?",
            (new_count, cat["cat_id"], daily["day"]),
        )
        db.commit()
    from social_game import record_weekly_event

    if record_weekly_event(db, cat, "messages"):
        send(chat_id, f"📅 {cat['name']} выполнил недельное задание! Награда добавлена.")


def award_minigame_win(db: sqlite3.Connection, chat_id: int, user_id: int) -> bool:
    """Reward the first game win of the day; the future mini-game handler can call this."""
    cat, daily, _ = _prepare_cat(db, chat_id, user_id)
    if not cat:
        return False
    db.execute(
        "CREATE TABLE IF NOT EXISTS cat_game_rewards (cat_id INTEGER NOT NULL, day TEXT NOT NULL, PRIMARY KEY(cat_id,day))"
    )
    try:
        db.execute(
            "INSERT INTO cat_game_rewards(cat_id,day) VALUES(?,?)",
            (cat["cat_id"], daily["day"]),
        )
    except sqlite3.IntegrityError:
        return False
    _add_progress(db, cat, xp=1, stones=1)
    return True
