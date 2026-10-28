import base64
import os
import random
import threading
import time
import urllib.parse
from flask import Flask
import requests
import telebot

TELEGRAM_TOKEN = "9129090353:AAFOZU4NEVz1SzXow8JQe5CIx1a0sSam19I"
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")

TEXT_MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
TIMEOUT_SECONDS = 600
SLOW_MODE_SECONDS = 6

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot_info = bot.get_me()
BOT_NAME = bot_info.username.lower()

chat_memory = {}
last_send_time = {}

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive 24/7!"


def wait_slow_mode(chat_id):
    now = time.time()
    last = last_send_time.get(chat_id, 0)
    diff = now - last
    if diff < SLOW_MODE_SECONDS:
        time.sleep(SLOW_MODE_SECONDS - diff)
    last_send_time[chat_id] = time.time()


def get_or_reset_memory(chat_id):
    now = time.time()
    if chat_id in chat_memory:
        if now - chat_memory[chat_id]["last_time"] > TIMEOUT_SECONDS:
            chat_memory[chat_id] = {"last_time": now, "messages": []}
    else:
        chat_memory[chat_id] = {"last_time": now, "messages": []}
    chat_memory[chat_id]["last_time"] = now
    return chat_memory[chat_id]["messages"]


# 🎨 Генерация артов через FLUX
def generate_free_art(chat_id, prompt, reply_to_id=None):
    wait_slow_mode(chat_id)
    bot.send_message(chat_id, "🎨 Рисую арт, подожди секунд 5...")

    encoded = urllib.parse.quote(prompt)
    seed = random.randint(1000, 999999)
    image_url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&nologo=true&seed={seed}&width=1024&height=1024"

    try:
        wait_slow_mode(chat_id)
        bot.send_photo(
            chat_id,
            image_url,
            caption=f"✨ {prompt}",
            reply_to_message_id=reply_to_id,
        )
    except Exception as err:
        bot.send_message(chat_id, f"Ошибка создания картинки: {err}")


