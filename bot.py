import os
import sys
from typing import Dict, Optional

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# === ЗАГРУЗКА .ENV ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")  # уже не обязателен для Responses API, но оставим на будущее

def check_env_var(name: str, value: Optional[str], min_len=10) -> bool:
    if not value:
        print(f"[ENV ERROR] {name} не найден. Проверь .env", file=sys.stderr)
        return False
    if len(value.strip()) < min_len:
        print(f"[ENV WARNING] {name} слишком короткий: {repr(value)}", file=sys.stderr)
    return True

if not (
    check_env_var("BOT_TOKEN", BOT_TOKEN, 20)
    and check_env_var("OPENAI_API_KEY", OPENAI_API_KEY, 20)
):
    sys.exit("Остановлено: нет корректных переменных окружения.")

# === OpenAI клиент ============================================================
client = OpenAI(api_key=OPENAI_API_KEY)

# === СОСТОЯНИЕ ОНБОРДИНГА =====================================================
user_state: Dict[int, str] = {}
user_data: Dict[int, dict] = {}

# === ФУНКЦИИ GPT ==============================================================
def ask_gpt(prompt: str) -> str:
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt
        )
        return response.output[0].content[0].text
    except Exception as e:
        print(f"[OpenAI ERROR] {e}", file=sys.stderr)
        return "Ошибка при обращении к GPT."

# === ОБРАБОТЧИКИ ==============================================================
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
        await update.message.reply_text(bot_text, reply_markup=markup)

    elif state == "AWAIT_PET_TYPE":
        user_data[chat_id] = {"pet_type": text}
        user_state[chat_id] = "AWAIT_PET_INFO"
        bot_text = "Расскажи о питомце:\n1. Имя:\n2. Порода:\n3. Возраст:\n4. Вес:\n5. Пол:"
        await update.message.reply_text(bot_text)

    elif state == "AWAIT_PET_INFO":
        user_data[chat_id]["pet_info"] = text
        user_state[chat_id] = "DONE"
        markup = ReplyKeyboardMarkup(
            [["Воспитание", "Дрессировка"], ["Игры", "Уход"]],
            resize_keyboard=True
        )
        bot_text = (
            "Отлично, всё готово. Можешь задать любой вопрос:\n\n"
            "Примеры:\n- Как приучить щенка к туалету?\n- Как научить собаку команде 'Сидеть'?\n Или выбери интересующую тебя тему"
        )
        await update.message.reply_text(bot_text, reply_markup=markup)

    elif state == "DONE":
        await update.message.reply_text("Секунду, думаю…")
        reply = ask_gpt(text)
        await update.message.reply_text(reply)

    else:
        bot_text = "Пожалуйста, нажми /start для начала."
        await update.message.reply_text(bot_text)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).read_timeout(60).write_timeout(60).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
