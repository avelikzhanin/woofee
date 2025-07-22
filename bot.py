import os
import sys
from typing import Dict, List
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
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # Модель по умолчанию

def check_env_var(name: str, value: str, min_len=10) -> bool:
    if not value:
        print(f"[ENV ERROR] {name} не найден. Проверь .env", file=sys.stderr)
        return False
    if len(value.strip()) < min_len:
        print(f"[ENV WARNING] {name} слишком короткий: {repr(value)}", file=sys.stderr)
    return True

if not (check_env_var("BOT_TOKEN", BOT_TOKEN, 20) and check_env_var("OPENAI_API_KEY", OPENAI_API_KEY, 20)):
    sys.exit("Остановлено: нет корректных переменных окружения.")

# === OpenAI клиент ============================================================
client = OpenAI(api_key=OPENAI_API_KEY)

# === ИСТОРИЯ ЧАТОВ (контекст) =================================================
chat_history: Dict[int, List[Dict[str, str]]] = {}  # chat_id -> [{"role": "user"/"assistant", "content": "..."}]

def ensure_history(chat_id: int):
    if chat_id not in chat_history:
        chat_history[chat_id] = []

def build_input_from_history(chat_id: int) -> str:
    """
    Склеивает историю сообщений чата в текст для Responses API.
    """
    ensure_history(chat_id)
    parts = []
    for msg in chat_history[chat_id]:
        role = msg["role"]
        prefix = "Пользователь:" if role == "user" else "Ассистент:"
        parts.append(f"{prefix} {msg['content']}")
    return "\n".join(parts)

def add_message(chat_id: int, role: str, content: str):
    ensure_history(chat_id)
    chat_history[chat_id].append({"role": role, "content": content})
    # Чтобы история не росла бесконечно
    if len(chat_history[chat_id]) > 20:
        chat_history[chat_id] = chat_history[chat_id][-20:]

def get_gpt_reply(chat_id: int) -> str:
    """
    Отправляет историю чата в Responses API и получает ответ.
    """
    try:
        input_text = build_input_from_history(chat_id)
        resp = client.responses.create(
            model=MODEL,
            input=input_text,
            max_output_tokens=600,
        )
        return resp.output_text
    except Exception as e:
        print(f"[OpenAI ERROR] {e}", file=sys.stderr)
        return "Ошибка при обращении к GPT."

# === СОСТОЯНИЕ ОНБОРДИНГА =====================================================
user_state: Dict[int, str] = {}
user_data: Dict[int, dict] = {}

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
    add_message(chat_id, "assistant", text)
    await update.message.reply_text(text, reply_markup=markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id)

    if state == "AWAIT_NEXT" and text == "Далее":
        user_state[chat_id] = "AWAIT_PET_TYPE"
        markup = ReplyKeyboardMarkup([["Кошка", "Собака", "Оба"]], resize_keyboard=True, one_time_keyboard=True)
        bot_text = "Кто у тебя дома?"
        add_message(chat_id, "assistant", bot_text)
        await update.message.reply_text(bot_text, reply_markup=markup)

    elif state == "AWAIT_PET_TYPE":
        user_data[chat_id] = {"pet_type": text}
        user_state[chat_id] = "AWAIT_PET_INFO"
        bot_text = "Расскажи о питомце:\n1. Имя:\n2. Порода:\n3. Возраст:\n4. Вес:\n5. Пол:"
        add_message(chat_id, "user", text)
        add_message(chat_id, "assistant", bot_text)
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
        add_message(chat_id, "user", text)
        add_message(chat_id, "assistant", bot_text)
        await update.message.reply_text(bot_text, reply_markup=markup)

    elif state == "DONE":
        add_message(chat_id, "user", text)
        await update.message.reply_text("Секунду, думаю…")
        reply = get_gpt_reply(chat_id)
        add_message(chat_id, "assistant", reply)
        await update.message.reply_text(reply)

    else:
        bot_text = "Пожалуйста, нажми /start для начала."
        add_message(chat_id, "assistant", bot_text)
        await update.message.reply_text(bot_text)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).read_timeout(60).write_timeout(60).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("[BOT] Polling started.")
    app.run_polling()

if __name__ == "__main__":
    main()
