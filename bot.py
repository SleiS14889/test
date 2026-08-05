# -*- coding: utf-8 -*-
"""
Telegram-бот для проходження анкети MI-CAT(C)-v2
(Монреальський інструмент для тестування артриту у котів, версія власника).

Запуск:
    1. pip install -r requirements.txt
    2. Встановіть змінну середовища TELEGRAM_BOT_TOKEN (токен від @BotFather)
    3. python bot.py
"""

import logging
import os
import sqlite3
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from questions import (
    ALL_QUESTIONS,
    ANSWER_DK,
    ANSWER_LABELS,
    ANSWER_NO,
    ANSWER_YES,
    compute_score,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Стани діалогу ---
OWNER_NAME, CAT_NAME, ASKING, COMMENT = range(4)

DB_PATH = os.path.join(os.path.dirname(__file__), "mi_cat_results.db")


# ------------------------------------------------------------------
# База даних
# ------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER,
            owner_name TEXT,
            cat_name TEXT,
            date TEXT,
            a INTEGER,
            b INTEGER,
            c INTEGER,
            d INTEGER,
            total_score REAL,
            comment TEXT,
            raw_answers TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_result(user_id, owner_name, cat_name, scores, comment, raw_answers):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO results
            (telegram_user_id, owner_name, cat_name, date, a, b, c, d,
             total_score, comment, raw_answers)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            owner_name,
            cat_name,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            scores["A"],
            scores["B"],
            scores["C"],
            scores["D"],
            scores["total_score"],
            comment,
            raw_answers,
        ),
    )
    conn.commit()
    conn.close()


# ------------------------------------------------------------------
# Допоміжні функції
# ------------------------------------------------------------------
def answer_keyboard():
    buttons = [
        [
            InlineKeyboardButton(ANSWER_LABELS[ANSWER_YES], callback_data=ANSWER_YES),
            InlineKeyboardButton(ANSWER_LABELS[ANSWER_NO], callback_data=ANSWER_NO),
        ],
        [InlineKeyboardButton(ANSWER_LABELS[ANSWER_DK], callback_data=ANSWER_DK)],
    ]
    return InlineKeyboardMarkup(buttons)


def question_text(index):
    table_number, category, text = ALL_QUESTIONS[index]
    table_label = "Таблиця 1 (активність)" if table_number == 1 else "Таблиця 2 (обмеження)"
    return (
        f"Питання {index + 1} з {len(ALL_QUESTIONS)}\n"
        f"{table_label} · {category}\n\n"
        f"{text}"
    )


def interpretation(total_score):
    if total_score is None:
        return "Недостатньо даних для розрахунку балу."
    if total_score < 0.2:
        return "Ознаки артриту виражені мінімально або відсутні."
    if total_score < 0.4:
        return "Присутні деякі ознаки, варто обговорити з ветеринаром."
    if total_score < 0.6:
        return "Помірно виражені ознаки, рекомендується консультація ветеринара."
    return "Виражені ознаки, рекомендується якнайшвидше звернутися до ветеринара."


# ------------------------------------------------------------------
# Хендлери діалогу
# ------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Вітаю! Це бот на основі анкети MI-CAT(C)-v2 — "
        "Монреальського інструменту для тестування артриту у котів "
        "(версія для власників).\n\n"
        "Анкета допоможе оцінити, чи є у вашого кота ознаки, "
        "характерні для артриту. Вона НЕ замінює візит до ветеринара, "
        "а лише допомагає зібрати структуровану інформацію для нього.\n\n"
        "Оцінюйте поведінку кота такою, якою вона є ЗАРАЗ.\n\n"
        "Як вас звати? (ім'я власника)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return OWNER_NAME


async def owner_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner_name"] = update.message.text.strip()
    await update.message.reply_text("Дякую! А яка кличка вашого кота?")
    return CAT_NAME


