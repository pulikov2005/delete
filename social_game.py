"""Relationships, useful items, and non-combat group mini-games."""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import game

GameSender = Callable[..., Any]
RELATIONSHIP_DAYS = 3
MAX_STAKE = 1000
MARRIAGE_WAIT_DAYS = 7
MARRIAGE_TRUST_REQUIRED = 70
MARRIAGE_COST = 20
KITTEN_CHANCE = 0.30
QUIZ_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
QUIZ_TIMES = ((10, 0), (15, 0), (20, 0))
QUIZ_POST_GRACE = timedelta(minutes=15)
QUIZ_DAILY_QUESTIONS = 3
KITTEN_RARITIES = (("common", 60), ("medium", 25), ("rare", 12), ("superrare", 3))
KITTEN_RARITY_NAMES = {
    "common": "обычный",
    "medium": "средней редкости",
    "rare": "очень редкий",
    "superrare": "суперредкий",
}
KITTEN_ABILITIES = {
    "common": "коллекционный, без игрового бонуса",
    "medium": "+1 настроение при кормлении",
    "rare": "+1 XP за первое задание дня",
    "superrare": "+1 камушек за первое задание дня",
}

ITEMS = {
    "feeder": ("Автокормушка", 25, "постоянный предмет"),
    "toy": ("Игрушечная мышка", 8, "расходуется при использовании"),
    "wand": ("Палочка нежности", 15, "расходуется при использовании"),
}
ITEM_ALIASES = {
    "feeder": "feeder", "autofeeder": "feeder", "кормушка": "feeder", "автокормушка": "feeder",
    "toy": "toy", "mouse": "toy", "игрушка": "toy", "мышка": "toy", "мышь": "toy",
    "wand": "wand", "палочка": "wand", "жезл": "wand",
}


