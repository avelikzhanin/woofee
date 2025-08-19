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
        
        # ИСПРАВЛЕНО: Используем более надежный способ отправки изображения
        try:
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
                        
                        Отвечай БЕЗ Markdown-разметки (не используй **, *, # и т.д.)
                        
                        ВАЖНО: Если ты видишь изображение, обязательно опиши его. Если изображение не загрузилось, скажи об этом четко."""
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
                                    "detail": "high"  # Изменил на high для лучшего качества
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            ai_response = response.choices[0].message.content
            logger.info("Получен ответ от Vision API")
            
            # Проверяем, что ИИ действительно увидел изображение
            if "изображение" in ai_response.lower() and "не" in ai_response.lower() and "было" in ai_response.lower():
                # Если ИИ не видит изображение, пробуем альтернативный способ
                logger.warning("Vision API не видит изображение, пробуем альтернативный способ")
                
                # Сохраняем изображение во временный файл и отправляем как URL
                import tempfile
                import requests
                
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
                    image.save(tmp_file.name, 'JPEG', quality=85)
                    
                    # Здесь можно использовать сервис для загрузки изображений
                    # Или просто отправить текстовый ответ
                    await update.message.reply_text(
                        "Извините, возникла техническая проблема с анализом изображения. "
                        "Попробуйте отправить фото еще раз или опишите вашего питомца текстом."
                    )
                    return
            
            await update.message.reply_text(ai_response)
            logger.info(f"Анализ фото завершен для пользователя {chat_id}")
            
        except Exception as api_error:
            logger.error(f"Ошибка Vision API: {api_error}")
            await update.message.reply_text(
                "Произошла ошибка при анализе изображения. Попробуйте еще раз или опишите питомца текстом."
            )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке фото: {e}")
        await update.message.reply_text(
            "Произошла ошибка при обработке фотографии. Попробуйте еще раз."
        )
