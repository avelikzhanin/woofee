import os
import sys
import asyncio
import warnings
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
from telegram.error import TimedOut

# ---------------------------------------------------------------------------
#  Environment
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")  # optional
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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

# ---------------------------------------------------------------------------
#  OpenAI client
# ---------------------------------------------------------------------------
# Фильтруем DeprecationWarnings (Assistants API deprecated)
warnings.filterwarnings("ignore", category=DeprecationWarning)

client = OpenAI(api_key=OPENAI_API_KEY)

DEFAULT_INSTRUCTIONS = (
    "Ты — заботливый и дружелюбный помощник по уходу за домашними животными. "
    "Помогай владельцам собак и кошек с воспитанием, дрессировкой, уходом, питанием, "
    "играми, безопасностью в путешествиях и здоровьем питомцев. "
    "Отвечай понятно, короткими блоками, давай практические шаги. "
    "Если вопрос не связан с животными, вежливо отвечай: 'Я могу помочь только с вопросами о питомцах.'"
)

def load_assistant_instructions() -> str:
    """Попытка получить instructions из заданного ассистента."""
    if not ASSISTANT_ID:
        print("[ASSISTANT] ASSISTANT_ID не задан. Используем дефолтные инструкции.")
        return DEFAULT_INSTRUCTIONS
    try:
        a = client.beta.assistants.retrieve(ASSISTANT_ID)
        inst = (a.instructions or "").strip()
        if not inst:
            print("[ASSISTANT] Инструкции ассистента пустые. Используем дефолтные.")
            return DEFAULT_INSTRUCTIONS
        print("[ASSISTANT] Инструкции ассистента загружены.")
        return inst
    except Exception as e:
        print(f"[ASSISTANT ERROR] {e}", file=sys.stderr)
        return DEFAULT_INSTRUCTIONS

SYSTEM_PROMPT = load_assistant_instructions()

# ---------------------------------------------------------------------------
#  Assistants Thread logging (optional but requested)
# ---------------------------------------------------------------------------
# Сохраняем thread_id для каждого Telegram chat_id в памяти.
user_threads: Dict[int, str] = {}
LOG_ONBOARDING_TO_THREAD = False       # можно True, если хочешь логировать все шаги
ASSOCIATE_THREAD_WITH_ASSISTANT = True # создаём 1 run при /start, чтобы Thread появился в UI

def _create_thread_for_chat_sync(chat_id: int, username: Optional[str]) -> str:
    meta = {"telegram_chat_id": str(chat_id)}
    if username:
        meta["telegram_username"] = username
    thread = client.beta.threads.create(metadata=meta)
    print(f"[THREAD CREATED] chat_id={chat_id} -> {thread.id}")
    # Чтобы Thread был связан с ассистентом в UI: создадим run (не ждём ответа)
    if ASSISTANT_ID and ASSOCIATE_THREAD_WITH_ASSISTANT:
        try:
            client.beta.threads.runs.create(
                thread_id=thread.id,
                assistant_id=ASSISTANT_ID,
                # Не обязательно что-то передавать; этот run просто «регистрирует» связь
                instructions="Bootstrap run: связать Thread с ассистентом."
            )
        except Exception as e:
            print(f"[THREAD RUN WARN] Не удалось связать Thread с ассистентом: {e}", file=sys.stderr)
    return thread.id

async def get_or_create_thread_for_chat(chat_id: int, username: Optional[str]) -> str:
    if chat_id in user_threads:
        return user_threads[chat_id]
    thread_id = await asyncio.to_thread(_create_thread_for_chat_sync, chat_id, username)
    user_threads[chat_id] = thread_id
    return thread_id

def _log_message_to_thread_sync(thread_id: str, role: str, content: str):
    try:
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role=role,
            content=content,
        )
    except Exception as e:
        print(f"[THREAD MSG ERROR] {e}", file=sys.stderr)

async def log_message_to_thread(thread_id: str, role: str, content: str):
    # Асинхронно: не блокируем Telegram-бот
    await asyncio.to_thread(_log_message_to_thread_sync, thread_id, role, content)

# ---------------------------------------------------------------------------
#  Local chat history for Responses API (context memory)
# ---------------------------------------------------------------------------
# chat_id -> list of {role, content}
chat_history: Dict[int, List[dict]] = {}
MAX_HISTORY = 30  # без system

