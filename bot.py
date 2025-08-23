import os
import time
import base64
import requests
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
        "Также могу анализировать фотографии твоего питомца!\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным.",
        reply_markup=markup
    )

async def download_photo(photo_file_id: str, bot_token: str) -> bytes:
    """Загружает фото из Telegram и возвращает его в виде байтов"""
    try:
        # Получаем информацию о файле
        file_info_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={photo_file_id}"
        file_info_response = requests.get(file_info_url)
        file_info = file_info_response.json()
        
        if not file_info.get('ok'):
            raise Exception(f"Ошибка получения информации о файле: {file_info}")
        
        file_path = file_info['result']['file_path']
        
        # Загружаем файл
        file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        file_response = requests.get(file_url)
        
        return file_response.content
        
    except Exception as e:
        print(f"Ошибка загрузки фото: {e}")
        raise

async def analyze_photo_with_gpt(image_bytes: bytes, text_prompt: str = "") -> str:
    """Анализирует фото с помощью GPT-4 Vision"""
    try:
        # Конвертируем изображение в base64
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        # Создаем промпт для анализа изображения питомца
        system_prompt = """Ты профессиональный ветеринар и специалист по уходу за домашними животными. 
        Анализируй фотографии животных и давай полезные рекомендации по:
        - Здоровью и состоянию питомца
        - Уходу и содержанию
        - Поведению
        - Питанию
        - Безопасности
        
        Отвечай на русском языке, будь внимательным и заботливым."""
        
        user_prompt = f"""Проанализируй эту фотографию питомца. 
        {text_prompt if text_prompt else "Что ты видишь? Дай рекомендации по уходу, здоровью или поведению на основе того, что изображено."}
        
        Обрати внимание на:
        1. Общее состояние животного
        2. Условия содержания (если видны)
        3. Возможные проблемы или опасности
        4. Рекомендации по улучшению ухода
        """
        
        response = client.chat.completions.create(
            model="gpt-4o",  # или "gpt-4-vision-preview" если используете старую версию
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000,
            temperature=0.7
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        print(f"Ошибка анализа фото с GPT: {e}")
        return f"Извини, не смог проанализировать фото. Ошибка: {str(e)}"

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

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фотографий"""
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id)
    
    if state != "DONE":
        await update.message.reply_text(
            "Сначала завершите настройку бота с помощью команды /start"
        )
        return
    
    try:
        await update.message.reply_text("📸 Анализирую фотографию...")
        
        # Получаем самое большое фото (лучшее качество)
        photo = update.message.photo[-1]
        
        # Загружаем фото
        image_bytes = await download_photo(photo.file_id, BOT_TOKEN)
        
        # Получаем текст сообщения, если есть
        caption = update.message.caption or ""
        
        # Анализируем фото с помощью GPT Vision
        analysis_result = await analyze_photo_with_gpt(image_bytes, caption)
        
        await update.message.reply_text(f"🔍 **Анализ фотографии:**\n\n{analysis_result}")
        
        # Также добавляем результат анализа в контекст Assistant
        context_message = f"Пользователь прислал фотографию. Результат анализа: {analysis_result}"
        if caption:
            context_message += f"\nПодпись к фото: {caption}"
        
        await ask_assistant(context_message, chat_id)
        
    except Exception as e:
        print(f"Ошибка обработки фото: {e}")
        await update.message.reply_text(
            "Извини, не смог обработать фотографию. Попробуй еще раз или отправь другое фото."
        )

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
            "Расскажи о питомце:\n1. Имя:\n2. Порода:\n3. Возраст:\n4. Вес:\n5. Пол:",
            reply_markup=ReplyKeyboardRemove()
        )

    elif state == "AWAIT_PET_INFO":
        user_data[chat_id]["pet_info"] = text
        user_state[chat_id] = "AWAIT_HELP_AREA"
        markup = ReplyKeyboardMarkup([
            ["Уход и питание", "Поведение и здоровье"],
            ["Игры и досуг", "Напиши свой вариант"],
        ], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("В чём тебе важнее всего моя помощь?", reply_markup=markup)

    elif state == "AWAIT_HELP_AREA":
        user_data[chat_id]["help_area"] = text
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
        
        await update.message.reply_text(
            "Отлично, всё готово! Можешь задать любой вопрос или отправить фото питомца для анализа:\n\n"
            "Примеры:\n- Как приучить щенка к туалету?\n- Чем кормить щенка хаски?\n- 📸 Отправь фото питомца для анализа",
            reply_markup=ReplyKeyboardRemove()
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
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))  # Обработчик фотографий
    app.run_polling()

if __name__ == "__main__":
    main()
