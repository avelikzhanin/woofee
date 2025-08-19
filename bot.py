import os
import time
import base64
import io
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import requests
from PIL import Image

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
    
    markup = ReplyKeyboardMarkup([["Далее"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Привет! Я твой помощник по уходу за домашним питомцем 🐕🐈\n"
        "Помогу с уходом, дрессировками, играми и по любым вопросам.\n"
        "Также могу анализировать фотографии твоего питомца!\n"
        "Начнём с небольшой настройки — так я смогу быть максимально полезным.",
        reply_markup=markup
    )

def encode_image_from_url(image_url: str) -> str:
    """Скачивает изображение по URL и кодирует в base64"""
    try:
        response = requests.get(image_url)
        response.raise_for_status()
        
        # Конвертируем в RGB если нужно и сжимаем
        image = Image.open(io.BytesIO(response.content))
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Сжимаем изображение для экономии токенов
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        
        # Конвертируем обратно в bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG', quality=85)
        img_byte_arr.seek(0)
        
        return base64.b64encode(img_byte_arr.read()).decode('utf-8')
    except Exception as e:
        print(f"Ошибка при обработке изображения: {e}")
        return None

async def ask_assistant(prompt: str, chat_id: int, image_base64: str = None) -> str:
    try:
        thread_id = user_threads.get(chat_id)
        
        if not thread_id:
            thread = client.beta.threads.create()
            user_threads[chat_id] = thread.id
            thread_id = thread.id
        
        # Если есть изображение, используем Vision API напрямую
        if image_base64:
            response = client.chat.completions.create(
                model="gpt-4.1",  # Ваша модель GPT-4.1
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
            return response.choices[0].message.content
        
        # Обычное сообщение без изображения - используем Assistant API
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )
        
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )
        
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
                    return message.content[0].text.value
        
        return f"Ошибка выполнения: {run.status}"
            
    except Exception as e:
        print(f"Ошибка при обращении к OpenAI API: {e}")
        return "Произошла ошибка при обращении к ИИ. Попробуй позже."

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, "")
    
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
        
        # Получаем URL изображения
        image_url = file.file_path
        
        # Кодируем изображение
        image_base64 = encode_image_from_url(image_url)
        
        if not image_base64:
            await update.message.reply_text(
                "Не удалось обработать изображение. Попробуйте еще раз."
            )
            return
        
        # Получаем caption если есть
        caption = update.message.caption or "Проанализируй эту фотографию моего питомца"
        
        # Отправляем на анализ
        response = await ask_assistant(caption, chat_id, image_base64)
        await update.message.reply_text(response)
        
    except Exception as e:
        print(f"Ошибка при обработке фото: {e}")
        await update.message.reply_text(
            "Произошла ошибка при анализе фотографии. Попробуйте еще раз."
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
        
        user_context = f"""
        Информация о пользователе:
        - Тип питомца: {user_data[chat_id].get('pet_type', 'Не указано')}
        - Информация о питомце: {user_data[chat_id].get('pet_info', 'Не указано')}
        - Интересующая область помощи: {user_data[chat_id].get('help_area', 'Не указано')}
        
        Пожалуйста, учитывай эту информацию при ответах на вопросы пользователя.
        """
        
        await ask_assistant(user_context, chat_id)
        
        await update.message.reply_text(
            "Отлично, всё готово! 🎉\n\n"
            "Можешь:\n"
            "• Задать любой вопрос\n"
            "• Отправить фотографию питомца для анализа\n\n"
            "Примеры:\n"
            "- Как приучить щенка к туалету?\n"
            "- Чем кормить котенка?\n"
            "- Отправь фото для анализа поведения или здоровья",
            reply_markup=ReplyKeyboardRemove()
        )

    elif state == "DONE":
        await update.message.reply_text("Секунду, думаю… 🤔")
        reply = await ask_assistant(text, chat_id)
        await update.message.reply_text(reply)

    else:
        await update.message.reply_text("Пожалуйста, нажми /start для начала.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Бот запущен! Поддерживается анализ фотографий 📸")
    app.run_polling()

if __name__ == "__main__":
    main()
