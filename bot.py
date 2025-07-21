import os
import sys

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# === ЗАГРУЗКА .ENV ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def check_env_var(name, value, min_len=10):
    if not value:
        print(f"[ENV ERROR] {name} не найден. Проверь .env", file=sys.stderr)
        return False
    if len(value.strip()) < min_len:
        print(f"[ENV WARNING] {name} слишком короткий: {repr(value)}", file=sys.stderr)
    if value != value.strip():
        print(f"[ENV WARNING] {name} содержит лишние пробелы: {repr(value)}", file=sys.stderr)
    return True

if not (check_env_var("BOT_TOKEN", BOT_TOKEN, 20) and check_env_var("OPENAI_API_KEY", OPENAI_API_KEY, 20)):
    sys.exit("Остановлено: нет корректных переменных окружения.")

# === КОНФИГ OpenAI ===
MODEL_NAME = "gpt-4o-mini"
client = OpenAI(api_key=OPENAI_API_KEY)

# === СОСТОЯНИЕ ПОЛЬЗОВАТЕЛЕЙ ===
user_state = {}
user_data = {}

# === ОБРАБОТЧИКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_chat.id] = "AWAIT_NEXT"
    markup = ReplyKeyboardMarkup([["Далее"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Привет! Я твой помощник по уходу за домашним питомцем.\n"
        "Помогу с уходом, дрессировками, играми и по любым вопросам.\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным.",
        reply_markup=markup
    )

async def ask_chatgpt(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system",
                 "content": (
                     "Ты — заботливый и дружелюбный помощник по уходу за домашними животными. "
                     "Твоя задача — помогать владельцам собак и кошек с воспитанием, дрессировкой, уходом, "
                     "питанием, играми и здоровьем питомцев. "
                     "Если вопрос не связан с животными, вежливо отвечай: 'Я могу помочь только с вопросами о питомцах.'"
                 )},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[OpenAI ERROR] {e}", file=sys.stderr)
        return "Произошла ошибка при обращении к ИИ. Попробуй позже."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id)

    if state == "AWAIT_NEXT" and text == "Далее":
        user_state[chat_id] = "AWAIT_PET_TYPE"
        markup = ReplyKeyboardMarkup([["Кошка", "Собака", "Оба"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Кто у тебя дома?", reply_markup=markup)

    elif state == "AWAIT_PET_TYPE":
        user_data[chat_id] = {"pet_type": text}
        user_state[chat_id] = "AWAIT_PET_INFO"
        await update.message.reply_text(
            "Расскажи о питомце:\n1. Имя:\n2. Порода:\n3. Возраст:\n4. Вес:\n5. Пол:"
        )

    elif state == "AWAIT_PET_INFO":
        user_data[chat_id]["pet_info"] = text
        user_state[chat_id] = "AWAIT_HELP_AREA"
        markup = ReplyKeyboardMarkup([
            ["Уход и питание", "Поведение и здоровье"],
            ["Игры и досуг", "Путешествия с питомцем"],
            ["Дрессировка", "Напиши свой вариант"]
        ], resize_keyboard=True)
        await update.message.reply_text("В чём тебе важнее всего моя помощь?", reply_markup=markup)

    elif state == "AWAIT_HELP_AREA":
        user_data[chat_id]["help_area"] = text
        user_state[chat_id] = "AWAIT_REMINDER_SETUP"
        markup = ReplyKeyboardMarkup([["Настроить", "Пропустить"]], resize_keyboard=True)
        await update.message.reply_text(
            "Хочешь, чтобы я напоминал о:\n- Кормлении\n- Обработках\n- Прививках\n- Стрижке когтей\n- Тренировках",
            reply_markup=markup
        )

    elif state == "AWAIT_REMINDER_SETUP":
        user_state[chat_id] = "DONE"
        markup = ReplyKeyboardMarkup([["Воспитание", "Дрессировка", "Игры", "Уход"]], resize_keyboard=True)
        await update.message.reply_text(
            "Отлично, всё готово. Можешь задать любой вопрос:\n\n"
            "Примеры:\n- Как приучить щенка к туалету?\n- Как научить собаку команде 'Сидеть'?",
            reply_markup=markup
        )

    elif state == "DONE":
        await update.message.reply_text("Секунду, думаю…")
        reply = await ask_chatgpt(text)
        await update.message.reply_text(reply)

    else:
        await update.message.reply_text("Пожалуйста, нажми /start для начала.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
