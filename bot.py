import os
import time
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")  # ID вашего Assistant

client = OpenAI(api_key=OPENAI_API_KEY)

user_state = {}
user_data = {}
user_threads = {}  # Для хранения thread_id каждого пользователя

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = "AWAIT_NEXT"
    
    # Создаем новый thread для пользователя
    thread = client.beta.threads.create()
    user_threads[chat_id] = thread.id
    
    markup = ReplyKeyboardMarkup([["Далее"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Привет! Я твой помощник по уходу за домашним питомцем.\n"
        "Помогу с уходом, дрессировками, играми и по любым вопросам.\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным.",
        reply_markup=markup
    )

async def ask_assistant(prompt: str, chat_id: int) -> str:
    try:
        thread_id = user_threads.get(chat_id)
        
        if not thread_id:
            # Создаем новый thread если его нет
            thread = client.beta.threads.create()
            user_threads[chat_id] = thread.id
            thread_id = thread.id
        
        # Добавляем сообщение в thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )
        
        # Запускаем Assistant
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )
        
        # Ждем завершения выполнения
        while run.status in ['queued', 'in_progress', 'cancelling']:
            time.sleep(1)
            run = client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )
        
        if run.status == 'completed':
            # Получаем сообщения из thread
            messages = client.beta.threads.messages.list(
                thread_id=thread_id
            )
            
            # Возвращаем последний ответ Assistant
            for message in messages.data:
                if message.role == "assistant":
                    return message.content[0].text.value
        
        elif run.status == 'requires_action':
            # Если требуется действие (например, function calling)
            return "Assistant требует дополнительных действий. Попробуйте переформулировать вопрос."
        
        else:
            return f"Ошибка выполнения: {run.status}"
            
    except Exception as e:
        print(f"Ошибка при обращении к Assistant API: {e}")
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
            ["Экстренные советы", "Напиши свой вариант"]
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
        
        # Отправляем контекст пользователя в Assistant
        user_context = f"""
        Информация о пользователе:
        - Тип питомца: {user_data[chat_id].get('pet_type', 'Не указано')}
        - Информация о питомце: {user_data[chat_id].get('pet_info', 'Не указано')}
        - Интересующая область помощи: {user_data[chat_id].get('help_area', 'Не указано')}
        
        Пожалуйста, учитывай эту информацию при ответах на вопросы пользователя.
        """
        
        await ask_assistant(user_context, chat_id)
        
        markup = ReplyKeyboardMarkup([["Воспитание", "Дрессировка", "Игры", "Уход"]], resize_keyboard=True)
        await update.message.reply_text(
            "Отлично, всё готово. Можешь задать любой вопрос:\n\n"
            "Примеры:\n- Как приучить щенка к туалету?\n- Чем кормить щенка хаски?",
            reply_markup=markup
        )

    elif state == "DONE":
        await update.message.reply_text("Секунду, думаю…")
        reply = await ask_assistant(text, chat_id)
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
