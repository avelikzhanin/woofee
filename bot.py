import os
import sys
import warnings
import asyncio
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

# --- ПРЕДУПРЕЖДЕНИЯ -----------------------------------------------------------
# Игнорируем DeprecationWarnings от Assistants API (мы его слегка трогаем только для чтения инструкций)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# --- ЗАГРУЗКА ОКРУЖЕНИЯ -------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")  # опционально
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # можно переопределить в Railway

def check_env_var(name: str, value: Optional[str], min_len=10) -> bool:
    if not value:
        print(f"[ENV ERROR] {name} не найден. Проверь переменные окружения / .env", file=sys.stderr)
        return False
    if len(value.strip()) < min_len:
        print(f"[ENV WARNING] {name} слишком короткий: {repr(value)}", file=sys.stderr)
    return True

if not (check_env_var("BOT_TOKEN", BOT_TOKEN, 20) and check_env_var("OPENAI_API_KEY", OPENAI_API_KEY, 20)):
    sys.exit("Остановлено: нет корректных переменных окружения.")

# --- OpenAI клиент ------------------------------------------------------------
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Получаем инструкции ассистента (если есть) -------------------------------
DEFAULT_INSTRUCTIONS = (
    "Ты — заботливый и дружелюбный помощник по уходу за домашними животными. "
    "Помогай владельцам собак и кошек с воспитанием, дрессировкой, уходом, питанием, "
    "играми, безопасностью в путешествиях и здоровьем питомцев. "
    "Отвечай понятно, короткими блоками, давай практические шаги. "
    "Если вопрос не связан с животными, вежливо отвечай: 'Я могу помочь только с вопросами о питомцах.'"
)

def load_assistant_instructions() -> str:
    """Если задан ASSISTANT_ID, пробуем подтянуть инструкции ассистента.
    Иначе — возвращаем дефолтные инструкции."""
    if not ASSISTANT_ID:
        print("[ASSISTANT] ASSISTANT_ID не задан. Используем дефолтные инструкции.")
        return DEFAULT_INSTRUCTIONS
    try:
        a = client.beta.assistants.retrieve(ASSISTANT_ID)
        inst = (a.instructions or "").strip()
        if not inst:
            print("[ASSISTANT] У ассистента нет инструкций. Используем дефолтные.")
            return DEFAULT_INSTRUCTIONS
        print("[ASSISTANT] Инструкции ассистента успешно загружены.")
        return inst
    except Exception as e:
        print(f"[ASSISTANT ERROR] Не удалось получить инструкции: {e}", file=sys.stderr)
        return DEFAULT_INSTRUCTIONS

SYSTEM_PROMPT = load_assistant_instructions()

# --- Контекст чатов -----------------------------------------------------------
# chat_id -> list[{"role": "...", "content": "..."}]
chat_history: Dict[int, List[dict]] = {}
MAX_HISTORY = 30  # сколько сообщений (кроме system) держим

def ensure_history(chat_id: int):
    if chat_id not in chat_history:
        chat_history[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

def add_to_history(chat_id: int, role: str, content: str):
    ensure_history(chat_id)
    chat_history[chat_id].append({"role": role, "content": content})
    # ограничиваем историю (system + хвост)
    system = chat_history[chat_id][:1]
    tail = chat_history[chat_id][1:][-MAX_HISTORY:]
    chat_history[chat_id] = system + tail

def build_messages(chat_id: int) -> List[dict]:
    ensure_history(chat_id)
    return chat_history[chat_id]

# --- GPT вызов (в фоне, чтобы не блокировать event loop) ----------------------
def _get_gpt_reply_sync(chat_id: int) -> str:
    try:
        resp = client.responses.create(
            model=MODEL,
            messages=build_messages(chat_id),
            max_output_tokens=600,
        )
        text = resp.output_text or "Ассистент не прислал ответ."
        # сохраняем в историю
        add_to_history(chat_id, "assistant", text)
        return text
    except Exception as e:
        print(f"[OpenAI ERROR] {e}", file=sys.stderr)
        return "Ошибка при обращении к GPT."

async def get_gpt_reply(chat_id: int) -> str:
    # выполняем синхронный вызов OpenAI в отдельном потоке,
    # чтобы не блокировать asyncio-цикл Telegram.
    return await asyncio.to_thread(_get_gpt_reply_sync, chat_id)

# --- Состояние онбординга -----------------------------------------------------
user_state: Dict[int, str] = {}
user_data: Dict[int, dict] = {}

# --- Хэндлеры -----------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_history(chat_id)
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
        # Добавим профиль в контекст (важно для последующих ответов GPT!)
        profile_text = (
            f"Контекст: у пользователя {user_data[chat_id].get('pet_type')} питомец. "
            f"Данные: {text}. Используй это в дальнейших ответах."
        )
        add_to_history(chat_id, "assistant", profile_text)

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
        add_to_history(chat_id, "assistant", bot_text)
        return

    if state == "DONE":
        # логируем сообщение пользователя и спрашиваем GPT
        add_to_history(chat_id, "user", text)
        thinking = await update.message.reply_text("Секунду, думаю…")
        reply = await get_gpt_reply(chat_id)
        await thinking.edit_text(reply)
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
