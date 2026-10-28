import base64
import os
import threading
import time
import urllib.parse
from flask import Flask
import requests
import telebot

TELEGRAM_TOKEN = "9129090353:AAFOZU4NEVz1SzXow8JQe5CIx1a0sSam19I"
GEMINI_KEY = "AIzaSyAS-RJstICvgB6z7cEYmwys1C0ni47qdvQ"
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]
TIMEOUT_SECONDS = 600  # 10 минут памяти

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot_info = bot.get_me()
BOT_NAME = bot_info.username.lower()

# Память диалогов: {chat_id: {"last_time": timestamp, "messages": []}}
chat_memory = {}

# Веб-сервер для поддержания жизни на Render
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive 24/7!"


def get_or_reset_memory(chat_id):
    """Проверяет таймаут 10 минут и возвращает активную память"""
    now = time.time()
    if chat_id in chat_memory:
        # Если прошло больше 10 минут — сбрасываем память
        if now - chat_memory[chat_id]["last_time"] > TIMEOUT_SECONDS:
            chat_memory[chat_id] = {"last_time": now, "messages": []}
    else:
        chat_memory[chat_id] = {"last_time": now, "messages": []}

    chat_memory[chat_id]["last_time"] = now
    return chat_memory[chat_id]["messages"]


# 1. Команда просмотра истории /history
@bot.message_handler(commands=["history"])
def show_history(message):
    messages = get_or_reset_memory(message.chat.id)
    if not messages:
        bot.reply_to(
            message, "Память пуста или прошло больше 10 минут молчания."
        )
        return

    text_history = "📜 **Текущая история диалога:**\n\n"
    for item in messages:
        role = "👤 Вы" if item["role"] == "user" else "🤖 Бот"
        part_text = item["parts"][0].get("text", "[Вложение]")
        text_history += f"{role}: {part_text[:120]}\n"

    bot.reply_to(message, text_history)


# 2. Команда ручного сброса памяти /clear
@bot.message_handler(commands=["clear", "reset"])
def clear_memory(message):
    chat_memory[message.chat.id] = {"last_time": time.time(), "messages": []}
    bot.reply_to(
        message, "🧹 Память диалога очищена! Начинаем с чистого листа."
    )


# 3. Генерация картинок по команде /img
@bot.message_handler(commands=["img", "pic", "image"])
def generate_image(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message, "Напиши, что нарисовать. Пример:\n/img кот на скейте"
        )
        return

    user_prompt = parts[1]
    bot.reply_to(message, "🎨 Рисую для чата, подождите...")

    encoded = urllib.parse.quote(user_prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded}"

    try:
        bot.send_photo(
            message.chat.id,
            image_url,
            caption=f"✨ {user_prompt}",
            reply_to_message_id=message.message_id,
        )
    except Exception as err:
        bot.reply_to(message, f"Ошибка загрузки картинки: {err}")


# 4. Распознавание ФОТО через Gemini
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    bot.send_chat_action(message.chat.id, "typing")

    caption = message.caption or "Опиши подробно, что изображено на этом фото."
    caption = caption.replace(f"@{bot_info.username}", "").strip()

    # Скачиваем самое качественное фото
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        img_bytes = requests.get(file_url).content
        b64_image = base64.b64encode(img_bytes).decode("utf-8")
    except Exception:
        bot.reply_to(message, "Не удалось загрузить фото из Телеграма.")
        return

    # Формируем запрос с картинкой
    parts_data = [
        {"text": caption},
        {"inlineData": {"mimeType": "image/jpeg", "data": b64_image}},
    ]
    data = {"contents": [{"role": "user", "parts": parts_data}]}

    # Отправляем в Gemini
    for model in MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            res = requests.post(url, json=data, timeout=15).json()

            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                bot.reply_to(message, answer)

                # Сохраняем текстовое описание в память диалога
                mem = get_or_reset_memory(message.chat.id)
                mem.append(
                    {"role": "user", "parts": [{"text": f"[Фото]: {caption}"}]}
                )
                mem.append({"role": "model", "parts": [{"text": answer}]})
                return
        except Exception:
            continue

    bot.reply_to(message, "Не удалось обработать фото через Google.")


# 5. Текстовые ответы с памятью разговора
@bot.message_handler(func=lambda msg: True)
def answer_text(message):
    if not message.text:
        return

    clean_text = message.text.replace(f"@{bot_info.username}", "")
    if clean_text.startswith("/ask") or clean_text.startswith("/ai"):
        clean_text = clean_text.split(maxsplit=1)[-1]
    clean_text = clean_text.strip()

    if not clean_text:
        return

    bot.send_chat_action(message.chat.id, "typing")

    # Достаем контекст диалога
    history = get_or_reset_memory(message.chat.id)

    # Добавляем новый вопрос
    new_user_message = {"role": "user", "parts": [{"text": clean_text}]}
    contents = history + [new_user_message]

    data = {"contents": contents}

    for model in MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            response = requests.post(url, json=data, timeout=12)
            res = response.json()

            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                bot.reply_to(message, answer)

                # Запоминаем вопрос и ответ в истории диалога
                history.append(new_user_message)
                history.append(
                    {"role": "model", "parts": [{"text": answer}]}
                )

                # Храним не больше 20 последних сообщений, чтобы не перегружать запрос
                if len(history) > 20:
                    chat_memory[message.chat.id]["messages"] = history[-20:]
                return
        except Exception:
            continue

    bot.reply_to(
        message, "Сервера Google сейчас заняты, повтори через пару секунд!"
    )


def run_telebot():
    bot.infinity_polling(timeout=20, long_polling_timeout=10)


if __name__ == "__main__":
    threading.Thread(target=run_telebot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