async def cat_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cat_name"] = update.message.text.strip()
    context.user_data["answers"] = []
    context.user_data["q_index"] = 0

    await update.message.reply_text(
        f"Чудово! Зараз я задам {len(ALL_QUESTIONS)} питань про {context.user_data['cat_name']}.\n"
        "На кожне оберіть один з варіантів: Так / Ні / Не знаю.\n\n"
        "Почнемо:"
    )
    await update.message.reply_text(
        question_text(0), reply_markup=answer_keyboard()
    )
    return ASKING


async def answer_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    answer = query.data  # "yes" / "no" / "dk"
    q_index = context.user_data["q_index"]
    table_number, _, _ = ALL_QUESTIONS[q_index]
    context.user_data["answers"].append((table_number, answer))

    next_index = q_index + 1
    context.user_data["q_index"] = next_index

    if next_index >= len(ALL_QUESTIONS):
        # Всі питання пройдено -> порахувати бал
        scores = compute_score(context.user_data["answers"])
        context.user_data["scores"] = scores

        total = scores["total_score"]
        total_str = f"{total:.2f}" if total is not None else "н/д"

        summary = (
            "Дякую! Анкету завершено.\n\n"
            f"Кіт: {context.user_data['cat_name']}\n"
            f"Власник: {context.user_data['owner_name']}\n\n"
            f"Таблиця 1 — «Так»: {scores['A']}, «Ні»: {scores['B']}\n"
            f"Таблиця 2 — «Так»: {scores['C']}, «Ні»: {scores['D']}\n\n"
            f"Загальний бал (B+C)/(A+B+C+D) = {total_str}\n\n"
            f"{interpretation(total)}\n\n"
            "Це попередня оцінка, а не діагноз. Обов'язково покажіть "
            "результат ветеринару.\n\n"
            "Якщо хочете, залиште короткий коментар для ветеринара "
            "(або надішліть /skip, щоб пропустити):"
        )
        await query.edit_message_text(question_text(q_index))
        await query.message.reply_text(summary)
        return COMMENT

    await query.edit_message_text(question_text(q_index))
    await query.message.reply_text(
        question_text(next_index), reply_markup=answer_keyboard()
    )
    return ASKING


async def comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    await finish_and_save(update, context, comment)
    return ConversationHandler.END


async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await finish_and_save(update, context, "")
    return ConversationHandler.END


async def finish_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE, comment: str):
    user_id = update.effective_user.id
    scores = context.user_data["scores"]
    raw_answers = ";".join(
        f"{i+1}:{a}" for i, (_, a) in enumerate(context.user_data["answers"])
    )
    save_result(
        user_id,
        context.user_data["owner_name"],
        context.user_data["cat_name"],
        scores,
        comment,
        raw_answers,
    )
    await update.effective_message.reply_text(
        "Результат збережено. Дякую, що подбали про здоров'я свого кота! 🐾\n\n"
        "Щоб пройти анкету знову, надішліть /start.\n"
        "Щоб побачити історію проходжень — /history."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Анкету скасовано. Надішліть /start, щоб почати знову.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date, cat_name, a, b, c, d, total_score
        FROM results
        WHERE telegram_user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Історія порожня. Пройдіть анкету через /start.")
        return

    lines = ["Останні результати:\n"]
    for date, cat_name, a, b, c, d, total_score in rows:
        total_str = f"{total_score:.2f}" if total_score is not None else "н/д"
        lines.append(f"{date} · {cat_name} · бал: {total_str} (A={a}, B={b}, C={c}, D={d})")

    await update.message.reply_text("\n".join(lines))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команди:\n"
        "/start — почати проходження анкети MI-CAT(C)-v2\n"
        "/history — переглянути останні результати\n"
        "/cancel — скасувати поточне проходження\n"
        "/help — це повідомлення"
    )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не знайдено TELEGRAM_BOT_TOKEN. Встановіть змінну середовища з "
            "токеном, отриманим від @BotFather."
        )

    init_db()

    application = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            OWNER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, owner_name_received)],
            CAT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cat_name_received)],
            ASKING: [CallbackQueryHandler(answer_received)],
            COMMENT: [
                CommandHandler("skip", skip_comment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, comment_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("help", help_command))

    logger.info("Бот запущено. Очікування повідомлень...")
    application.run_polling()


if __name__ == "__main__":
    main()
