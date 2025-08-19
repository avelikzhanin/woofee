import os
import time
import base64
import io
import logging
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from PIL import Image

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

client = OpenAI(api_key=OPENAI_API_KEY)

user_state = {}
user_data = {}
user_threads = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = "AWAIT_NEXT"
    
    thread = client.beta.threads.create()
    user_threads[chat_id] = thread.id
    
    logger.info(f"Новый пользователь {chat_id} начал настройку")
    
    markup = ReplyKeyboardMarkup([["Далее"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Привет! Я твой помощник по уходу за домашним питомцем 🐕��\n"
        "Помогу с уходом, дрессировками, играми и по любым вопросам.\n"
        "Также могу анализировать фотографии твоего питомца!\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным.",
        reply_markup=markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать справку по командам"""
    help_text = """
🤖 Доступные команды:
/start - Начать настройку бота
/help - Показать эту справку
/reset - Сбросить настройки и начать заново

📸 Отправьте фото питомца для анализа
💬 Задавайте любые вопросы о животных
    """
    await update.message.reply_text(help_text)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбросить настройки пользователя"""
    chat_id = update.effective_chat.id
    
    # Очищаем данные пользователя
    user_state.pop(chat_id, None)
    user_data.pop(chat_id, None)
    user_threads.pop(chat_id, None)
    
    await update.message.reply_text(
        "Настройки сброшены! Используйте /start для новой настройки."
    )
    logger.info(f"Пользователь {chat_id} сбросил настройки")

async def ask_assistant(prompt: str, chat_id: int, image_base64: str = None) -> str:
    try:
        thread_id = user_threads.get(chat_id)
        
        if not thread_id:
            thread = client.beta.threads.create()
            user_threads[chat_id] = thread.id
            thread_id = thread.id
        
        # Если есть изображение, используем Vision API напрямую
        if image_base64:
            logger.info(f"Отправляем изображение на анализ для пользователя {chat_id}")
            response = client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=[
                    {
                        "role": "system",
                        "content": """Ты — заботливый и опытный помощник для владельцев домашних животных. 
                        
                        При анализе фотографий:
                        - Описывай питомца позитивно и с любовью
                        - Обращай внимание на здоровье, поведение, окружение
                        - Давай советы по уходу если видишь что-то важное
                        - Избегай негативных слов: вместо "старый" говори "взрослый", "мудрый"
                        - Будь эмпатичным и поддерживающим
                        
                        Отвечай БЕЗ Markdown-разметки (не используй **, *, # и т.д.)"""
                    },
                    {
                        "role": "user", 
                        "content": [
                            {
                                "type": "text",
                                "text": f"{prompt}\n\nИнформация о питомце владельца:\n{user_data.get(chat_id, {})}"
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}",
                                    "detail": "low"  # Экономим токены
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000,
                temperature=0.7
            )
            logger.info("Получен ответ от Vision API")
            return response.choices[0].message.content
        
        # Обычное сообщение без изображения - используем Assistant API
        logger.info(f"Отправляем текстовый запрос для пользователя {chat_id}")
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )
        
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
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            
            for message in messages.data:
                if message.role == "assistant":
                    logger.info("Получен ответ от Assistant API")
                    return message.content[0].text.value
        
        logger.error(f"Ошибка выполнения Assistant: {run.status}")
        return f"Ошибка выполнения: {run.status}"
            
    except Exception as e:
        logger.error(f"Ошибка при обращении к OpenAI API: {e}")
        return "Произошла ошибка при обращении к ИИ. Попробуй позже."

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, "")
    
    logger.info(f"Пользователь {chat_id} отправил фото")
    
    # Проверяем, завершена ли настройка
    if state != "DONE":
        await update.message.reply_text(
            "Пожалуйста, сначала завершите настройку с помощью команды /start"
        )
        return
    
    await update.message.reply_text("Анализирую фотографию... 📸")
    
    try:
        # Получаем информацию о фото
        photo = update.message.photo[-1]  # Берем фото наибольшего размера
        file = await context.bot.get_file(photo.file_id)
        
        # Скачиваем изображение напрямую
        image_data = await file.download_as_bytearray()
        
        # Конвертируем в PIL Image
        image = Image.open(io.BytesIO(image_data))
        logger.info(f"Размер изображения: {image.size}, режим: {image.mode}")
        
        # Конвертируем в RGB если нужно
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Сжимаем изображение для экономии токенов
        original_size = image.size
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        logger.info(f"Сжато с {original_size} до {image.size}")
        
        # Конвертируем в base64
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG', quality=85)
        img_byte_arr.seek(0)
        
        image_base64 = base64.b64encode(img_byte_arr.read()).decode('utf-8')
        logger.info("Изображение успешно закодировано в base64")
        
        # Получаем caption если есть
        caption = update.message.caption or "Проанализируй эту фотографию моего питомца"
        logger.info(f"Caption: {caption}")
        
        # Отправляем на анализ
        response = await ask_assistant(caption, chat_id, image_base64)
        await update.message.reply_text(response)
        logger.info(f"Анализ фото завершен для пользователя {chat_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке фото: {e}")
        await update.message.reply_text(
            "Произошла ошибка при анализе фотографии. Попробуйте еще раз."
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id)

    logger.info(f"Пользователь {chat_id} в состоянии {state} написал: {text}")

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
        
        user_context = f"""
        Информация о пользователе:
        - Тип питомца: {user_data[chat_id].get('pet_type', 'Не указано')}
        - Информация о питомце: {user_data[chat_id].get('pet_info', 'Не указано')}
        - Интересующая область помощи: {user_data[chat_id].get('help_area', 'Не указано')}
        
        Пожалуйста, учитывай эту информацию при ответах на вопросы пользователя.
        """
        
        await ask_assistant(user_context, chat_id)
        
        await update.message.reply_text(
            "Отлично, всё готово! ��\n\n"
            "Можешь:\n"
            "• Задать любой вопрос\n"
            "• Отправить фотографию питомца для анализа\n\n"
            "Примеры:\n"
            "- Как приучить щенка к туалету?\n"
            "- Чем кормить котенка?\n"
            "- Отправь фото для анализа поведения или здоровья",
            reply_markup=ReplyKeyboardRemove()
        )
        
        logger.info(f"Настройка завершена для пользователя {chat_id}")

    elif state == "DONE":
        await update.message.reply_text("Секунду, думаю… 🤔")
        reply = await ask_assistant(text, chat_id)
        await update.message.reply_text(reply)

    else:
        await update.message.reply_text("Пожалуйста, нажми /start для начала.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Произошла ошибка. Попробуйте еще раз или обратитесь к разработчику."
        )

def main():
    if not BOT_TOKEN or not OPENAI_API_KEY or not ASSISTANT_ID:
        logger.error("Не все переменные окружения установлены! Проверьте .env файл")
        return
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчик ошибок
    app.add_error_handler(error_handler)
    
    logger.info("🚀 Бот запущен! Поддерживается анализ фотографий 📸")
    print("🚀 Бот запущен! Поддерживается анализ фотографий 📸")
    
    # Запускаем бота
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
