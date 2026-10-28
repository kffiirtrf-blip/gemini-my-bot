import os
import threading
import urllib.parse
from flask import Flask
import requests
import telebot

TELEGRAM_TOKEN = "9129090353:AAFOZU4NEVz1SzXow8JQe5CIx1a0sSam19I"
GEMINI_KEY = "AIzaSyAS-RJstICvgB6z7cEYmwys1C0ni47qdvQ"
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot_info = bot.get_me()

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive 24/7!"


# 1. Генерация картинок по команде /img
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


# 2. Текстовые ответы через Gemini
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
    data = {"contents": [{"parts": [{"text": clean_text}]}]}

    for model in MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            response = requests.post(url, json=data, timeout=10)
            res = response.json()

            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                bot.reply_to(message, answer)
                return
        except Exception:
            continue

    bot.reply_to(message, "Сервера Google сейчас заняты, повтори через пару секунд!")


def run_telebot():
    bot.infinity_polling(timeout=20, long_polling_timeout=10)


if __name__ == "__main__":
    threading.Thread(target=run_telebot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
