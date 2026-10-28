import base64
import os
import threading
import time
import urllib.parse
from flask import Flask
import requests
import telebot

TELEGRAM_TOKEN = "9129090353:AAFOZU4NEVz1SzXow8JQe5CIx1a0sSam19I"
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")

MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
TIMEOUT_SECONDS = 600  # 10 минут памяти

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot_info = bot.get_me()
BOT_NAME = bot_info.username.lower()

chat_memory = {}
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive 24/7!"


def get_or_reset_memory(chat_id):
    now = time.time()
    if chat_id in chat_memory:
        if now - chat_memory[chat_id]["last_time"] > TIMEOUT_SECONDS:
            chat_memory[chat_id] = {"last_time": now, "messages": []}
    else:
        chat_memory[chat_id] = {"last_time": now, "messages": []}
    chat_memory[chat_id]["last_time"] = now
    return chat_memory[chat_id]["messages"]


# 1. История диалога
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


# 2. Очистить память
@bot.message_handler(commands=["clear", "reset"])
def clear_memory(message):
    chat_memory[message.chat.id] = {"last_time": time.time(), "messages": []}
    bot.reply_to(message, "🧹 Память диалога сброшена!")


# 3. Картинки (/img)
@bot.message_handler(commands=["img", "pic", "image"])
def generate_image(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message, "Напиши, что нарисовать. Пример:\n/img кот на скейте"
        )
        return

    user_prompt = parts[1]
    bot.reply_to(message, "🎨 Рисую, подожди немного...")
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


# 4. Распознавание ФОТО (в группе — только если тегнули или ответили боту)
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    caption = message.caption or ""
    is_mentioned = f"@{BOT_NAME}" in caption.lower()

    # В группе игнорируем чужие фото, если к боту не обратились
    if not is_private and not (is_reply_to_bot or is_mentioned):
        return

    bot.send_chat_action(message.chat.id, "typing")
    clean_caption = (
        caption.replace(f"@{bot_info.username}", "").replace(f"@{BOT_NAME}", "")
    ).strip()
    clean_caption = clean_caption or "Опиши подробно, что изображено на фото."

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        img_bytes = requests.get(file_url).content
        b64_image = base64.b64encode(img_bytes).decode("utf-8")
    except Exception:
        bot.reply_to(message, "Не удалось загрузить фото из Телеграма.")
        return

    parts_data = [
        {"text": clean_caption},
        {"inlineData": {"mimeType": "image/jpeg", "data": b64_image}},
    ]
    data = {"contents": [{"role": "user", "parts": parts_data}]}

    for model in MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            res = requests.post(url, json=data, timeout=15).json()

            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                bot.reply_to(message, answer)
                mem = get_or_reset_memory(message.chat.id)
                mem.append(
                    {
                        "role": "user",
                        "parts": [{"text": f"[Фото]: {clean_caption}"}],
                    }
                )
                mem.append({"role": "model", "parts": [{"text": answer}]})
                return
        except Exception:
            continue

    bot.reply_to(message, "Не удалось распознать фото через Google.")


# 5. Текстовые ответы (ТОЛЬКО НА РЕПЛАИ ИЛИ ТЕГИ В ГРУППЕ)
@bot.message_handler(func=lambda msg: True)
def answer_text(message):
    if not message.text:
        return

    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    is_mentioned = f"@{BOT_NAME}" in message.text.lower()
    is_command = message.text.startswith("/ask") or message.text.startswith(
        "/ai"
    )

    # 🛑 В ГРУППЕ МОЛЧИМ, если это не ответ на сообщение бота и не тег!
    if not is_private and not (is_reply_to_bot or is_mentioned or is_command):
        return

    clean_text = (
        message.text.replace(f"@{bot_info.username}", "")
        .replace(f"@{BOT_NAME}", "")
        .strip()
    )
    if clean_text.startswith("/ask") or clean_text.startswith("/ai"):
        clean_text = clean_text.split(maxsplit=1)[-1].strip()

    if not clean_text:
        bot.reply_to(message, "Я тут! Чем помочь?")
        return

    bot.send_chat_action(message.chat.id, "typing")
    history = get_or_reset_memory(message.chat.id)

    new_user_message = {"role": "user", "parts": [{"text": clean_text}]}
    data = {"contents": history + [new_user_message]}

    for model in MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            response = requests.post(url, json=data, timeout=10)
            res = response.json()

            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                bot.reply_to(message, answer)

                history.append(new_user_message)
                history.append(
                    {"role": "model", "parts": [{"text": answer}]}
                )
                if len(history) > 20:
                    chat_memory[message.chat.id]["messages"] = history[-20:]
                return
        except Exception:
            continue

    bot.reply_to(message, "Сервера Google заняты, попробуй еще раз!")


def run_telebot():
    bot.infinity_polling(timeout=20, long_polling_timeout=10)


if __name__ == "__main__":
    threading.Thread(target=run_telebot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