# Вспомогательная функция расшифровки аудио
def transcribe_audio_bytes(b64_audio):
    parts_data = [
        {
            "text": (
                "Дословно переведи это голосовое сообщение в текст на русском"
                " языке. Напиши ТОЛЬКО текст того, что сказал человек, без"
                " лишних комментариев, приветствий и пояснений."
            )
        },
        {"inlineData": {"mimeType": "audio/ogg", "data": b64_audio}},
    ]
    data = {"contents": [{"role": "user", "parts": parts_data}]}

    for model in TEXT_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            res = requests.post(url, json=data, timeout=25).json()
            if "candidates" in res:
                return res["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            continue
    return None


# 1. Команды /img, /history, /clear
@bot.message_handler(commands=["img", "pic", "image", "art"])
def cmd_image(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "Напиши, что нарисовать. Пример:\n/img желтый спорткар ночью",
        )
        return
    generate_free_art(message.chat.id, parts[1], message.message_id)


@bot.message_handler(commands=["history"])
def show_history(message):
    wait_slow_mode(message.chat.id)
    messages = get_or_reset_memory(message.chat.id)
    if not messages:
        bot.reply_to(
            message, "Память пуста или прошло больше 10 минут молчания."
        )
        return

    text_history = "📜 **История диалога:**\n\n"
    for item in messages:
        role = "👤" if item["role"] == "user" else "🤖"
        part_text = item["parts"][0].get("text", "[Медиа/Файл]")
        text_history += f"{role} {part_text[:120]}\n"
    bot.reply_to(message, text_history)


@bot.message_handler(commands=["clear", "reset"])
def clear_memory(message):
    wait_slow_mode(message.chat.id)
    chat_memory[message.chat.id] = {"last_time": time.time(), "messages": []}
    bot.reply_to(message, "🧹 Память диалога очищена!")


# 2. Обработка входящих ГОЛОСОВЫХ (напрямую боту)
@bot.message_handler(content_types=["voice", "audio"])
def handle_voice(message):
    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )

    if not is_private and not is_reply_to_bot:
        return

    bot.send_chat_action(message.chat.id, "typing")
    user = message.from_user
    username_tag = f"@{user.username}" if user.username else user.first_name

    try:
        voice = message.voice or message.audio
        file_info = bot.get_file(voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        audio_bytes = requests.get(file_url).content
        b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception:
        bot.reply_to(message, "Не удалось загрузить голосовое сообщение.")
        return

    answer = transcribe_audio_bytes(b64_audio)
    if answer:
        wait_slow_mode(message.chat.id)
        bot.reply_to(message, f"{username_tag} {answer}")
    else:
        bot.reply_to(message, "Не удалось распознать голосовое сообщение.")


# 3. Обработка ФАЙЛОВ и ДОКУМЕНТОВ
@bot.message_handler(content_types=["document"])
def handle_document(message):
    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    caption = message.caption or ""
    is_gemini = "gemini" in caption.lower() or "гемини" in caption.lower()
    is_mentioned = f"@{BOT_NAME}" in caption.lower()

    if not is_private and not (is_reply_to_bot or is_mentioned or is_gemini):
        return

    bot.send_chat_action(message.chat.id, "typing")
    user = message.from_user
    username_tag = f"@{user.username}" if user.username else user.first_name

    try:
        file_info = bot.get_file(message.document.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        doc_bytes = requests.get(file_url).content
        b64_doc = base64.b64encode(doc_bytes).decode("utf-8")
        mime = message.document.mime_type or "application/pdf"
    except Exception:
        bot.reply_to(message, "Не удалось прочитать файл.")
        return

    clean_caption = (
        caption.replace(f"@{bot_info.username}", "")
        .replace(f"@{BOT_NAME}", "")
        .strip()
    )
    prompt = clean_caption or "Изучи этот документ и подробно ответь, о чем он."

    parts_data = [
        {"text": f"Пользователь {username_tag} отправил файл. Вопрос: {prompt}"},
        {"inlineData": {"mimeType": mime, "data": b64_doc}},
    ]
    data = {"contents": [{"role": "user", "parts": parts_data}]}

    for model in TEXT_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            res = requests.post(url, json=data, timeout=30).json()
            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                wait_slow_mode(message.chat.id)
                bot.reply_to(message, f"{username_tag} {answer}")
                return
        except Exception:
            continue
    bot.reply_to(message, "Не удалось обработать документ.")


# 4. Распознавание ФОТО
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    caption = message.caption or ""
    is_gemini = "gemini" in caption.lower() or "гемини" in caption.lower()
    is_mentioned = f"@{BOT_NAME}" in caption.lower()

    if not is_private and not (is_reply_to_bot or is_mentioned or is_gemini):
        return

    bot.send_chat_action(message.chat.id, "typing")
    user = message.from_user
    username_tag = f"@{user.username}" if user.username else user.first_name

    clean_caption = (
        caption.replace(f"@{bot_info.username}", "")
        .replace(f"@{BOT_NAME}", "")
        .strip()
    )
    clean_caption = clean_caption or "Опиши подробно, что на фото."

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        img_bytes = requests.get(file_url).content
        b64_image = base64.b64encode(img_bytes).decode("utf-8")
    except Exception:
        bot.reply_to(message, "Не удалось загрузить фото.")
        return

    parts_data = [
        {"text": f"Пользователь {username_tag} отправил фото: {clean_caption}"},
        {"inlineData": {"mimeType": "image/jpeg", "data": b64_image}},
    ]
    data = {"contents": [{"role": "user", "parts": parts_data}]}

    for model in TEXT_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            res = requests.post(url, json=data, timeout=15).json()
            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                wait_slow_mode(message.chat.id)
                bot.reply_to(message, f"{username_tag} {answer}")
                return
        except Exception:
            continue
    bot.reply_to(message, "Ошибка распознавания фото.")


# 5. ТЕКСТОВЫЕ ОТВЕТЫ + РАСШИФРОВКА ГС ДРУГА ПО РЕПЛАЮ
@bot.message_handler(func=lambda msg: True)
def answer_text(message):
    if not message.text:
        return

    user = message.from_user
    username_tag = f"@{user.username}" if user.username else user.first_name

    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    is_mentioned = f"@{BOT_NAME}" in message.text.lower()
    is_gemini_word = (
        "gemini" in message.text.lower() or "гемини" in message.text.lower()
    )
    is_command = message.text.startswith("/ask") or message.text.startswith(
        "/ai"
    )

    # 🎙️ ФИШКА: РАСШИФРОВКА ГОЛОСОВОГО СООБЩЕНИЯ ДРУГА ПО РЕПЛАЮ
    if message.reply_to_message and (
        message.reply_to_message.voice or message.reply_to_message.audio
    ):
        triggers = [
            "расшифруй",
            "текст",
            "в текст",
            "что сказал",
            "переведи",
            "gemini",
            "гемини",
        ]
        if any(tr in message.text.lower() for tr in triggers):
            bot.send_chat_action(message.chat.id, "typing")
            target_voice = (
                message.reply_to_message.voice or message.reply_to_message.audio
            )
            friend = message.reply_to_message.from_user
            friend_tag = (
                f"@{friend.username}" if friend.username else friend.first_name
            )

            try:
                file_info = bot.get_file(target_voice.file_id)
                file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
                audio_bytes = requests.get(file_url).content
                b64_audio = base64.b64encode(audio_bytes).decode("utf-8")

                text_result = transcribe_audio_bytes(b64_audio)
                if text_result:
                    wait_slow_mode(message.chat.id)
                    reply_text = f"🎤 **Расшифровка для {friend_tag}:**\n\n«{text_result.strip()}»"
                    bot.reply_to(message.reply_to_message, reply_text)
                    return
            except Exception:
                bot.reply_to(
                    message, "Не удалось прослушать голосовое друга 😢"
                )
                return

    # В группе отвечаем, если это обращение к боту
    if not is_private and not (
        is_reply_to_bot or is_mentioned or is_command or is_gemini_word
    ):
        return

    clean_text = (
        message.text.replace(f"@{bot_info.username}", "")
        .replace(f"@{BOT_NAME}", "")
        .strip()
    )
    if clean_text.startswith("/ask") or clean_text.startswith("/ai"):
        clean_text = clean_text.split(maxsplit=1)[-1].strip()

    if not clean_text:
        bot.reply_to(message, f"{username_tag} Я тут! Чем помочь?")
        return

    # Генерация картинки текстом
    lower = clean_text.lower()
    draw_triggers = [
        "нарисуй мне",
        "нарисуй",
        "сгенерируй картинку",
        "сгенерируй фото",
        "сгенерируй арт",
        "сгенерируй",
        "сделай картинку",
        "сделай фото",
        "изобрази",
        "создай фото",
        "создай арт",
        "draw",
    ]
    for trigger in draw_triggers:
        if lower.startswith(trigger):
            art_prompt = clean_text[len(trigger) :].strip(" :,-")
            if art_prompt:
                generate_free_art(
                    message.chat.id, art_prompt, message.message_id
                )
                return

    bot.send_chat_action(message.chat.id, "typing")
    history = get_or_reset_memory(message.chat.id)

    tagged_message = (
        f"Пользователь {username_tag} ({user.first_name}) пишет: {clean_text}"
    )
    new_user_message = {"role": "user", "parts": [{"text": tagged_message}]}
    data = {"contents": history + [new_user_message]}

    for model in TEXT_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            response = requests.post(url, json=data, timeout=12)
            res = response.json()

            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                wait_slow_mode(message.chat.id)
                clean_reply = f"{username_tag} {answer}"
                bot.reply_to(message, clean_reply)

                history.append(new_user_message)
                history.append(
                    {
                        "role": "model",
                        "parts": [{"text": f"Ответ для {username_tag}: {answer}"}],
                    }
                )
                if len(history) > 20:
                    chat_memory[message.chat.id]["messages"] = history[-20:]
                return
        except Exception:
            continue

    bot.reply_to(message, f"{username_tag} Сервера Google заняты, повтори!")


def run_telebot():
    bot.infinity_polling(timeout=20, long_polling_timeout=10)


if __name__ == "__main__":
    threading.Thread(target=run_telebot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