def setup_db(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS cat_items (
            cat_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(cat_id,item_key)
        );
        CREATE TABLE IF NOT EXISTS relationship_progress (
            chat_id INTEGER NOT NULL,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            day TEXT NOT NULL,
            a_action INTEGER NOT NULL DEFAULT 0,
            b_action INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id,user_a,user_b,day)
        );
        CREATE TABLE IF NOT EXISTS relationships (
            chat_id INTEGER NOT NULL,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            status TEXT NOT NULL,
            trust INTEGER NOT NULL DEFAULT 100,
            created_on TEXT NOT NULL,
            ended_on TEXT,
            PRIMARY KEY(chat_id,user_a,user_b)
        );
        CREATE INDEX IF NOT EXISTS relationships_active_a
            ON relationships(chat_id,user_a,status);
        CREATE INDEX IF NOT EXISTS relationships_active_b
            ON relationships(chat_id,user_b,status);
        CREATE TABLE IF NOT EXISTS relationship_actions (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            action TEXT NOT NULL,
            PRIMARY KEY(chat_id,user_id,day,action)
        );
        CREATE TABLE IF NOT EXISTS marriages (
            chat_id INTEGER NOT NULL,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            status TEXT NOT NULL,
            proposed_by INTEGER NOT NULL,
            proposed_on TEXT NOT NULL,
            married_on TEXT,
            ended_on TEXT,
            PRIMARY KEY(chat_id,user_a,user_b)
        );
        CREATE TABLE IF NOT EXISTS marriage_weekly_rolls (
            chat_id INTEGER NOT NULL,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            week_index INTEGER NOT NULL,
            PRIMARY KEY(chat_id,user_a,user_b,week_index)
        );
        CREATE TABLE IF NOT EXISTS mini_kittens (
            kitten_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            rarity TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_on TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS mini_kittens_family
            ON mini_kittens(chat_id,user_a,user_b,status);
        CREATE TABLE IF NOT EXISTS active_kittens (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            kitten_id INTEGER NOT NULL,
            PRIMARY KEY(chat_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS ttt_games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            player_x INTEGER NOT NULL,
            player_o INTEGER NOT NULL,
            stake INTEGER NOT NULL,
            state TEXT NOT NULL,
            board TEXT NOT NULL DEFAULT '.........',
            turn_user INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ttt_games_x_state
            ON ttt_games(chat_id,player_x,state);
        CREATE INDEX IF NOT EXISTS ttt_games_o_state
            ON ttt_games(chat_id,player_o,state);
        CREATE TABLE IF NOT EXISTS race_games (
            race_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            player_a INTEGER NOT NULL,
            player_b INTEGER NOT NULL,
            stake INTEGER NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS race_games_a_state
            ON race_games(chat_id,player_a,state);
        CREATE INDEX IF NOT EXISTS race_games_b_state
            ON race_games(chat_id,player_b,state);
        CREATE TABLE IF NOT EXISTS weekly_quests (
            cat_id INTEGER NOT NULL,
            week_start TEXT NOT NULL,
            kind TEXT NOT NULL,
            target INTEGER NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(cat_id,week_start)
        );
        CREATE TABLE IF NOT EXISTS weekly_stats (
            cat_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            week_start TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            messages INTEGER NOT NULL DEFAULT 0,
            care_done INTEGER NOT NULL DEFAULT 0,
            quiz_correct INTEGER NOT NULL DEFAULT 0,
            games_played INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(cat_id,week_start)
        );
        CREATE INDEX IF NOT EXISTS weekly_stats_chat
            ON weekly_stats(chat_id,week_start,points);
        CREATE TABLE IF NOT EXISTS quiz_sessions (
            chat_id INTEGER NOT NULL,
            cat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            question_index INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id,cat_id,day)
        );
        CREATE TABLE IF NOT EXISTS daily_quiz_posts (
            post_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            slot INTEGER NOT NULL,
            question_index INTEGER NOT NULL,
            message_id INTEGER,
            winner_user_id INTEGER,
            status TEXT NOT NULL DEFAULT 'posting',
            created_at INTEGER NOT NULL,
            UNIQUE(chat_id,day,slot)
        );
        CREATE INDEX IF NOT EXISTS daily_quiz_posts_day
            ON daily_quiz_posts(chat_id,day,status,slot);
        CREATE TABLE IF NOT EXISTS daily_quiz_answers (
            post_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            cat_id INTEGER NOT NULL,
            choice INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            answered_at INTEGER NOT NULL,
            PRIMARY KEY(post_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS daily_quiz_results (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            answered_count INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id,user_id,day)
        );
        """
    )
    db.commit()


WEEKLY_QUESTS = {
    "messages": ("Напиши 25 сообщений в беседе", 25),
    "care": ("Выполни 3 задания по уходу за котиком", 3),
    "games": ("Сыграй в 2 мини-игры", 2),
    "quiz": ("Ответь на все 3 вопроса общей викторины", 1),
}
QUIZ_QUESTIONS = (
    ("Как называют детёныша кошки?", ("Котёнок", "Щенок", "Жеребёнок"), 1),
    ("Сколько лап у здоровой кошки?", ("Две", "Четыре", "Шесть"), 2),
    ("Что кошка обычно делает, когда ей спокойно и приятно?", ("Мурлычет", "Квакает", "Каркает"), 1),
    ("Как называется домик или место для кошачьего туалета?", ("Лоток", "Аквариум", "Клетка"), 1),
    ("Какой звук обычно издаёт кошка?", ("Мяу", "Гав", "Му"), 1),
    ("Что кошке удобно точить о когтеточку?", ("Когти", "Усы", "Хвост"), 1),
    ("Какой из этих продуктов лучше дать кошке как обычное питьё?", ("Свежую воду", "Газировку", "Кофе"), 1),
    ("Какая часть тела помогает кошке держать равновесие?", ("Хвост", "Ухо", "Нос"), 1),
    ("Когда кошки чаще всего видят в темноте лучше людей?", ("При слабом свете", "В полной темноте без света", "Никак"), 1),
    ("Как называется врач для животных?", ("Ветеринар", "Повар", "Пилот"), 1),
    ("Что кошке нужно для безопасной игры?", ("Игрушка", "Стекло", "Открытое окно"), 1),
    ("Какой орган кошка часто использует для умывания?", ("Язык", "Локоть", "Колено"), 1),
)


def _week_start(day: date | None = None) -> date:
    day = day or game._today()
    return day - timedelta(days=day.weekday())


def _ensure_weekly_quest(db: sqlite3.Connection, cat: sqlite3.Row) -> sqlite3.Row:
    week = _week_start().isoformat()
    row = db.execute(
        "SELECT * FROM weekly_quests WHERE cat_id=? AND week_start=?",
        (cat["cat_id"], week),
    ).fetchone()
    if row:
        return row
    kind = random.choice(tuple(WEEKLY_QUESTS))
    target = WEEKLY_QUESTS[kind][1]
    db.execute(
        "INSERT OR IGNORE INTO weekly_quests(cat_id,week_start,kind,target) VALUES(?,?,?,?)",
        (cat["cat_id"], week, kind, target),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM weekly_quests WHERE cat_id=? AND week_start=?",
        (cat["cat_id"], week),
    ).fetchone()


def weekly_status(db: sqlite3.Connection, chat_id: int, user_id: int) -> str:
    cat = _current_cat(db, chat_id, user_id)
    if not cat:
        return "Сначала заведи котика командой /cat Имя."
    quest = _ensure_weekly_quest(db, cat)
    title = WEEKLY_QUESTS[str(quest["kind"])][0]
    progress = min(int(quest["progress"]), int(quest["target"]))
    state = "✅ выполнено" if quest["completed"] else f"{progress}/{quest['target']}"
    stones = 10 + (2 if cat["personality"] == "Упорный" else 0)
    return (
        f"📅 Недельное задание для {cat['name']}\n{title}: {state}\n"
        f"Награда: +8 XP и +{stones} камушков. Сброс — в понедельник.\n"
        "Выполненные задания также добавляют очки в /leaderboard."
    )


def record_weekly_event(
    db: sqlite3.Connection,
    cat: sqlite3.Row,
    event: str,
    amount: int = 1,
    quiz_score: int = 0,
    winner: bool = False,
) -> bool:
    """Record weekly points and quest progress; return True on first quest completion."""
    amount = max(0, int(amount))
    if not amount:
        return False
    week = _week_start().isoformat()
    cat_id, chat_id = int(cat["cat_id"]), int(cat["chat_id"])
    _ensure_weekly_quest(db, cat)
    db.execute(
        "INSERT OR IGNORE INTO weekly_stats(cat_id,chat_id,week_start) VALUES(?,?,?)",
        (cat_id, chat_id, week),
    )
    stats = db.execute(
        "SELECT * FROM weekly_stats WHERE cat_id=? AND week_start=?", (cat_id, week)
    ).fetchone()
    points = 0
    if event == "messages":
        old = int(stats["messages"])
        new = old + amount
        points = new // 5 - old // 5
        db.execute(
            "UPDATE weekly_stats SET messages=?,points=points+? WHERE cat_id=? AND week_start=?",
            (new, points, cat_id, week),
        )
    elif event == "care":
        points = 3 * amount
        db.execute(
            "UPDATE weekly_stats SET care_done=care_done+?,points=points+? WHERE cat_id=? AND week_start=?",
            (amount, points, cat_id, week),
        )
    elif event == "quiz":
        correct = max(0, int(quiz_score))
        points = 2 * correct
        db.execute(
            "UPDATE weekly_stats SET quiz_correct=quiz_correct+?,points=points+? WHERE cat_id=? AND week_start=?",
            (correct, points, cat_id, week),
        )
    elif event == "games":
        points = 3 * amount + (2 if winner else 0)
        db.execute(
            "UPDATE weekly_stats SET games_played=games_played+?,points=points+? WHERE cat_id=? AND week_start=?",
            (amount, points, cat_id, week),
        )
    else:
        return False
    quest = db.execute(
        "SELECT * FROM weekly_quests WHERE cat_id=? AND week_start=?", (cat_id, week)
    ).fetchone()
    newly_completed = False
    if not quest["completed"] and quest["kind"] == event:
        progress = min(int(quest["target"]), int(quest["progress"]) + amount)
        newly_completed = progress >= int(quest["target"])
        db.execute(
            "UPDATE weekly_quests SET progress=?,completed=? WHERE cat_id=? AND week_start=?",
            (progress, int(newly_completed), cat_id, week),
        )
        if newly_completed:
            fresh_cat = db.execute("SELECT * FROM cats WHERE cat_id=?", (cat_id,)).fetchone()
            extra_stones = 2 if fresh_cat["personality"] == "Упорный" else 0
            game._add_progress(db, fresh_cat, xp=8, stones=10 + extra_stones)
            db.execute(
                "UPDATE weekly_stats SET points=points+10 WHERE cat_id=? AND week_start=?",
                (cat_id, week),
            )
    db.commit()
    return newly_completed


def weekly_leaderboard(db: sqlite3.Connection, chat_id: int) -> str:
    week = _week_start().isoformat()
    rows = db.execute(
        "SELECT c.name,c.personality,c.level,s.points FROM weekly_stats s "
        "JOIN cats c ON c.cat_id=s.cat_id WHERE s.chat_id=? AND s.week_start=? "
        "ORDER BY s.points DESC,c.total_xp DESC,c.cat_id ASC LIMIT 10",
        (chat_id, week),
    ).fetchall()
    if not rows:
        return "🏆 Недельная таблица пока пуста. Начни с /weekly, ухода, викторины или мини-игры."
    lines = ["🏆 Таблица лидеров этой недели"]
    medals = ("🥇", "🥈", "🥉")
    lines.extend(
        f"{medals[index] if index < 3 else f'{index + 1}.'} {row['name']} — {row['points']} очк. · ур. {row['level']} · {row['personality']}"
        for index, row in enumerate(rows)
    )
    return "\n".join(lines)


def publish_due_quiz_questions(db: sqlite3.Connection, send: GameSender) -> None:
    """Post one shared daily question per group at each scheduled local time."""
    now = datetime.now(QUIZ_TIMEZONE)
    day = now.date().isoformat()
    slot: int | None = None
    for index, (hour, minute) in enumerate(QUIZ_TIMES):
        scheduled = datetime(now.year, now.month, now.day, hour, minute, tzinfo=QUIZ_TIMEZONE)
        if scheduled <= now < scheduled + QUIZ_POST_GRACE:
            slot = index
            break
    db.execute(
        "UPDATE daily_quiz_posts SET status='closed' WHERE status='open' AND day<>?",
        (day,),
    )
    db.commit()
    if slot is None:
        return

    groups = db.execute("SELECT DISTINCT chat_id FROM known_users WHERE chat_id<0").fetchall()
    for group in groups:
        chat_id = int(group["chat_id"])
        post = db.execute(
            "SELECT * FROM daily_quiz_posts WHERE chat_id=? AND day=? AND slot=?",
            (chat_id, day, slot),
        ).fetchone()
        if post and post["status"] in {"open", "failed", "closed"}:
            continue
        if post is None:
            used = {
                int(row["question_index"])
                for row in db.execute(
                    "SELECT question_index FROM daily_quiz_posts WHERE chat_id=? AND day=?",
                    (chat_id, day),
                ).fetchall()
            }
            available = [index for index in range(len(QUIZ_QUESTIONS)) if index not in used]
            question_index = random.choice(available or list(range(len(QUIZ_QUESTIONS))))
            cursor = db.execute(
                "INSERT INTO daily_quiz_posts(chat_id,day,slot,question_index,status,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (chat_id, day, slot, question_index, "posting", int(time.time())),
            )
            post_id = int(cursor.lastrowid)
            db.commit()
        else:
            post_id = int(post["post_id"])
            question_index = int(post["question_index"])

        question, options, _ = QUIZ_QUESTIONS[question_index]
        hour, minute = QUIZ_TIMES[slot]
        text = (
            f"🐾 <b>Общая викторина · вопрос {slot + 1}/{QUIZ_DAILY_QUESTIONS}</b>\n"
            f"Сегодня в {hour:02d}:{minute:02d} по Екатеринбургу\n\n{question}\n\n"
            "На вопрос одна попытка; награду получает первый верный ответ. Выбери вариант кнопкой или ответь командой /answer 1, /answer 2 либо /answer 3."
        )
        keyboard = {
            "inline_keyboard": [
                [{"text": f"{choice}. {option}", "callback_data": f"cq:{post_id}:{choice}"}]
                for choice, option in enumerate(options, 1)
            ]
        }
        try:
            sent = send(chat_id, text, parse_mode="HTML", reply_markup=keyboard, auto_delete=False)
        except Exception:
            db.execute("UPDATE daily_quiz_posts SET status='failed' WHERE post_id=?", (post_id,))
            db.commit()
            logging.exception("Could not post daily quiz in chat %s", chat_id)
            continue
        message_id = sent.get("message_id") if isinstance(sent, dict) else None
        db.execute(
            "UPDATE daily_quiz_posts SET message_id=?,status='open' WHERE post_id=?",
            (message_id, post_id),
        )
        db.commit()


def _quiz_status(db: sqlite3.Connection, chat_id: int, user_id: int) -> str:
    cat = _current_cat(db, chat_id, user_id)
    if not cat:
        return "Сначала заведи котика командой /cat Имя."
    today = game._today().isoformat()
    result = db.execute(
        "SELECT answered_count,correct_count,completed FROM daily_quiz_results "
        "WHERE chat_id=? AND user_id=? AND day=?",
        (chat_id, user_id, today),
    ).fetchone()
    answered = int(result["answered_count"]) if result else 0
    correct = int(result["correct_count"]) if result else 0
    wins = int(db.execute(
        "SELECT COUNT(*) FROM daily_quiz_posts WHERE chat_id=? AND day=? AND winner_user_id=?",
        (chat_id, today, user_id),
    ).fetchone()[0])
    if result and result["completed"]:
        progress = f"Сегодня отвечено на все 3 вопроса: {correct}/3 верно; выиграно первым ответом: {wins}/3."
    else:
        progress = f"Сегодня отвечено: {answered}/3; верных ответов: {correct}; выиграно первым ответом: {wins}."
    schedule = ", ".join(f"{hour:02d}:{minute:02d}" for hour, minute in QUIZ_TIMES)
    return (
        f"🐾 Общая викторина беседы: три вопроса в день ({schedule}, Екатеринбург).\n"
        f"{progress}\nНажимай кнопки в сообщениях викторины или используй /answer 1, /answer 2, /answer 3."
    )


def _submit_quiz_answer(
    db: sqlite3.Connection,
    chat_id: int,
    user_id: int,
    post_id: int,
    choice: int,
) -> tuple[str, bool]:
    today = game._today().isoformat()
    post = db.execute(
        "SELECT * FROM daily_quiz_posts WHERE post_id=? AND chat_id=? AND day=? AND status='open'",
        (post_id, chat_id, today),
    ).fetchone()
    if not post:
        return "Этот вопрос уже закрыт или не найден.", False
    if choice not in (1, 2, 3):
        return "Выбери ответ от 1 до 3.", False
    cat = _current_cat(db, chat_id, user_id)
    if not cat:
        return "Сначала заведи котика командой /cat Имя.", False
    question, options, correct_choice = QUIZ_QUESTIONS[int(post["question_index"])]
    is_correct = choice == int(correct_choice)
    try:
        db.execute(
            "INSERT INTO daily_quiz_answers(post_id,chat_id,user_id,cat_id,choice,correct,answered_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (post_id, chat_id, user_id, cat["cat_id"], choice, int(is_correct), int(time.time())),
        )
    except sqlite3.IntegrityError:
        return "Ты уже отвечал(а) на этот вопрос.", False
    db.execute(
        "INSERT INTO daily_quiz_results(chat_id,user_id,day,answered_count,correct_count) VALUES(?,?,?,1,?) "
        "ON CONFLICT(chat_id,user_id,day) DO UPDATE SET "
        "answered_count=answered_count+1,correct_count=correct_count+excluded.correct_count",
        (chat_id, user_id, today, int(is_correct)),
    )
    if is_correct:
        db.execute(
            "UPDATE daily_quiz_posts SET winner_user_id=? WHERE post_id=? AND winner_user_id IS NULL",
            (user_id, post_id),
        )
    db.commit()

    result = db.execute(
        "SELECT * FROM daily_quiz_results WHERE chat_id=? AND user_id=? AND day=?",
        (chat_id, user_id, today),
    ).fetchone()
    completed_now = int(result["answered_count"]) >= QUIZ_DAILY_QUESTIONS and not bool(result["completed"])
    quest_done = False
    bonus_xp = 0
    first_correct = bool(
        is_correct
        and db.execute(
            "SELECT winner_user_id FROM daily_quiz_posts WHERE post_id=?",
            (post_id,),
        ).fetchone()["winner_user_id"] == user_id
    )
    if first_correct:
        game._add_progress(db, cat, xp=1, stones=2)
    if completed_now:
        db.execute(
            "UPDATE daily_quiz_results SET completed=1 WHERE chat_id=? AND user_id=? AND day=? AND completed=0",
            (chat_id, user_id, today),
        )
        db.commit()
        wins = int(db.execute(
            "SELECT COUNT(*) FROM daily_quiz_posts WHERE chat_id=? AND day=? AND winner_user_id=?",
            (chat_id, today, user_id),
        ).fetchone()[0])
        if cat["personality"] == "Любознательный" and wins == QUIZ_DAILY_QUESTIONS:
            bonus_xp = 1
            game._add_progress(db, cat, xp=bonus_xp, stones=0)
        quest_done = record_weekly_event(db, cat, "quiz", quiz_score=wins)

    if first_correct:
        response = "🏆 Первый правильный ответ! +1 XP и +2 камушка."
    elif is_correct:
        response = "✅ Верно, но награду за этот вопрос уже забрал первый ответивший."
    else:
        response = f"❌ Неверно. Правильный ответ: {options[int(correct_choice) - 1]}."
    if completed_now:
        wins = int(db.execute(
            "SELECT COUNT(*) FROM daily_quiz_posts WHERE chat_id=? AND day=? AND winner_user_id=?",
            (chat_id, today, user_id),
        ).fetchone()[0])
        response += f"\n📊 Дневной результат: {result['correct_count']}/3 верно, выиграно вопросов: {wins}/3."
        if bonus_xp:
            response += " Характер «Любознательный»: +1 XP."
        if quest_done:
            response += " Недельное задание выполнено!"
    return response, True


def _quiz_command(
    db: sqlite3.Connection,
    chat_id: int,
    user_id: int,
    command: str,
    args: list[str],
    send: GameSender,
) -> None:
    if command == "quiz":
        send(chat_id, _quiz_status(db, chat_id, user_id))
        return
    if not args or args[0] not in {"1", "2", "3"}:
        send(chat_id, "Выбери вариант: /answer 1, /answer 2 или /answer 3.")
        return
    today = game._today().isoformat()
    post = db.execute(
        "SELECT p.* FROM daily_quiz_posts p LEFT JOIN daily_quiz_answers a "
        "ON a.post_id=p.post_id AND a.user_id=? "
        "WHERE p.chat_id=? AND p.day=? AND p.status='open' AND a.post_id IS NULL "
        "ORDER BY p.slot DESC LIMIT 1",
        (user_id, chat_id, today),
    ).fetchone()
    if not post:
        send(chat_id, "Пока нет вопроса, на который ты ещё не ответил(а). Проверь расписание командой /quiz.")
        return
    response, _ = _submit_quiz_answer(db, chat_id, user_id, int(post["post_id"]), int(args[0]))
    send(chat_id, response)


def handle_quiz_callback(
    db: sqlite3.Connection,
    callback: dict[str, Any],
    send: GameSender,
    answer_callback: Callable[[str, str], Any],
) -> bool:
    data = str(callback.get("data", ""))
    if not data.startswith("cq:"):
        return False
    query_id = str(callback.get("id", ""))
    user_id = int(callback.get("from", {}).get("id", 0))
    message = callback.get("message", {})
    chat_id = int(message.get("chat", {}).get("id", 0))
    try:
        _, post_id_text, choice_text = data.split(":", 2)
        post_id, choice = int(post_id_text), int(choice_text)
        response, accepted = _submit_quiz_answer(db, chat_id, user_id, post_id, choice)
    except (ValueError, TypeError):
        response, accepted = "Не получилось прочитать ответ. Попробуй ещё раз.", False
    answer_callback(query_id, response[:190])
    if not accepted and response.startswith("Сначала заведи котика") and chat_id:
        send(chat_id, response)
    return True


def finalize_auto_feed(
    db: sqlite3.Connection, cat: sqlite3.Row, daily: sqlite3.Row
) -> sqlite3.Row:
    """Use one stored feeder fish at day end, before missed-care mood changes."""
    if daily["fed"]:
        return daily
    current = db.execute(
        "SELECT feeder_food,mood FROM cats WHERE cat_id=?", (cat["cat_id"],)
    ).fetchone()
    if not current or int(current["feeder_food"]) < 1:
        return daily
    mood_room = max(0, game._max_mood(cat) - int(current["mood"]))
    mood_added = min(mood_room, 1 + mood_bonus_for_cat(db, cat))
    db.execute(
        "UPDATE cats SET feeder_food=feeder_food-1,mood=mood+? WHERE cat_id=?",
        (mood_added, cat["cat_id"]),
    )
    task_needs_reward = daily["task"] == "feed" and not bool(daily["task_done"])
    db.execute(
        "UPDATE cat_days SET fed=1,task_done=CASE WHEN task='feed' THEN 1 ELSE task_done END "
        "WHERE cat_id=? AND day=?",
        (cat["cat_id"], daily["day"]),
    )
    if task_needs_reward:
        extra_xp, extra_stones = task_bonus_for_cat(db, cat)
        game._add_progress(db, cat, xp=3 + extra_xp, stones=2 + extra_stones)
        record_weekly_event(db, cat, "care")
    return db.execute(
        "SELECT * FROM cat_days WHERE cat_id=? AND day=?",
        (cat["cat_id"], daily["day"]),
    ).fetchone()


def _pair(user_a: int, user_b: int) -> tuple[int, int]:
    return (user_a, user_b) if user_a < user_b else (user_b, user_a)


def _current_cat(db: sqlite3.Connection, chat_id: int, user_id: int) -> sqlite3.Row | None:
    return game._current_cat(db, chat_id, user_id)


def _cat_for(db: sqlite3.Connection, chat_id: int, user_id: int) -> sqlite3.Row | None:
    cat, _, notices = game._prepare_cat(db, chat_id, user_id)
    return cat


def _known_user(db: sqlite3.Connection, chat_id: int, user_id: int) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT user_id,username,first_name FROM known_users WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    if not row:
        return None
    return {"id": int(row["user_id"]), "username": row["username"], "first_name": row["first_name"]}


def _target(
    db: sqlite3.Connection, chat_id: int, message: dict[str, Any], args: list[str]
) -> dict[str, Any] | None:
    reply = message.get("reply_to_message", {}).get("from")
    if reply and reply.get("id"):
        return {
            "id": int(reply["id"]),
            "username": reply.get("username"),
            "first_name": reply.get("first_name", "участник"),
        }
    for arg in args:
        if not arg.startswith("@"):
            continue
        username = arg[1:].lower()
        row = db.execute(
            "SELECT user_id,username,first_name FROM known_users WHERE chat_id=? AND username=?",
            (chat_id, username),
        ).fetchone()
        if row:
            return {"id": int(row["user_id"]), "username": row["username"], "first_name": row["first_name"]}
    return None


def _display_user(db: sqlite3.Connection, chat_id: int, user_id: int) -> str:
    user = _known_user(db, chat_id, user_id)
    if not user:
        return "котик"
    if user.get("username"):
        return f"@{user['username']}"
    return str(user.get("first_name") or "котик")


def _active_relationship(db: sqlite3.Connection, chat_id: int, user_id: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM relationships WHERE chat_id=? AND status='active' AND (user_a=? OR user_b=?) "
        "ORDER BY created_on DESC LIMIT 1",
        (chat_id, user_id, user_id),
    ).fetchone()


def _partner_id(relation: sqlite3.Row, user_id: int) -> int:
    return int(relation["user_b"] if int(relation["user_a"]) == user_id else relation["user_a"])


def _active_marriage(db: sqlite3.Connection, chat_id: int, user_id: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM marriages WHERE chat_id=? AND status='married' AND (user_a=? OR user_b=?) "
        "ORDER BY married_on DESC LIMIT 1",
        (chat_id, user_id, user_id),
    ).fetchone()


def _family_kittens(db: sqlite3.Connection, chat_id: int, user_id: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM mini_kittens WHERE chat_id=? AND (user_a=? OR user_b=?) ORDER BY kitten_id",
        (chat_id, user_id, user_id),
    ).fetchall()


def _pair_kittens(
    db: sqlite3.Connection, chat_id: int, user_a: int, user_b: int
) -> list[sqlite3.Row]:
    user_a, user_b = _pair(user_a, user_b)
    return db.execute(
        "SELECT * FROM mini_kittens WHERE chat_id=? AND user_a=? AND user_b=? ORDER BY kitten_id",
        (chat_id, user_a, user_b),
    ).fetchall()


def _kitten_ability(rarity: str) -> str:
    return KITTEN_ABILITIES.get(rarity, "без особого бонуса")


def task_bonus_for_cat(db: sqlite3.Connection, cat: sqlite3.Row) -> tuple[int, int]:
    row = db.execute(
        "SELECT k.rarity FROM active_kittens a JOIN mini_kittens k ON k.kitten_id=a.kitten_id "
        "WHERE a.chat_id=? AND a.user_id=? AND k.status='active' LIMIT 1",
        (cat["chat_id"], cat["user_id"]),
    ).fetchone()
    return (1 if row and row["rarity"] == "rare" else 0,
            1 if row and row["rarity"] == "superrare" else 0)


def mood_bonus_for_cat(db: sqlite3.Connection, cat: sqlite3.Row) -> int:
    row = db.execute(
        "SELECT k.rarity FROM active_kittens a JOIN mini_kittens k ON k.kitten_id=a.kitten_id "
        "WHERE a.chat_id=? AND a.user_id=? AND k.status='active' LIMIT 1",
        (cat["chat_id"], cat["user_id"]),
    ).fetchone()
    return 1 if row and row["rarity"] == "medium" else 0


def _maybe_roll_kitten(
    db: sqlite3.Connection, chat_id: int, user_id: int, partner_id: int, send: GameSender
) -> None:
    marriage = _active_marriage(db, chat_id, user_id)
    if not marriage or _partner_id(marriage, user_id) != partner_id:
        return
    day = game._today()
    reciprocal = db.execute(
        "SELECT COUNT(*) FROM relationship_actions WHERE chat_id=? AND day=? AND action='kiss' "
        "AND ((user_id=? AND target_id=?) OR (user_id=? AND target_id=?))",
        (chat_id, day.isoformat(), user_id, partner_id, partner_id, user_id),
    ).fetchone()[0]
    if int(reciprocal) < 2:
        return
    kittens = int(db.execute(
        "SELECT COUNT(*) FROM mini_kittens WHERE chat_id=? AND user_a=? AND user_b=?",
        (chat_id, marriage["user_a"], marriage["user_b"]),
    ).fetchone()[0])
    if kittens >= 3:
        send(chat_id, "🐾 У вашей семьи уже есть три мини-котёнка — больше мест пока нет.")
        return
    married_on = date.fromisoformat(marriage["married_on"])
    week_index = max(0, (day - married_on).days // 7)
    cursor = db.execute(
        "INSERT OR IGNORE INTO marriage_weekly_rolls(chat_id,user_a,user_b,week_index) VALUES(?,?,?,?)",
        (chat_id, marriage["user_a"], marriage["user_b"], week_index),
    )
    if not cursor.rowcount:
        return
    if random.random() >= KITTEN_CHANCE:
        db.commit()
        send(chat_id, "🐾 На этой неделе мини-котёнок пока не появился. Попробуйте снова на следующей неделе.")
        return
    rarity = random.choices(
        [item[0] for item in KITTEN_RARITIES], weights=[item[1] for item in KITTEN_RARITIES], k=1
    )[0]
    db.execute(
        "INSERT INTO mini_kittens(chat_id,user_a,user_b,rarity,status,created_on) VALUES(?,?,?,?,?,?)",
        (chat_id, marriage["user_a"], marriage["user_b"], rarity, "pending", day.isoformat()),
    )
    db.commit()
    send(
        chat_id,
        f"🎉 В семье появился мини-котёнок редкости «{KITTEN_RARITY_NAMES[rarity]}»! "
        "Дайте ему имя командой /namekitten Имя — тогда он появится в /family.",
    )


def _marriage_command(
    db: sqlite3.Connection, message: dict[str, Any], command: str, args: list[str], send: GameSender
) -> None:
    chat_id = int(message["chat"]["id"])
    user_id = int(message.get("from", {}).get("id", 0))
    today = game._today()

    if command == "family":
        relation = _active_relationship(db, chat_id, user_id)
        marriage = _active_marriage(db, chat_id, user_id)
        partner_id: int | None = None
        if marriage:
            partner_id = _partner_id(marriage, user_id)
            status = f"💍 Вы в браке с {_display_user(db, chat_id, partner_id)} с {marriage['married_on']}."
        elif relation:
            partner_id = _partner_id(relation, user_id)
            days = max(0, (today - date.fromisoformat(relation["created_on"])).days)
            status = (
                f"💞 Вы в паре с {_display_user(db, chat_id, partner_id)} {days} дней. "
                f"Для предложения нужно {MARRIAGE_WAIT_DAYS} дней и доверие {MARRIAGE_TRUST_REQUIRED}/100."
            )
        else:
            status = "Ты пока не в паре. Начните с взаимных /kiss в разные дни."
        kittens = (
            _pair_kittens(db, chat_id, user_id, partner_id)
            if partner_id is not None
            else _family_kittens(db, chat_id, user_id)
        )
        active = db.execute(
            "SELECT kitten_id FROM active_kittens WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        count_text = f"Мини-котята в этой семье: {len(kittens)}/3" if partner_id is not None else f"Мини-котята в коллекции: {len(kittens)} (до 3 на пару)"
        lines = [f"🏠 Семья: {status}", count_text]
        for row in kittens:
            if row["status"] == "pending":
                lines.append(
                    f"• #{row['kitten_id']} ждёт имени — {KITTEN_RARITY_NAMES[row['rarity']].capitalize()}. "
                    "Завершить создание: /namekitten Имя"
                )
            else:
                selected = " · активен" if active and int(active["kitten_id"]) == int(row["kitten_id"]) else ""
                lines.append(
                    f"• #{row['kitten_id']} {row['name']} — {KITTEN_RARITY_NAMES[row['rarity']]}{selected}: "
                    f"{_kitten_ability(row['rarity'])}"
                )
        if not kittens:
            lines.append("Мини-котят пока нет.")
        send(chat_id, "\n".join(lines))
        return

    if command == "namekitten":
        kitten_id: int | None = int(args[0]) if args and args[0].isdigit() else None
        name_args = args[1:] if kitten_id is not None else args
        name = game._cat_name(name_args)
        if name is None:
            send(chat_id, "Назови мини-котёнка: /namekitten Имя. Если их несколько, укажи номер: /namekitten ID Имя.")
            return
        if kitten_id is None:
            pending = db.execute(
                "SELECT * FROM mini_kittens WHERE chat_id=? AND status='pending' AND (user_a=? OR user_b=?) "
                "ORDER BY kitten_id DESC LIMIT 1",
                (chat_id, user_id, user_id),
            ).fetchone()
        else:
            pending = db.execute(
                "SELECT * FROM mini_kittens WHERE chat_id=? AND kitten_id=? AND status='pending' AND (user_a=? OR user_b=?)",
                (chat_id, kitten_id, user_id, user_id),
            ).fetchone()
        if not pending:
            send(chat_id, "У вашей семьи нет мини-котёнка, который ждёт имени. Проверь /family.")
            return
        db.execute(
            "UPDATE mini_kittens SET name=?,status='active' WHERE kitten_id=?",
            (name, pending["kitten_id"]),
        )
        for parent_id in (int(pending["user_a"]), int(pending["user_b"])):
            db.execute(
                "INSERT OR IGNORE INTO active_kittens(chat_id,user_id,kitten_id) VALUES(?,?,?)",
                (chat_id, parent_id, pending["kitten_id"]),
            )
        db.commit()
        send(
            chat_id,
            f"🐱 Мини-котёнка назвали {name}! Редкость: {KITTEN_RARITY_NAMES[pending['rarity']]}. "
            f"Способность: {_kitten_ability(pending['rarity'])}. Выбрать активного: /activekitten {pending['kitten_id']}.",
        )
        return

    if command == "activekitten":
        if not args or not args[0].isdigit():
            send(chat_id, "Укажи номер мини-котёнка из /family, например /activekitten 2. Команда /activekitten 0 отключает бонус.")
            return
        kitten_id = int(args[0])
        if kitten_id == 0:
            db.execute("DELETE FROM active_kittens WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            db.commit()
            send(chat_id, "Бонус мини-котёнка отключён.")
            return
        kitten = db.execute(
            "SELECT * FROM mini_kittens WHERE chat_id=? AND kitten_id=? AND status='active' AND (user_a=? OR user_b=?)",
            (chat_id, kitten_id, user_id, user_id),
        ).fetchone()
        if not kitten:
            send(chat_id, "Не нашёл такого мини-котёнка в твоей семье. Посмотри список: /family.")
            return
        db.execute(
            "INSERT INTO active_kittens(chat_id,user_id,kitten_id) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET kitten_id=excluded.kitten_id",
            (chat_id, user_id, kitten_id),
        )
        db.commit()
        send(chat_id, f"✨ Теперь активный мини-котёнок — {kitten['name']}. Способность: {_kitten_ability(kitten['rarity'])}.")
        return

    if command == "marry":
        target = _target(db, chat_id, message, args)
        if not target:
            send(chat_id, "Предложение можно сделать партнёру: /marry @username или ответом на его сообщение.")
            return
        target_id = int(target["id"])
        relation = _active_relationship(db, chat_id, user_id)
        if not relation or _partner_id(relation, user_id) != target_id:
            send(chat_id, "Сначала нужно быть парой с этим участником.")
            return
        days = (today - date.fromisoformat(relation["created_on"])).days
        if days < MARRIAGE_WAIT_DAYS or int(relation["trust"]) < MARRIAGE_TRUST_REQUIRED:
            send(
                chat_id,
                f"Для предложения нужно быть парой минимум {MARRIAGE_WAIT_DAYS} дней и иметь доверие "
                f"{MARRIAGE_TRUST_REQUIRED}/100. Сейчас: {max(0, days)} дней, доверие {relation['trust']}.",
            )
            return
        if _active_marriage(db, chat_id, user_id) or _active_marriage(db, chat_id, target_id):
            send(chat_id, "Один из вас уже состоит в браке.")
            return
        proposer_cat = _current_cat(db, chat_id, user_id)
        if not proposer_cat or int(proposer_cat["stones"]) < MARRIAGE_COST:
            send(chat_id, f"На предложение и свадьбу нужно накопить {MARRIAGE_COST} камушков.")
            return
        user_a, user_b = _pair(user_id, target_id)
        old = db.execute(
            "SELECT status,proposed_on FROM marriages WHERE chat_id=? AND user_a=? AND user_b=?",
            (chat_id, user_a, user_b),
        ).fetchone()
        if (
            old and old["status"] == "proposed"
            and (today - date.fromisoformat(old["proposed_on"])).days <= 3
        ):
            send(chat_id, "Предложение этой паре уже отправлено. Дождитесь ответа или отмените его командой /cancelmarriage.")
            return
        db.execute(
            "INSERT INTO marriages(chat_id,user_a,user_b,status,proposed_by,proposed_on) VALUES(?,?,?,'proposed',?,?) "
            "ON CONFLICT(chat_id,user_a,user_b) DO UPDATE SET status='proposed',proposed_by=excluded.proposed_by,proposed_on=excluded.proposed_on,married_on=NULL,ended_on=NULL",
            (chat_id, user_a, user_b, user_id, today.isoformat()),
        )
        db.commit()
        send(chat_id, f"💍 {_display_user(db, chat_id, user_id)} сделал(а) предложение {_display_user(db, chat_id, target_id)}! Ответь /acceptmarriage или /declinemarriage. Свадьба стоит по {MARRIAGE_COST} камушков каждому.")
        return

    if command in {"acceptmarriage", "declinemarriage"}:
        proposal = db.execute(
            "SELECT * FROM marriages WHERE chat_id=? AND status='proposed' AND (user_a=? OR user_b=?) "
            "ORDER BY proposed_on DESC LIMIT 1",
            (chat_id, user_id, user_id),
        ).fetchone()
        if not proposal or int(proposal["proposed_by"]) == user_id:
            send(chat_id, "Для тебя нет ожидающего предложения руки и сердца.")
            return
        if command == "declinemarriage":
            db.execute(
                "UPDATE marriages SET status='declined',ended_on=? WHERE chat_id=? AND user_a=? AND user_b=?",
                (today.isoformat(), chat_id, proposal["user_a"], proposal["user_b"]),
            )
            db.commit()
            send(chat_id, "Предложение отклонено. Отношения продолжаются.")
            return
        if (today - date.fromisoformat(proposal["proposed_on"])).days > 3:
            db.execute(
                "UPDATE marriages SET status='expired' WHERE chat_id=? AND user_a=? AND user_b=?",
                (chat_id, proposal["user_a"], proposal["user_b"]),
            )
            db.commit()
            send(chat_id, "Предложение уже истекло. Партнёр может сделать его снова.")
            return
        proposer = int(proposal["proposed_by"])
        relation = _active_relationship(db, chat_id, user_id)
        proposer_cat = _current_cat(db, chat_id, proposer)
        acceptor_cat = _current_cat(db, chat_id, user_id)
        if (
            not relation
            or _partner_id(relation, user_id) != proposer
            or (today - date.fromisoformat(relation["created_on"])).days < MARRIAGE_WAIT_DAYS
            or int(relation["trust"]) < MARRIAGE_TRUST_REQUIRED
        ):
            send(chat_id, "Условия предложения больше не выполнены. Проверьте статус пары через /family.")
            return
        if (
            not proposer_cat or not acceptor_cat
            or int(proposer_cat["stones"]) < MARRIAGE_COST
            or int(acceptor_cat["stones"]) < MARRIAGE_COST
        ):
            send(chat_id, f"Для свадьбы каждому нужно {MARRIAGE_COST} камушков. Пока предложение остаётся открытым.")
            return
        db.execute(
            "UPDATE cats SET stones=stones-? WHERE cat_id IN (?,?)",
            (MARRIAGE_COST, proposer_cat["cat_id"], acceptor_cat["cat_id"]),
        )
        db.execute(
            "UPDATE marriages SET status='married',married_on=?,ended_on=NULL WHERE chat_id=? AND user_a=? AND user_b=?",
            (today.isoformat(), chat_id, proposal["user_a"], proposal["user_b"]),
        )
        db.execute(
            "DELETE FROM marriage_weekly_rolls WHERE chat_id=? AND user_a=? AND user_b=?",
            (chat_id, proposal["user_a"], proposal["user_b"]),
        )
        db.commit()
        send(chat_id, f"💍🎉 {_display_user(db, chat_id, proposal['user_a'])} и {_display_user(db, chat_id, proposal['user_b'])} поженились! О семье: /family.")
        return

    if command == "cancelmarriage":
        proposal = db.execute(
            "SELECT * FROM marriages WHERE chat_id=? AND status='proposed' AND proposed_by=? ORDER BY proposed_on DESC LIMIT 1",
            (chat_id, user_id),
        ).fetchone()
        if not proposal:
            send(chat_id, "У тебя нет отправленного предложения, которое можно отменить.")
            return
        db.execute(
            "UPDATE marriages SET status='cancelled',ended_on=? WHERE chat_id=? AND user_a=? AND user_b=?",
            (today.isoformat(), chat_id, proposal["user_a"], proposal["user_b"]),
        )
        db.commit()
        send(chat_id, "Предложение отменено.")
        return

    if command == "divorce":
        marriage = _active_marriage(db, chat_id, user_id)
        if not marriage:
            send(chat_id, "Ты сейчас не состоишь в браке.")
            return
        db.execute(
            "UPDATE marriages SET status='divorced',ended_on=? WHERE chat_id=? AND user_a=? AND user_b=?",
            (today.isoformat(), chat_id, marriage["user_a"], marriage["user_b"]),
        )
        db.execute(
            "UPDATE relationships SET status='ended',ended_on=? WHERE chat_id=? AND user_a=? AND user_b=?",
            (today.isoformat(), chat_id, marriage["user_a"], marriage["user_b"]),
        )
        db.execute(
            "DELETE FROM relationship_progress WHERE chat_id=? AND user_a=? AND user_b=?",
            (chat_id, marriage["user_a"], marriage["user_b"]),
        )
        db.commit()
        send(chat_id, "💔 Вы развелись. Мини-котята остаются в семейном альбоме обоих родителей.")


def _ensure_pair_cats(
    db: sqlite3.Connection, chat_id: int, user_a: int, user_b: int, send: GameSender
) -> bool:
    missing: list[int] = []
    for user_id in (user_a, user_b):
        cat = _cat_for(db, chat_id, user_id)
        if not cat:
            missing.append(user_id)
    if missing:
        if user_a in missing:
            send(chat_id, "Сначала заведи своего котика командой /cat Имя.")
        else:
            send(chat_id, f"У {_display_user(db, chat_id, user_b)} пока нет котика. Ему нужно выполнить /cat Имя.")
        return False
    return True


def _relationship_command(
    db: sqlite3.Connection, message: dict[str, Any], command: str, args: list[str], send: GameSender
) -> None:
    chat_id = int(message["chat"]["id"])
    user_id = int(message.get("from", {}).get("id", 0))
    relation = _active_relationship(db, chat_id, user_id)
    if command == "breakup":
        if not relation:
            send(chat_id, "Сейчас ты ни с кем не в паре.")
            return
        if _active_marriage(db, chat_id, user_id):
            send(chat_id, "Вы в браке. Для расставания используйте /divorce.")
            return
        partner = _partner_id(relation, user_id)
        requested = _target(db, chat_id, message, args)
        if requested and int(requested["id"]) != partner:
            send(chat_id, "Укажи текущего партнёра ответом на его сообщение или оставь команду без цели.")
            return
        db.execute(
            "UPDATE relationships SET status='ended',ended_on=? WHERE chat_id=? AND user_a=? AND user_b=?",
            (game._today().isoformat(), chat_id, relation["user_a"], relation["user_b"]),
        )
        db.execute(
            "DELETE FROM relationship_progress WHERE chat_id=? AND user_a=? AND user_b=?",
            (chat_id, relation["user_a"], relation["user_b"]),
        )
        db.commit()
        send(
            chat_id,
            f"💔 {_display_user(db, chat_id, user_id)} и {_display_user(db, chat_id, partner)} решили расстаться. "
            "Каждый может начать новые отношения позже.",
        )
        return

    if command in {"relationship", "jealous"}:
        if relation:
            partner = _partner_id(relation, user_id)
            trust = int(relation["trust"])
            feeling = "Партнёр немного ревнует." if trust < 75 else "В паре всё спокойно."
            if command == "jealous":
                send(chat_id, f"💚 Доверие с {_display_user(db, chat_id, partner)}: {trust}/100. {feeling}")
            else:
                send(chat_id, f"💞 Ты в паре с {_display_user(db, chat_id, partner)}. Доверие: {trust}/100. {feeling}")
            return
        rows = db.execute(
            "SELECT user_a,user_b,COUNT(*) AS days FROM relationship_progress "
            "WHERE chat_id=? AND (user_a=? OR user_b=?) AND a_action=1 AND b_action=1 "
            "GROUP BY user_a,user_b ORDER BY days DESC LIMIT 5",
            (chat_id, user_id, user_id),
        ).fetchall()
        if not rows:
            send(chat_id, "Ты пока не в паре. Для взаимной симпатии используйте /kiss @username в разные дни.")
            return
        progress = [
            f"{_display_user(db, chat_id, int(row['user_b'] if int(row['user_a']) == user_id else row['user_a']))}: "
            f"{min(RELATIONSHIP_DAYS, int(row['days']))}/{RELATIONSHIP_DAYS} взаимных дней"
            for row in rows
        ]
        send(chat_id, "💌 Путь к отношениям\n" + "\n".join(progress))
        return

    target = _target(db, chat_id, message, args)
    if not target:
        send(chat_id, "Ответь на сообщение участника или укажи @username, например /kiss @username.")
        return
    target_id = int(target["id"])
    if target_id == user_id:
        send(chat_id, "Себя целовать не нужно — найди котика для взаимной симпатии.")
        return
    if not _ensure_pair_cats(db, chat_id, user_id, target_id, send):
        return

    if command == "hug":
        if not relation or _partner_id(relation, user_id) != target_id:
            send(chat_id, "Обнимашки доступны партнёру. Для начала отношений используйте взаимные /kiss.")
            return
        day = game._today().isoformat()
        try:
            db.execute(
                "INSERT INTO relationship_actions(chat_id,user_id,target_id,day,action) VALUES(?,?,?,?,?)",
                (chat_id, user_id, target_id, day, "hug"),
            )
        except sqlite3.IntegrityError:
            send(chat_id, "Сегодня ты уже отправлял обнимашки этому котику.")
            return
        cat = _current_cat(db, chat_id, user_id)
        boost = 12 if cat and cat["personality"] == "Ласковый" else 10
        db.execute(
            "UPDATE relationships SET trust=MIN(100,trust+?) WHERE chat_id=? AND user_a=? AND user_b=?",
            (boost, chat_id, relation["user_a"], relation["user_b"]),
        )
        db.commit()
        send(chat_id, f"🤗 {_display_user(db, chat_id, user_id)} обнял(а) партнёра. Доверие выросло на {boost}.")
        return

    # A kiss outside an active couple builds reciprocal affection. If the sender
    # already has a partner, it counts as a breach of trust instead.
    relation = _active_relationship(db, chat_id, user_id)
    if relation:
        partner = _partner_id(relation, user_id)
        if partner == target_id:
            day = game._today().isoformat()
            try:
                db.execute(
                    "INSERT INTO relationship_actions(chat_id,user_id,target_id,day,action) VALUES(?,?,?,?,?)",
                    (chat_id, user_id, target_id, day, "kiss"),
                )
            except sqlite3.IntegrityError:
                send(chat_id, "Ты уже выражал чувства партнёру сегодня.")
                return
            db.execute(
                "UPDATE relationships SET trust=MIN(100,trust+5) WHERE chat_id=? AND user_a=? AND user_b=?",
                (chat_id, relation["user_a"], relation["user_b"]),
            )
            db.commit()
            send(chat_id, f"💞 {_display_user(db, chat_id, user_id)} отправил(а) партнёру воздушный поцелуй. Доверие +5.")
            _maybe_roll_kitten(db, chat_id, user_id, target_id, send)
            return
        day = game._today().isoformat()
        try:
            db.execute(
                "INSERT INTO relationship_actions(chat_id,user_id,target_id,day,action) VALUES(?,?,?,?,?)",
                (chat_id, user_id, target_id, day, "jealousy"),
            )
        except sqlite3.IntegrityError:
            send(chat_id, "Сегодня ревность уже разгоралась; повторно доверие не меняется.")
            return
        trust = max(0, int(relation["trust"]) - 25)
        status = "ended" if trust == 0 else "active"
        db.execute(
            "UPDATE relationships SET trust=?,status=?,ended_on=CASE WHEN ?='ended' THEN ? ELSE ended_on END "
            "WHERE chat_id=? AND user_a=? AND user_b=?",
            (trust, status, status, day, chat_id, relation["user_a"], relation["user_b"]),
        )
        if status == "ended":
            db.execute(
                "UPDATE marriages SET status='divorced',ended_on=? WHERE chat_id=? AND user_a=? AND user_b=? AND status='married'",
                (day, chat_id, relation["user_a"], relation["user_b"]),
            )
        db.commit()
        if status == "ended":
            send(chat_id, f"😿 Поцелуй на стороне разрушил доверие. {_display_user(db, chat_id, user_id)} и {_display_user(db, chat_id, partner)} расстались.")
        else:
            send(chat_id, f"😾 {_display_user(db, chat_id, partner)} ревнует: доверие снизилось до {trust}/100. /hug поможет помириться.")
        return

    target_relation = _active_relationship(db, chat_id, target_id)
    if target_relation:
        send(chat_id, f"У {_display_user(db, chat_id, target_id)} уже есть партнёр.")
        return

    user_a, user_b = _pair(user_id, target_id)
    day = game._today().isoformat()
    db.execute(
        "INSERT OR IGNORE INTO relationship_progress(chat_id,user_a,user_b,day) VALUES(?,?,?,?)",
        (chat_id, user_a, user_b, day),
    )
    col = "a_action" if user_id == user_a else "b_action"
    cursor = db.execute(
        f"UPDATE relationship_progress SET {col}=1 WHERE chat_id=? AND user_a=? AND user_b=? AND day=? AND {col}=0",
        (chat_id, user_a, user_b, day),
    )
    if not cursor.rowcount:
        send(chat_id, "Сегодня ты уже выражал(а) симпатию этому котику.")
        return
    db.commit()
    progress = db.execute(
        "SELECT COUNT(*) FROM relationship_progress WHERE chat_id=? AND user_a=? AND user_b=? "
        "AND a_action=1 AND b_action=1",
        (chat_id, user_a, user_b),
    ).fetchone()[0]
    this_day = db.execute(
        "SELECT a_action,b_action FROM relationship_progress WHERE chat_id=? AND user_a=? AND user_b=? AND day=?",
        (chat_id, user_a, user_b, day),
    ).fetchone()
    if int(progress) >= RELATIONSHIP_DAYS:
        if _active_relationship(db, chat_id, user_id) or _active_relationship(db, chat_id, target_id):
            send(chat_id, "Сначала нужно завершить текущие отношения.")
            return
        db.execute(
            "INSERT INTO relationships(chat_id,user_a,user_b,status,trust,created_on) VALUES(?,?,?,'active',100,?) "
            "ON CONFLICT(chat_id,user_a,user_b) DO UPDATE SET status='active',trust=100,created_on=excluded.created_on,ended_on=NULL",
            (chat_id, user_a, user_b, day),
        )
        db.execute(
            "DELETE FROM relationship_progress WHERE chat_id=? AND user_a=? AND user_b=?",
            (chat_id, user_a, user_b),
        )
        db.commit()
        send(chat_id, f"💞 {_display_user(db, chat_id, user_a)} и {_display_user(db, chat_id, user_b)} стали парой! Команда /relationship покажет статус.")
    elif this_day["a_action"] and this_day["b_action"]:
        send(chat_id, f"💞 Взаимная симпатия! Уже {progress}/{RELATIONSHIP_DAYS} дней. Продолжайте в разные дни, чтобы стать парой.")
    else:
        send(chat_id, f"💌 Симпатия отправлена {_display_user(db, chat_id, target_id)}. Для пары нужны взаимные действия в разные дни: {progress}/{RELATIONSHIP_DAYS} взаимных дней.")


def _item_key(name: str) -> str | None:
    return ITEM_ALIASES.get(name.casefold())


def _item_count(db: sqlite3.Connection, cat_id: int, item_key: str) -> int:
    row = db.execute(
        "SELECT quantity FROM cat_items WHERE cat_id=? AND item_key=?",
        (cat_id, item_key),
    ).fetchone()
    return int(row["quantity"]) if row else 0


def _item_command(
    db: sqlite3.Connection, message: dict[str, Any], command: str, args: list[str], send: GameSender
) -> None:
    chat_id = int(message["chat"]["id"])
    user_id = int(message.get("from", {}).get("id", 0))
    cat, _, notices = game._prepare_cat(db, chat_id, user_id)
    game._send_notices(send, chat_id, notices)
    if command == "shop":
        lines = ["🛒 Магазин котика"]
        for key, (title, cost, detail) in ITEMS.items():
            lines.append(f"/buy {key} — {title}, {cost} камушков ({detail})")
        send(chat_id, "\n".join(lines))
        return
    if not cat:
        send(chat_id, "Сначала заведи котика командой /cat Имя.")
        return
    if command == "items":
        rows = db.execute(
            "SELECT item_key,quantity FROM cat_items WHERE cat_id=? AND quantity>0 ORDER BY item_key",
            (cat["cat_id"],),
        ).fetchall()
        lines = [f"🎒 Вещи {cat['name']}:"]
        if rows:
            lines.extend(f"• {ITEMS[row['item_key']][0]} × {row['quantity']}" for row in rows if row["item_key"] in ITEMS)
        else:
            lines.append("Пока пусто.")
        if _item_count(db, int(cat["cat_id"]), "feeder"):
            lines.append(f"Автокормушка загружена: {cat['feeder_food']}/3 рыбок.")
        send(chat_id, "\n".join(lines))
        return
    if not args:
        send(chat_id, f"Укажи предмет. Посмотреть ассортимент: /shop. Команды: /buy и /use.")
        return
    key = _item_key(args[0])
    if not key:
        send(chat_id, "Не знаю такой предмет. Посмотреть ассортимент: /shop.")
        return
    if command == "buy":
        title, cost, _ = ITEMS[key]
        if key == "feeder" and _item_count(db, int(cat["cat_id"]), key):
            send(chat_id, "У котика уже есть автокормушка. Загрузи её командой /use feeder.")
            return
        if int(cat["stones"]) < cost:
            send(chat_id, f"{title} стоит {cost} камушков, сейчас у котика {cat['stones']}.")
            return
        db.execute("UPDATE cats SET stones=stones-? WHERE cat_id=?", (cost, cat["cat_id"]))
        db.execute(
            "INSERT INTO cat_items(cat_id,item_key,quantity) VALUES(?,?,1) "
            "ON CONFLICT(cat_id,item_key) DO UPDATE SET quantity=quantity+1",
            (cat["cat_id"], key),
        )
        db.commit()
        send(chat_id, f"🛍️ Куплено: {title}. Потрачено {cost} камушков.")
        return

    quantity = _item_count(db, int(cat["cat_id"]), key)
    if quantity < 1:
        send(chat_id, f"У котика нет этого предмета. Посмотреть ассортимент: /shop.")
        return
    if key == "feeder":
        room = max(0, 3 - int(cat["feeder_food"]))
        load = min(room, int(cat["fish"]))
        if load < 1:
            send(chat_id, "Для автокормушки нужна рыбка, а свободно в ней не больше 3 порций.")
            return
        db.execute(
            "UPDATE cats SET fish=fish-?,feeder_food=feeder_food+? WHERE cat_id=?",
            (load, load, cat["cat_id"]),
        )
        db.commit()
        send(chat_id, f"🤖 Автокормушка загружена на {load} дня(дней). Она покормит котика, если ты забудешь.")
        return
    if key == "toy":
        max_mood = game._max_mood(cat)
        mood_added = min(3 if cat["personality"] == "Игривый" else 2, max_mood - int(cat["mood"]))
        if mood_added < 1:
            send(chat_id, f"У {cat['name']} и так отличное настроение.")
            return
        _consume_item(db, int(cat["cat_id"]), key)
        db.execute("UPDATE cats SET mood=mood+? WHERE cat_id=?", (mood_added, cat["cat_id"]))
        db.commit()
        send(chat_id, f"🐭 {cat['name']} поиграл с мышкой. Настроение +{mood_added}.")
        return
    if key == "wand":
        target = _target(db, chat_id, message, args[1:])
        relation = _active_relationship(db, chat_id, user_id)
        if not relation:
            send(chat_id, "Палочку нежности можно использовать только на своего партнёра.")
            return
        partner = _partner_id(relation, user_id)
        if target and int(target["id"]) != partner:
            send(chat_id, "Палочка нежности предназначена твоему партнёру.")
            return
        trust = int(relation["trust"])
        if trust >= 100:
            send(chat_id, "Доверие уже на максимуме, палочка пока не нужна.")
            return
        boost = min(15, 100 - trust)
        _consume_item(db, int(cat["cat_id"]), key)
        db.execute(
            "UPDATE relationships SET trust=MIN(100,trust+15) WHERE chat_id=? AND user_a=? AND user_b=?",
            (chat_id, relation["user_a"], relation["user_b"]),
        )
        db.commit()
        send(chat_id, f"✨ Палочка нежности помогла помириться. Доверие с {_display_user(db, chat_id, partner)} выросло на {boost}.")


def _consume_item(db: sqlite3.Connection, cat_id: int, item_key: str) -> None:
    db.execute(
        "UPDATE cat_items SET quantity=quantity-1 WHERE cat_id=? AND item_key=? AND quantity>0",
        (cat_id, item_key),
    )


def _has_open_game(db: sqlite3.Connection, chat_id: int, user_id: int) -> bool:
    ttt = db.execute(
        "SELECT 1 FROM ttt_games WHERE chat_id=? AND state IN ('pending','active') AND (player_x=? OR player_o=?) LIMIT 1",
        (chat_id, user_id, user_id),
    ).fetchone()
    race = db.execute(
        "SELECT 1 FROM race_games WHERE chat_id=? AND state='pending' AND (player_a=? OR player_b=?) LIMIT 1",
        (chat_id, user_id, user_id),
    ).fetchone()
    return bool(ttt or race)


def _game_for_user(db: sqlite3.Connection, chat_id: int, user_id: int, state: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM ttt_games WHERE chat_id=? AND state=? AND (player_x=? OR player_o=?) "
        "ORDER BY game_id DESC LIMIT 1",
        (chat_id, state, user_id, user_id),
    ).fetchone()


def _board_text(board: str) -> str:
    cells = [mark if mark != "." else str(index + 1) for index, mark in enumerate(board)]
    return "\n".join((" | ".join(cells[0:3]), "---------", " | ".join(cells[3:6]), "---------", " | ".join(cells[6:9])))


def _winner(board: str) -> str | None:
    lines = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
    for a, b, c in lines:
        if board[a] != "." and board[a] == board[b] == board[c]:
            return board[a]
    return None


def _settle_game(db: sqlite3.Connection, row: sqlite3.Row, winner_id: int | None, send: GameSender, reason: str = "") -> None:
    chat_id = int(row["chat_id"])
    stake = int(row["stake"])
    if winner_id is None:
        for user_id in (int(row["player_x"]), int(row["player_o"])):
            cat = _current_cat(db, chat_id, user_id)
            if cat:
                db.execute("UPDATE cats SET stones=stones+? WHERE cat_id=?", (stake, cat["cat_id"]))
        text = f"🤝 Ничья! Камушки возвращены игрокам: по {stake} каждому."
    else:
        winner_cat = _current_cat(db, chat_id, winner_id)
        if winner_cat:
            db.execute("UPDATE cats SET stones=stones+? WHERE cat_id=?", (stake * 2, winner_cat["cat_id"]))
        loser_id = int(row["player_o"] if winner_id == int(row["player_x"]) else row["player_x"])
        text = f"🏆 {_display_user(db, chat_id, winner_id)} выиграл(а) {stake * 2} камушков у {_display_user(db, chat_id, loser_id)}!"
    db.execute("UPDATE ttt_games SET state='finished',turn_user=NULL WHERE game_id=?", (row["game_id"],))
    db.commit()
    quest_messages: list[str] = []
    for player_id in (int(row["player_x"]), int(row["player_o"])):
        cat = _current_cat(db, chat_id, player_id)
        if cat and record_weekly_event(db, cat, "games", winner=winner_id == player_id):
            quest_messages.append(f"📅 Недельное задание {cat['name']} выполнено! Награда добавлена.")
    if reason:
        text += f" {reason}"
    if quest_messages:
        text += "\n" + "\n".join(quest_messages)
    send(chat_id, text)


def _ttt_command(
    db: sqlite3.Connection, message: dict[str, Any], command: str, args: list[str], send: GameSender
) -> None:
    chat_id = int(message["chat"]["id"])
    user_id = int(message.get("from", {}).get("id", 0))
    if command == "ttt":
        target = _target(db, chat_id, message, args)
        if not target:
            send(chat_id, "Ответь на сообщение или укажи цель и ставку: /ttt @username 10.")
            return
        target_id = int(target["id"])
        if target_id == user_id:
            send(chat_id, "Найди соперника для крестиков-ноликов.")
            return
        stake = next((int(arg) for arg in args if arg.isdigit()), None)
        if stake is None or stake < 1 or stake > MAX_STAKE:
            send(chat_id, f"Укажи ставку от 1 до {MAX_STAKE} камушков: /ttt @username 10.")
            return
        if not _ensure_pair_cats(db, chat_id, user_id, target_id, send):
            return
        if _has_open_game(db, chat_id, user_id) or _has_open_game(db, chat_id, target_id):
            send(chat_id, "Один из игроков уже ждёт приглашение или играет в другую партию.")
            return
        cats = (_current_cat(db, chat_id, user_id), _current_cat(db, chat_id, target_id))
        if any(not cat or int(cat["stones"]) < stake for cat in cats):
            send(chat_id, "У обоих игроков должно быть достаточно камушков для ставки.")
            return
        db.execute(
            "INSERT INTO ttt_games(chat_id,player_x,player_o,stake,state,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, user_id, target_id, stake, "pending", datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
        send(chat_id, f"❎⭕ {_display_user(db, chat_id, user_id)} вызывает {_display_user(db, chat_id, target_id)} на крестики-нолики. Ставка: {stake} камушков с каждого. Ответь /accept или /decline.")
        return

    if command == "cancelgame":
        row = db.execute(
            "SELECT * FROM ttt_games WHERE chat_id=? AND state='pending' AND player_x=? ORDER BY game_id DESC LIMIT 1",
            (chat_id, user_id),
        ).fetchone()
        if row:
            db.execute("UPDATE ttt_games SET state='cancelled' WHERE game_id=?", (row["game_id"],))
            db.commit()
            send(chat_id, "Приглашение отменено. Камушки не списывались.")
            return
        race = db.execute(
            "SELECT * FROM race_games WHERE chat_id=? AND state='pending' AND player_a=? ORDER BY race_id DESC LIMIT 1",
            (chat_id, user_id),
        ).fetchone()
        if race:
            db.execute("UPDATE race_games SET state='cancelled' WHERE race_id=?", (race["race_id"],))
            db.commit()
            send(chat_id, "Вызов на котогонку отменён. Камушки не списывались.")
            return
        if not row:
            send(chat_id, "У тебя нет ожидающего приглашения, которое можно отменить.")
            return

    if command in {"accept", "decline"}:
        row = db.execute(
            "SELECT * FROM ttt_games WHERE chat_id=? AND state='pending' AND player_o=? ORDER BY game_id DESC LIMIT 1",
            (chat_id, user_id),
        ).fetchone()
        if not row:
            _race_invitation_reply(db, chat_id, user_id, command, send)
            return
        if command == "decline":
            db.execute("UPDATE ttt_games SET state='declined' WHERE game_id=?", (row["game_id"],))
            db.commit()
            send(chat_id, f"Приглашение от {_display_user(db, chat_id, int(row['player_x']))} отклонено. Камушки не списывались.")
            return
        stake = int(row["stake"])
        player_x = int(row["player_x"])
        cat_x = _current_cat(db, chat_id, player_x)
        cat_o = _current_cat(db, chat_id, user_id)
        if not cat_x or not cat_o or int(cat_x["stones"]) < stake or int(cat_o["stones"]) < stake:
            db.execute("UPDATE ttt_games SET state='declined' WHERE game_id=?", (row["game_id"],))
            db.commit()
            send(chat_id, "Приглашение закрыто: одному из игроков уже не хватает камушков на ставку.")
            return
        db.execute("UPDATE cats SET stones=stones-? WHERE cat_id IN (?,?)", (stake, cat_x["cat_id"], cat_o["cat_id"]))
        db.execute(
            "UPDATE ttt_games SET state='active',board='.........',turn_user=? WHERE game_id=?",
            (player_x, row["game_id"]),
        )
        db.commit()
        send(chat_id, f"🎮 Партия началась! {_display_user(db, chat_id, player_x)} — X, ты — O. Ход X. Чтобы поставить знак, напиши /move номер клетки.\n{_board_text('.........')}")
        return

    if command == "resign":
        row = _game_for_user(db, chat_id, user_id, "active")
        if not row:
            send(chat_id, "У тебя сейчас нет активной партии.")
            return
        winner_id = int(row["player_o"] if user_id == int(row["player_x"]) else row["player_x"])
        _settle_game(db, row, winner_id, send, "Соперник завершил партию досрочно.")
        return

    row = _game_for_user(db, chat_id, user_id, "active")
    if not row:
        send(chat_id, "У тебя сейчас нет активной партии.")
        return
    if int(row["turn_user"] or 0) != user_id:
        send(chat_id, "Сейчас ход соперника.")
        return
    if not args or not args[0].isdigit() or not 1 <= int(args[0]) <= 9:
        send(chat_id, "Укажи номер пустой клетки от 1 до 9: /move 5.")
        return
    index = int(args[0]) - 1
    board = str(row["board"])
    if board[index] != ".":
        send(chat_id, "Эта клетка уже занята. Выбери свободную.")
        return
    mark = "X" if user_id == int(row["player_x"]) else "O"
    board = board[:index] + mark + board[index + 1:]
    winner_mark = _winner(board)
    if winner_mark:
        winner_id = int(row["player_x"] if winner_mark == "X" else row["player_o"])
        db.execute("UPDATE ttt_games SET board=? WHERE game_id=?", (board, row["game_id"]))
        db.commit()
        send(chat_id, f"{_board_text(board)}")
        _settle_game(db, row, winner_id, send)
    elif "." not in board:
        db.execute("UPDATE ttt_games SET board=? WHERE game_id=?", (board, row["game_id"]))
        db.commit()
        send(chat_id, _board_text(board))
        _settle_game(db, row, None, send)
    else:
        next_user = int(row["player_o"] if user_id == int(row["player_x"]) else row["player_x"])
        db.execute("UPDATE ttt_games SET board=?,turn_user=? WHERE game_id=?", (board, next_user, row["game_id"]))
        db.commit()
        send(chat_id, f"{_board_text(board)}\nХод: {_display_user(db, chat_id, next_user)}.")


def _race_invitation_reply(
    db: sqlite3.Connection, chat_id: int, user_id: int, command: str, send: GameSender
) -> None:
    row = db.execute(
        "SELECT * FROM race_games WHERE chat_id=? AND state='pending' AND player_b=? ORDER BY race_id DESC LIMIT 1",
        (chat_id, user_id),
    ).fetchone()
    if not row:
        send(chat_id, "Для тебя нет ожидающего игрового приглашения.")
        return
    if command == "decline":
        db.execute("UPDATE race_games SET state='declined' WHERE race_id=?", (row["race_id"],))
        db.commit()
        send(chat_id, f"Вызов на котогонку от {_display_user(db, chat_id, int(row['player_a']))} отклонён. Камушки не списывались.")
        return
    stake = int(row["stake"])
    player_a = int(row["player_a"])
    player_b = int(row["player_b"])
    cat_a = _current_cat(db, chat_id, player_a)
    cat_b = _current_cat(db, chat_id, player_b)
    if not cat_a or not cat_b or int(cat_a["stones"]) < stake or int(cat_b["stones"]) < stake:
        db.execute("UPDATE race_games SET state='declined' WHERE race_id=?", (row["race_id"],))
        db.commit()
        send(chat_id, "Вызов закрыт: одному из игроков уже не хватает камушков на ставку.")
        return
    score_a = int(cat_a["agility"]) * 3 + int(cat_a["luck"]) + random.randint(1, 12) + (2 if cat_a["personality"] == "Шустрый" else 0)
    score_b = int(cat_b["agility"]) * 3 + int(cat_b["luck"]) + random.randint(1, 12) + (2 if cat_b["personality"] == "Шустрый" else 0)
    db.execute(
        "UPDATE cats SET stones=stones-? WHERE cat_id IN (?,?)",
        (stake, cat_a["cat_id"], cat_b["cat_id"]),
    )
    winner_id: int | None = None
    if score_a == score_b:
        db.execute("UPDATE cats SET stones=stones+? WHERE cat_id IN (?,?)", (stake, cat_a["cat_id"], cat_b["cat_id"]))
        result = f"🤝 Ничья в котогонке! Камушки возвращены. Счёт: {score_a}:{score_b}."
    else:
        winner_id = player_a if score_a > score_b else player_b
        winner_cat_id = cat_a["cat_id"] if score_a > score_b else cat_b["cat_id"]
        db.execute("UPDATE cats SET stones=stones+? WHERE cat_id=?", (stake * 2, winner_cat_id))
        result = (
            f"🏁 {_display_user(db, chat_id, winner_id)} выиграл(а) котогонку и получает "
            f"{stake * 2} камушков! Счёт: {score_a}:{score_b}."
        )
    db.execute("UPDATE race_games SET state='finished' WHERE race_id=?", (row["race_id"],))
    db.commit()
    quest_messages: list[str] = []
    for player_id in (player_a, player_b):
        participant_cat = _current_cat(db, chat_id, player_id)
        if participant_cat and record_weekly_event(db, participant_cat, "games", winner=winner_id == player_id):
            quest_messages.append(f"📅 Недельное задание {participant_cat['name']} выполнено! Награда добавлена.")
    if quest_messages:
        result += "\n" + "\n".join(quest_messages)
    send(chat_id, result)


def _race_command(
    db: sqlite3.Connection, message: dict[str, Any], args: list[str], send: GameSender
) -> None:
    chat_id = int(message["chat"]["id"])
    user_id = int(message.get("from", {}).get("id", 0))
    target = _target(db, chat_id, message, args)
    if not target:
        send(chat_id, "Ответь на сообщение или укажи участника и ставку: /race @username 10.")
        return
    target_id = int(target["id"])
    if target_id == user_id:
        send(chat_id, "Для котогонки нужен соперник.")
        return
    stake = next((int(arg) for arg in args if arg.isdigit()), None)
    if stake is None or not 1 <= stake <= MAX_STAKE:
        send(chat_id, f"Укажи ставку от 1 до {MAX_STAKE} камушков: /race @username 10.")
        return
    if not _ensure_pair_cats(db, chat_id, user_id, target_id, send):
        return
    if _has_open_game(db, chat_id, user_id) or _has_open_game(db, chat_id, target_id):
        send(chat_id, "Один из игроков уже ждёт приглашение или играет в другую партию.")
        return
    cats = (_current_cat(db, chat_id, user_id), _current_cat(db, chat_id, target_id))
    if any(not cat or int(cat["stones"]) < stake for cat in cats):
        send(chat_id, "У обоих игроков должно быть достаточно камушков для ставки.")
        return
    db.execute(
        "INSERT INTO race_games(chat_id,player_a,player_b,stake,state,created_at) VALUES(?,?,?,?,?,?)",
        (chat_id, user_id, target_id, stake, "pending", datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    send(chat_id, f"🏁 {_display_user(db, chat_id, user_id)} вызывает {_display_user(db, chat_id, target_id)} на котогонку! Ставка — {stake} камушков с каждого. Ответь /accept или /decline.")


def handle_command(
    db: sqlite3.Connection,
    message: dict[str, Any],
    command: str,
    args: list[str],
    send: GameSender,
) -> bool:
    if command in {"weekly", "leaderboard", "quiz", "answer"}:
        chat_id = int(message["chat"]["id"])
        user_id = int(message.get("from", {}).get("id", 0))
        if command == "weekly":
            send(chat_id, weekly_status(db, chat_id, user_id))
        elif command == "leaderboard":
            send(chat_id, weekly_leaderboard(db, chat_id))
        else:
            _quiz_command(db, chat_id, user_id, command, args, send)
    elif command in {
        "family", "marry", "acceptmarriage", "declinemarriage", "cancelmarriage",
        "divorce", "namekitten", "activekitten",
    }:
        _marriage_command(db, message, command, args, send)
    elif command in {"kiss", "hug", "relationship", "jealous", "breakup"}:
        _relationship_command(db, message, command, args, send)
    elif command in {"shop", "buy", "items", "use"}:
        _item_command(db, message, command, args, send)
    elif command == "race":
        _race_command(db, message, args, send)
    elif command in {"ttt", "accept", "decline", "cancelgame", "move", "resign"}:
        _ttt_command(db, message, command, args, send)
    else:
        return False
    return True
