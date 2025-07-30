import os
import sys
import time
from typing import Dict, Optional

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# === .ENV =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

def check_env_var(name: str, value: Optional[str], min_len=10) -> bool:
    if not value:
        print(f"[ENV ERROR] {name} не найден", file=sys.stderr)
        return False
    if len(value.strip()) < min_len:
        print(f"[ENV WARNING] {name} слишком короткий: {repr(value)}", file=sys.stderr)
    return True

if not (
    check_env_var("BOT_TOKEN", BOT_TOKEN, 20)
    and check_env_var("OPENAI_API_KEY", OPENAI_API_KEY, 20)
    and check_env_var("ASSISTANT_ID", ASSISTANT_ID, 10)
):
    sys.exit("Нет нужных переменных окружения.")

# === OpenAI Client + Assistant ===============================================
client = OpenAI(api_key=OPENAI_API_KEY)

# Хранение thread_id для каждого пользователя
user_threads: Dict[int, str] = {}

def get_or_create_thread(chat_id: int) -> str:
    if chat_id not in user_threads:
        thread = client.beta.threads.create()
        user_threads[chat_id] = thread.id
    return user_threads[chat_id]

def get_gpt_reply(chat_id: int, user_message: str) -> str:
    try:
        thread_id = get_or_create_thread(chat_id)

        # Добавляем сообщение в thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message,
        )

        # Запускаем ассистента
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
        )

        # Ждём, пока завершится
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status in ["completed", "failed", "cancelled"]:
                break
            time.sleep(1)

        # Получаем последнее сообщение от ассистента
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for message in reversed(messages.data):
            if message.role == "assistant":
                return message.content[0].text.value.strip()

        return "Ассистент не прислал ответ."

    except Exception as e:
        print(f"[OpenAI ERROR] {e}", file=sys.stderr)
        return "Ошибка при обращении к Assistant API."

# === Telegram logic ==========================================================
user_state: Dict[int, str] = {}
user_data: Dict[int, dict] = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = "AWAIT_NEXT"

    markup = ReplyKeyboardMarkup([["Далее"]], resize_keyboard=True, one_time_keyboard=True)
    text = (
        "Привет! Я твой помощник по уходу за домашним питомцем.\n"
        "Помогу с уходом, дрессировками, играми и по любым вопросам.\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным."
    )
    await update.message.reply_text(text, reply_markup=markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id)

    if state == "AWAIT_NEXT" and text == "Далее":
        user_state[chat_id] = "AWAIT_PET_TYPE"
        markup = ReplyKeyboardMarkup([["Кошка", "Собака", "Оба"]], resize_keyboard=True, one_time_keyboard=True)
        bot_text = "Кто у тебя дома?"
        await update.message.reply_text(bot_text)
        return

    if state == "AWAIT_PET_TYPE":
        user_data[chat_id] = {"pet_type": text}
        user_state[chat_id] = "AWAIT_PET_INFO"
        bot_text = "Расскажи о питомце:\n1. Имя:\n2. Порода:\n3. Возраст:\n4. Вес:\n5. Пол:"
        await update.message.reply_text(bot_text)
        return

    if state == "AWAIT_PET_INFO":
        user_data[chat_id]["pet_info"] = text
        user_state[chat_id] = "DONE"
        markup = ReplyKeyboardMarkup(
            [["Воспитание", "Дрессировка"], ["Игры", "Уход"]],
            resize_keyboard=True
        )
        bot_text = (
            "Отлично, всё готово. Можешь задать любой вопрос:\n\n"
            "Примеры:\n- Как приучить щенка к туалету?\n- Как научить собаку команде 'Сидеть'?\n"
            "Или выбери интересующую тему ниже."
        )
        await update.message.reply_text(bot_text, reply_markup=markup)
        return

    if state == "DONE":
        await update.message.reply_text("Секунду, думаю…")
        reply = get_gpt_reply(chat_id, text)
        await update.message.reply_text(reply)
        return

    await update.message.reply_text("Пожалуйста, нажми /start для начала.")

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("[BOT] Polling started.")
    app.run_polling()

if __name__ == "__main__":
    main()
