"""First-pass cat-care, activity, and training game for Telegram groups."""

from __future__ import annotations

import random
import sqlite3
import time
import html
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo


GameSender = Callable[..., Any]
MAX_LEVEL = 20
MAX_FISH = 12
MAX_MOOD = 10
FEED_NOTICE_DELETE_SECONDS = 40
FISH_NOTICE_DELETE_SECONDS = 40
STONE_FISHING_SUCCESS_CHANCE = 0.70
TASK_CHANCE = 0.35
RENAME_COST = 10
PERSONALITIES = ("Любознательный", "Игривый", "Ласковый", "Шустрый", "Упорный")
PERSONALITY_HINTS = {
    "Любознательный": "обожает задачки и загадки",
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
        CREATE TABLE IF NOT EXISTS stone_fishing_claims (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY(chat_id,user_id,day)
        );
        CREATE TABLE IF NOT EXISTS stone_ledger (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            cat_id INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            source TEXT NOT NULL,
            actor_id INTEGER,
            details TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS stone_ledger_cat
            ON stone_ledger(chat_id,user_id,entry_id DESC);
        CREATE TABLE IF NOT EXISTS cat_name_prompts (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            prompt_message_id INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            PRIMARY KEY(chat_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS cat_gate_notices (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            sent_at INTEGER NOT NULL,
            PRIMARY KEY(chat_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS daily_task_announcements (
            chat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            slot INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'posting',
            PRIMARY KEY(chat_id,day,slot)
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


def change_stones(
    db: sqlite3.Connection,
    cat: sqlite3.Row,
    delta: int,
    source: str,
    actor_id: int | None = None,
    details: str = "",
) -> bool:
    """Change a cat's stone balance and record why it changed."""
    delta = int(delta)
    if delta == 0:
        return True
    current = db.execute("SELECT stones FROM cats WHERE cat_id=?", (cat["cat_id"],)).fetchone()
    if not current or int(current["stones"]) + delta < 0:
        return False
    balance = int(current["stones"]) + delta
    db.execute("UPDATE cats SET stones=? WHERE cat_id=?", (balance, cat["cat_id"]))
    db.execute(
        "INSERT INTO stone_ledger(chat_id,user_id,cat_id,delta,balance_after,source,actor_id,details,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            int(cat["chat_id"]), int(cat["user_id"]), int(cat["cat_id"]), delta,
            balance, str(source)[:100], actor_id, str(details)[:300], int(time.time()),
        ),
    )
    return True


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


def _add_progress(
    db: sqlite3.Connection, cat: sqlite3.Row, xp: int, stones: int,
    stone_source: str = "игровая награда", actor_id: int | None = None,
) -> tuple[int, int]:
    level = int(cat["level"])
    current_xp = int(cat["xp"]) + xp
    levels_gained = 0
    while level < MAX_LEVEL and current_xp >= _xp_needed(level):
        current_xp -= _xp_needed(level)
        level += 1
        levels_gained += 1
    db.execute(
        "UPDATE cats SET xp=?,total_xp=total_xp+?,level=? WHERE cat_id=?",
        (current_xp, xp, level, cat["cat_id"]),
    )
    if stones:
        change_stones(db, cat, stones, stone_source, actor_id)
    db.commit()
    return level, levels_gained


def _task_text(daily: sqlite3.Row) -> str:
    if not daily["task"]:
        return "Сегодня специальное задание не выпало. /brush и /clean выполняются только когда соответствующее задание показано здесь."
    if daily["task_done"]:
        return f"Задание на сегодня выполнено: {TASKS[daily['task']]}."
    action = {
        "brush": "/brush",
        "litter": "/clean",
        "vomit": "/clean",
        "feed": "/feed",
    }[daily["task"]]
    return f"Задание: {TASKS[daily['task']]}. Выполни командой {action}."


def _task_prompt(
    db: sqlite3.Connection, chat_id: int, user_id: int
) -> tuple[str, dict[str, Any] | None, list[str]]:
    cat, daily, notices = _prepare_cat(db, chat_id, user_id)
    if not cat:
        return "Сначала заведи котика командой /cat ИМЯ.", None, notices
    text = (
        f"🐾 Ежедневный уход за {cat['name']}\n{_task_text(daily)}\n"
        f"Кормление на сегодня: {'выполнено' if daily['fed'] else 'нужно покормить'}.\n"
        "Недельный прогресс смотри командой /weekly."
    )
    buttons: list[list[dict[str, str]]] = []
    task_actions = {"brush": ("brush", "🪮 Выполнить: расчесать"),
                    "litter": ("clean", "🧹 Выполнить: убрать лоток"),
                    "vomit": ("clean", "🧹 Выполнить: убрать сюрприз"),
                    "feed": ("feed", "🐟 Выполнить: покормить")}
    if daily["task"] and not daily["task_done"]:
        action, label = task_actions[str(daily["task"])]
        buttons.append([{
            "text": label,
            "callback_data": f"care:{user_id}:{action}:{daily['day']}",
        }])
    if not daily["fed"] and int(cat["fish"]) > 0 and daily["task"] != "feed":
        buttons.append([{
            "text": "🐟 Покормить котика",
            "callback_data": f"care:{user_id}:feed:{daily['day']}",
        }])
    markup = {"inline_keyboard": buttons} if buttons else None
    return text, markup, notices


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
    _, levels_gained = _add_progress(
        db, cat, xp=xp_earned, stones=stones_earned, stone_source="ежедневное задание", actor_id=int(cat["user_id"])
    )
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
        if not daily["task"]:
            send(
                chat_id,
                "Сегодня специальное задание не выпало. /clean работает, только если в /task написано "
                "«убрать лоток» или «убрать сюрприз». Недельный уход засчитывает выпавшие задания — проверь /weekly.",
            )
        elif daily["task_done"]:
            send(chat_id, f"Сегодня уже выполнено: {TASKS[str(daily['task'])]}. Посмотри новое состояние через /task.")
        else:
            task_command = {"brush": "/brush", "litter": "/clean", "vomit": "/clean", "feed": "/feed"}[str(daily["task"])]
            send(
                chat_id,
                f"Сегодня выпало задание: {TASKS[str(daily['task'])]}. Для него используй {task_command}. "
                "Команды /brush и /clean не выполняют другие задания; смотри /task.",
            )
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
    db.execute(f"UPDATE cats SET {stat}={stat}+1 WHERE cat_id=?", (cat["cat_id"],))
    change_stones(db, cat, -cost, "тренировка", user_id, stat)
    db.commit()
    display_name, emoji = STAT_NAMES[stat]
    new_value = current + 1
    extra = " Максимальное настроение увеличилось на 1." if stat == "endurance" else ""
    send(chat_id, f"🏋️ {emoji} {display_name.capitalize()}: {current} → {new_value}. Потрачено {cost} камушков.{extra}")


def _handle_stone_fishing(
    db: sqlite3.Connection, chat_id: int, user_id: int, args: list[str], send: GameSender
) -> None:
    if args:
        send(chat_id, "Рыбалка запускается без параметров: /fishing.")
        return
    cat = _current_cat(db, chat_id, user_id)
    if not cat:
        send(chat_id, "Сначала заведи котика командой /cat Имя.")
        return

    day = _today().isoformat()
    cursor = db.execute(
        "INSERT OR IGNORE INTO stone_fishing_claims(chat_id,user_id,day) VALUES(?,?,?)",
        (chat_id, user_id, day),
    )
    if cursor.rowcount == 0:
        send(chat_id, "🎣 Сегодня котик уже рыбачил. Следующая попытка будет завтра.")
        return

    found_stone = random.random() < STONE_FISHING_SUCCESS_CHANCE
    if found_stone:
        change_stones(db, cat, 1, "рыбалка", user_id)
        db.commit()
        send(chat_id, f"🎣 {cat['name']} вытащил камушек! +1 камушек. Теперь у котика {int(cat['stones']) + 1}.")
    else:
        db.commit()
        send(chat_id, f"🐟 {cat['name']} сегодня ничего не поймал. Попробуй снова завтра!")


def handle_command(
    db: sqlite3.Connection,
    message: dict[str, Any],
    command: str,
    args: list[str],
    send: GameSender,
) -> bool:
    if command not in {"cat", "rename", "task", "feed", "brush", "clean", "train", "fishing", "stat"}:
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
                sent = send(
                    chat_id,
                    "🐾 Чтобы начать общаться в беседе, сначала заведи котика.\n"
                    "Напиши имя в ответ на это сообщение или отправь команду /cat Снежок.\n"
                    "Имя — до 24 символов.",
                    reply_markup={"force_reply": True, "input_field_placeholder": "Имя котика"},
                    auto_delete_after=300,
                )
                prompt_id = sent.get("message_id") if isinstance(sent, dict) else None
                if prompt_id:
                    db.execute(
                        "INSERT INTO cat_name_prompts(chat_id,user_id,prompt_message_id,expires_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(chat_id,user_id) DO UPDATE SET prompt_message_id=excluded.prompt_message_id,expires_at=excluded.expires_at",
                        (chat_id, user_id, int(prompt_id), int(time.time()) + 300),
                    )
                    db.commit()
            else:
                send(chat_id, "Не получилось создать котика. Попробуй /cat Снежок.")
            return True
        send(chat_id, _status_text(cat, daily))
    elif command == "stat":
        cat = _current_cat(db, chat_id, user_id)
        if not cat:
            send(chat_id, "Сначала заведи котика командой /cat ИМЯ.")
            return True
        entries = db.execute(
            "SELECT delta,source,details FROM stone_ledger WHERE chat_id=? AND user_id=? "
            "ORDER BY entry_id DESC LIMIT 8",
            (chat_id, user_id),
        ).fetchall()
        lines = [f"📊 {cat['name']}: 🪨 {cat['stones']} камушков"]
        if entries:
            lines.append("\nПоследние изменения:")
            for entry in entries:
                sign = "+" if int(entry["delta"]) > 0 else ""
                detail = f" — {entry['details']}" if entry["details"] else ""
                lines.append(f"• {sign}{entry['delta']} · {entry['source']}{detail}")
        else:
            lines.append("Пока изменений баланса не было.")
        send(chat_id, "\n".join(lines))
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
            "UPDATE cats SET name=? WHERE cat_id=?",
            (name, cat["cat_id"]),
        )
        change_stones(db, cat, -RENAME_COST, "смена имени", user_id, name)
        db.commit()
        send(chat_id, f"✨ Теперь котика зовут {name}. Потрачено {RENAME_COST} камушков.")
    elif command == "task":
        prompt, keyboard, notices = _task_prompt(db, chat_id, user_id)
        _send_notices(send, chat_id, notices)
        extra = {"reply_markup": keyboard} if keyboard else {}
        send(chat_id, prompt, **extra)
    elif command == "feed":
        _handle_feed(db, chat_id, user_id, send)
    elif command == "fishing":
        _handle_stone_fishing(db, chat_id, user_id, args, send)
    elif command in ("brush", "clean"):
        _handle_care(db, chat_id, user_id, command, send)
    elif command == "train":
        _handle_train(db, chat_id, user_id, args, send)
    return True


def handle_cat_name_reply(
    db: sqlite3.Connection, message: dict[str, Any], send: GameSender
) -> bool:
    """Treat a reply to the /cat prompt as a cat-creation name."""
    chat_id = int(message.get("chat", {}).get("id", 0))
    user_id = int(message.get("from", {}).get("id", 0))
    reply_id = int(message.get("reply_to_message", {}).get("message_id", 0))
    if not chat_id or not user_id or not reply_id:
        return False
    prompt = db.execute(
        "SELECT * FROM cat_name_prompts WHERE chat_id=? AND user_id=? AND prompt_message_id=?",
        (chat_id, user_id, reply_id),
    ).fetchone()
    if not prompt:
        return False
    if int(prompt["expires_at"]) <= int(time.time()):
        db.execute("DELETE FROM cat_name_prompts WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        db.commit()
        send(chat_id, "Подсказка устарела. Отправь /cat ИМЯ, например /cat Снежок.")
        return True
    name = _cat_name([str(message.get("text", ""))])
    if not name:
        send(chat_id, "Имя должно содержать от 1 до 24 символов. Ответь на подсказку ещё раз.")
        return True
    db.execute("DELETE FROM cat_name_prompts WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    db.commit()
    handle_command(db, message, "cat", [name], send)
    return True


def handle_task_callback(
    db: sqlite3.Connection,
    callback: dict[str, Any],
    send: GameSender,
    answer_callback: Callable[[str, str], Any],
    edit_message: Callable[..., Any],
) -> bool:
    data = str(callback.get("data", ""))
    if not data.startswith("care:"):
        return False
    query_id = str(callback.get("id", ""))
    user = callback.get("from", {})
    user_id = int(user.get("id", 0))
    message = callback.get("message", {})
    chat = message.get("chat", {})
    chat_id = int(chat.get("id", 0))
    message_id = int(message.get("message_id", 0))
    try:
        _, owner_text, action, day_text = data.split(":", 3)
        owner_id = int(owner_text)
    except (ValueError, TypeError):
        answer_callback(query_id, "Кнопка устарела. Отправь /task ещё раз.")
        return True
    if user_id != owner_id:
        answer_callback(query_id, "Эта кнопка предназначена владельцу котика.")
        return True
    if action not in {"brush", "clean", "feed"}:
        answer_callback(query_id, "Неизвестное действие. Открой /task ещё раз.")
        return True
    cat, daily, notices = _prepare_cat(db, chat_id, user_id)
    if not cat:
        answer_callback(query_id, "Сначала заведи котика командой /cat ИМЯ.")
        return True
    _send_notices(send, chat_id, notices)
    if str(daily["day"]) != day_text:
        answer_callback(query_id, "Это задание уже устарело. Открой /task, чтобы увидеть сегодняшнее.")
        return True
    expected_action = {
        "brush": "brush", "litter": "clean", "vomit": "clean", "feed": "feed",
    }.get(str(daily["task"]))
    task_button = expected_action == action and not bool(daily["task_done"])
    daily_feed_button = action == "feed" and not bool(daily["fed"])
    if not task_button and not daily_feed_button:
        answer_callback(query_id, "Задание уже выполнено или изменилось. Открой /task ещё раз.")
        return True

    responses: list[str] = []

    def capture(_chat_id: int, text: str, **_extra: Any) -> None:
        responses.append(str(text))

    if action == "feed":
        _handle_feed(db, chat_id, user_id, capture)
    else:
        _handle_care(db, chat_id, user_id, action, capture)
    prompt, keyboard, extra_notices = _task_prompt(db, chat_id, user_id)
    _send_notices(send, chat_id, extra_notices)
    if responses:
        prompt += "\n\n" + responses[-1]
    edit_message(
        chat_id, message_id, prompt,
        keyboard if keyboard else {"inline_keyboard": []},
    )
    answer_callback(query_id, "Готово")
    return True


def has_cat(db: sqlite3.Connection, chat_id: int, user_id: int) -> bool:
    return _current_cat(db, chat_id, user_id) is not None


def should_send_cat_gate_notice(db: sqlite3.Connection, chat_id: int, user_id: int) -> bool:
    now = int(time.time())
    row = db.execute(
        "SELECT sent_at FROM cat_gate_notices WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    if row and int(row["sent_at"]) > now - 300:
        return False
    db.execute(
        "INSERT INTO cat_gate_notices(chat_id,user_id,sent_at) VALUES(?,?,?) "
        "ON CONFLICT(chat_id,user_id) DO UPDATE SET sent_at=excluded.sent_at",
        (chat_id, user_id, now),
    )
    db.commit()
    return True


def announce_due_daily_tasks(db: sqlite3.Connection, send: GameSender) -> None:
    """At 10:00 and 20:00 Yekaterinburg time, mention members with a daily task."""
    now = datetime.now(ZoneInfo("Asia/Yekaterinburg"))
    if not ((now.hour == 10 or now.hour == 20) and now.minute < 15):
        return
    slot = 10 if now.hour == 10 else 20
    day = now.date()
    groups = db.execute("SELECT DISTINCT chat_id FROM cats WHERE chat_id<0").fetchall()
    for group in groups:
        chat_id = int(group["chat_id"])
        cursor = db.execute(
            "INSERT OR IGNORE INTO daily_task_announcements(chat_id,day,slot,status) VALUES(?,?,?,'posting')",
            (chat_id, day.isoformat(), slot),
        )
        if cursor.rowcount == 0:
            continue
        current_cats = db.execute(
            "SELECT c.* FROM cats c WHERE c.chat_id=? AND c.cat_id=("
            "SELECT MAX(c2.cat_id) FROM cats c2 WHERE c2.chat_id=c.chat_id AND c2.user_id=c.user_id)",
            (chat_id,),
        ).fetchall()
        lines: list[str] = []
        for cat in current_cats:
            daily = _daily(db, int(cat["cat_id"]), day)
            if not daily["task"] or daily["task_done"]:
                continue
            user = db.execute(
                "SELECT username,first_name FROM known_users WHERE chat_id=? AND user_id=?",
                (chat_id, cat["user_id"]),
            ).fetchone()
            if user and user["username"]:
                who = "@" + str(user["username"])
            else:
                display = html.escape(str(user["first_name"] if user else cat["name"]))
                who = f'<a href="tg://user?id={int(cat["user_id"])}">{display}</a>'
            lines.append(f"• {who} — {TASKS[str(daily['task'])]} ({html.escape(str(cat['name']))})")
        try:
            if lines:
                send(
                    chat_id,
                    f"🐾 Ежедневные задания на сегодня — {slot:02d}:00, Екатеринбург:\n" + "\n".join(lines),
                    parse_mode="HTML",
                )
        except Exception:
            db.execute(
                "DELETE FROM daily_task_announcements WHERE chat_id=? AND day=? AND slot=?",
                (chat_id, day.isoformat(), slot),
            )
            db.commit()
            raise
        db.execute(
            "UPDATE daily_task_announcements SET status='sent' WHERE chat_id=? AND day=? AND slot=?",
            (chat_id, day.isoformat(), slot),
        )
        db.commit()


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
            send(
                chat_id,
                f"🐟 {cat['color'].capitalize()} котик нашёл рыбку! В запасе: {int(cat['fish']) + stored}/{MAX_FISH}.",
                auto_delete_after=FISH_NOTICE_DELETE_SECONDS,
            )
        else:
            send(
                chat_id,
                f"🐟 Запас рыбок у {cat['name']} полон ({MAX_FISH}); новая рыбка не поместилась.",
                auto_delete_after=FISH_NOTICE_DELETE_SECONDS,
            )
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
    _add_progress(db, cat, xp=1, stones=1, stone_source="победа в мини-игре", actor_id=user_id)
    return True
