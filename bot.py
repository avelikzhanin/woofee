import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.utils import executor
from openai import AsyncOpenAI
import os

API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

user_data = {}

@dp.message_handler(commands=["start"])
async def start(message: Message):
    await message.answer("Привет! Я твой ИИ-помощник. Задай мне вопрос!")

async def ask_assistant_via_thread(chat_id: int, prompt: str) -> str:
    if chat_id not in user_data:
        user_data[chat_id] = {}

    if "thread_id" not in user_data[chat_id]:
        thread = await client.beta.threads.create()
        user_data[chat_id]["thread_id"] = thread.id

    thread_id = user_data[chat_id]["thread_id"]

    await client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=prompt
    )

    run = await client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

    while True:
        run_status = await client.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run.id
        )
        if run_status.status == "completed":
            break
        elif run_status.status == "failed":
            return "❌ Ошибка выполнения запроса."
        await asyncio.sleep(1)

    messages = await client.beta.threads.messages.list(thread_id=thread_id)
    for msg in reversed(messages.data):
        if msg.role == "assistant":
            return msg.content[0].text.value

    return "❌ Ответ не получен."

@dp.message_handler()
async def handle_message(message: Message):
    chat_id = message.chat.id
    text = message.text
    await message.chat.do("typing")
    try:
        reply = await ask_assistant_via_thread(chat_id, text)
    except Exception as e:
        reply = "Произошла ошибка: " + str(e)
    await message.answer(reply)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, skip_updates=True)