def ensure_history(chat_id: int):
    if chat_id not in chat_history:
        chat_history[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

def add_to_history(chat_id: int, role: str, content: str):
    ensure_history(chat_id)
    chat_history[chat_id].append({"role": role, "content": content})
    system = chat_history[chat_id][:1]
    tail = chat_history[chat_id][1:][-MAX_HISTORY:]
    chat_history[chat_id] = system + tail

def build_messages(chat_id: int) -> List[dict]:
    ensure_history(chat_id)
    return chat_history[chat_id]

def _get_gpt_reply_sync(chat_id: int) -> str:
    try:
        messages = build_messages(chat_id)
        resp = client.responses.create(
            model=MODEL,
            messages=messages,
            max_output_tokens=600,
        )
        text = resp.output_text or "Ассистент не прислал ответ."
        # сохраняем в локальную историю
        add_to_history(chat_id, "assistant", text)
        return text
    except Exception as e:
        import traceback
        print(f"[GPT ERROR] {e}\n{traceback.format_exc()}", file=sys.stderr)
        return f"Ошибка при обращении к GPT: {e}"

async def get_gpt_reply(chat_id: int) -> str:
    return await asyncio.to_thread(_get_gpt_reply_sync, chat_id)

# ---------------------------------------------------------------------------
#  Onboarding state
# ---------------------------------------------------------------------------
user_state: Dict[int, str] = {}
user_data: Dict[int, dict] = {}

# ---------------------------------------------------------------------------
#  Telegram helpers
# ---------------------------------------------------------------------------
async def safe_reply(update: Update, text: str, **kwargs):
    try:
        return await update.message.reply_text(text, **kwargs)
    except TimedOut:
        print("[TG WARNING] reply timeout", file=sys.stderr)
    except Exception as e:
        print(f"[TG ERROR] reply_text: {e}", file=sys.stderr)
    return None

async def safe_edit(message, text: str):
    if not message:
        return
    try:
        await message.edit_text(text)
    except TimedOut:
        print("[TG WARNING] edit timeout", file=sys.stderr)
    except Exception as e:
        print(f"[TG ERROR] edit_text: {e}", file=sys.stderr)

# ---------------------------------------------------------------------------
#  Handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_chat.username

    ensure_history(chat_id)
    user_state[chat_id] = "AWAIT_NEXT"

    # создаём Assistants Thread (логирование)
    thread_id = await get_or_create_thread_for_chat(chat_id, username)

    markup = ReplyKeyboardMarkup([["Далее"]], resize_keyboard=True, one_time_keyboard=True)
    text = (
        "Привет! Я твой помощник по уходу за домашним питомцем.\n"
        "Помогу с уходом, дрессировками, играми и по любым вопросам.\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным."
    )
    await safe_reply(update, text, reply_markup=markup)
    add_to_history(chat_id, "assistant", text)

    if LOG_ONBOARDING_TO_THREAD:
        asyncio.create_task(log_message_to_thread(thread_id, "assistant", text))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_chat.username
    text = update.message.text
    state = user_state.get(chat_id)

    # thread для логов
    thread_id = await get_or_create_thread_for_chat(chat_id, username)

    if state == "AWAIT_NEXT" and text == "Далее":
        user_state[chat_id] = "AWAIT_PET_TYPE"
        markup = ReplyKeyboardMarkup([["Кошка", "Собака", "Оба"]], resize_keyboard=True, one_time_keyboard=True)
        bot_text = "Кто у тебя дома?"
        await safe_reply(update, bot_text, reply_markup=markup)
        add_to_history(chat_id, "assistant", bot_text)
        if LOG_ONBOARDING_TO_THREAD:
            asyncio.create_task(log_message_to_thread(thread_id, "assistant", bot_text))
        return

    if state == "AWAIT_PET_TYPE":
        user_data[chat_id] = {"pet_type": text}
        user_state[chat_id] = "AWAIT_PET_INFO"
        bot_text = "Расскажи о питомце:\n1. Имя:\n2. Порода:\n3. Возраст:\n4. Вес:\n5. Пол:"
        await safe_reply(update, bot_text)
        add_to_history(chat_id, "user", f"Тип питомца: {text}")
        add_to_history(chat_id, "assistant", bot_text)
        if LOG_ONBOARDING_TO_THREAD:
            asyncio.create_task(log_message_to_thread(thread_id, "user", f"Тип питомца: {text}"))
            asyncio.create_task(log_message_to_thread(thread_id, "assistant", bot_text))
        return

    if state == "AWAIT_PET_INFO":
        user_data[chat_id]["pet_info"] = text
        user_state[chat_id] = "DONE"

        # Контекст профиля добавим в историю как assistant (служебное)
        profile_text = (
            f"Контекст: у пользователя {user_data[chat_id].get('pet_type')} питомец. "
            f"Данные: {text}. Учитывай это в дальнейших ответах."
        )
        add_to_history(chat_id, "assistant", profile_text)
        if LOG_ONBOARDING_TO_THREAD:
            asyncio.create_task(log_message_to_thread(thread_id, "assistant", profile_text))

        markup = ReplyKeyboardMarkup(
            [["Воспитание", "Дрессировка"], ["Игры", "Уход"]],
            resize_keyboard=True
        )
        bot_text = (
            "Отлично, всё готово. Можешь задать любой вопрос:\n\n"
            "Примеры:\n- Как приучить щенка к туалету?\n- Как научить собаку команде 'Сидеть'?\n"
            "Или выбери интересующую тему ниже."
        )
        await safe_reply(update, bot_text, reply_markup=markup)
        add_to_history(chat_id, "assistant", bot_text)
        if LOG_ONBOARDING_TO_THREAD:
            asyncio.create_task(log_message_to_thread(thread_id, "user", f"Инфо о питомце: {text}"))
            asyncio.create_task(log_message_to_thread(thread_id, "assistant", bot_text))
        return

    if state == "DONE":
        # Логируем user сообщение в локальный контекст
        add_to_history(chat_id, "user", text)
        # И в Thread (чтобы видеть в OpenAI UI)
        asyncio.create_task(log_message_to_thread(thread_id, "user", text))

        thinking = await safe_reply(update, "Секунду, думаю…")

        reply = await get_gpt_reply(chat_id)

        # Логируем ответ ассистента в Thread
        asyncio.create_task(log_message_to_thread(thread_id, "assistant", reply))

        await safe_edit(thinking, reply)
        return

    # fallback
    bot_text = "Пожалуйста, нажми /start для начала."
    await safe_reply(update, bot_text)
    add_to_history(chat_id, "assistant", bot_text)
    asyncio.create_task(log_message_to_thread(thread_id, "assistant", bot_text))

# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
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
