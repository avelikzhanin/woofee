import os
import sys
from typing import Dict, List, Optional

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

# === ЗАГРУЗКА .ENV ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
# .env может отсутствовать в проде (Railway); ошибки терпим
if os.path.exists(ENV_PATH):
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()  # на всякий случай

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def check_env_var(name: str, value: Optional[str], min_len=10) -> bool:
    if not value:
        print(f"[ENV ERROR] {name} не найден. Проверь переменные окружения / .env", file=sys.stderr)
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

# === СИСТЕМНЫЙ ПРОМПТ (роль ассистента) ======================================
SYSTEM_PROMPT = (
    "Ты — заботливый и дружелюбный помощник по уходу за домашними животными. "
    "Помогай владельцам собак и кошек с воспитанием, дрессировкой, уходом, питанием, "
    "играми, безопасностью в путешествиях и здоровьем питомцев. "
    "Отвечай понятно, короткими блоками, давай практические шаги. "
    "Если вопрос не связан с животными, вежливо отвечай: 'Я могу помочь только с вопросами о питомцах.'"
)

# === ХРАНЕНИЕ ИСТОРИИ ЧАТА ===================================================
# Каждому Telegram chat_id соответствует список сообщений формата {"role": "...", "content": "..."}
chat_history: Dict[int, List[dict]] = {}

# сколько последних сообщений (кроме system) держим в контексте
MAX_HISTORY = 20

def _ensure_history(chat_id: int):
    if chat_id not in chat_history:
        # создаём историю сразу с system-промптом
        chat_history[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

def add_to_history(chat_id: int, role: str, content: str):
    _ensure_history(chat_id)
    chat_history[chat_id].append({"role": role, "content": content})
    # не обрезаем system (первый элемент); ограничиваем остальное
    base = chat_history[chat_id][0:1]  # system
    tail = chat_history[chat_id][1:][-MAX_HISTORY:]
    chat_history[chat_id] = base + tail

def build_messages_for_openai(chat_id: int) -> List[dict]:
    _ensure_history(chat_id)
    return chat_history[chat_id]

def get_gpt_reply(chat_id: int) -> str:
    """
    Вызывает OpenAI Responses API, передавая текущую историю.
    Возвращает текст ответа (или сообщение об ошибке).
    """
    try:
        messages = build_messages_for_openai(chat_id)
        resp = client.responses.create(
            model="gpt-4.1-mini",
            messages=messages,
            max_output_tokens=600,
        )
        reply_text = resp.output_text or "Ассистент не прислал ответ."
        # записываем ответ в историю
        add_to_history(chat_id, "assistant", reply_text)
        return reply_text
    except Exception as e:
        print(f"[OpenAI ERROR] {e}", file=sys.stderr)
        return "Ошибка при обращении к GPT."

# === СОСТОЯНИЕ ОНБОРДИНГА =====================================================
user_state: Dict[int, str] = {}
user_data: Dict[int, dict] = {}

# === ОБРАБОТЧИКИ ==============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _ensure_history(chat_id)  # инициализируем историю

    user_state[chat_id] = "AWAIT_NEXT"

    markup = ReplyKeyboardMarkup([["Далее"]], resize_keyboard=True, one_time_keyboard=True)
    text = (
        "Привет! Я твой помощник по уходу за домашним питомцем.\n"
        "Помогу с уходом, дрессировками, играми и по любым вопросам.\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным."
    )
    await update.message.reply_text(text, reply_markup=markup)
    add_to_history(chat_id, "assistant", text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id)

    if state == "AWAIT_NEXT" and text == "Далее":
        user_state[chat_id] = "AWAIT_PET_TYPE"
        markup = ReplyKeyboardMarkup([["Кошка", "Собака", "Оба"]], resize_keyboard=True, one_time_keyboard=True)
        bot_text = "Кто у тебя дома?"
        await update.message.reply_text(bot_text, reply_markup=markup)
        add_to_history(chat_id, "assistant", bot_text)
        return

    if state == "AWAIT_PET_TYPE":
        user_data[chat_id] = {"pet_type": text}
        user_state[chat_id] = "AWAIT_PET_INFO"
        bot_text = "Расскажи о питомце:\n1. Имя:\n2. Порода:\n3. Возраст:\n4. Вес:\n5. Пол:"
        await update.message.reply_text(bot_text)
        add_to_history(chat_id, "user", f"Тип питомца: {text}")
        add_to_history(chat_id, "assistant", bot_text)
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
        add_to_history(chat_id, "user", f"Инфо о питомце: {text}")
        add_to_history(chat_id, "assistant", bot_text)
        return

    if state == "DONE":
        # добавляем пользовательский ввод в контекст и вызываем GPT
        add_to_history(chat_id, "user", text)
        await update.message.reply_text("Секунду, думаю…")
        reply = get_gpt_reply(chat_id)
        await update.message.reply_text(reply)
        return

    # fallback
    bot_text = "Пожалуйста, нажми /start для начала."
    await update.message.reply_text(bot_text)
    add_to_history(chat_id, "assistant", bot_text)

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
