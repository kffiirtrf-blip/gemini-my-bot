import base64
import os
import random
import re
import threading
import time
import urllib.parse
from flask import Flask
import requests
import telebot

TELEGRAM_TOKEN = "9129090353:AAFOZU4NEVz1SzXow8JQe5CIx1a0sSam19I"
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")

TEXT_MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
TIMEOUT_SECONDS = 600  # Память контекста 10 минут
ACTIVE_SESSION_SECONDS = 90  # Окно живого диалога без слова "гемини" (90 сек)
SLOW_MODE_SECONDS = 6

MATH_INSTRUCTION = (
    "Ты умный помощник в Телеграме. Важнейшее правило оформления: НИКОГДА не используй "
    "LaTeX-разметку (никаких $, $$, \\frac, \\sqrt, \\cdot, \\text и подобных тегов)! "
    "Телеграм не поддерживает LaTeX. Записывай любые математические формулы понятным текстом "
    "с юникод-символами: ², ³, √, ±, ÷, ×, ≠, ≤, ≥, π, а дроби пиши через наклонную черту / со скобками."
)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot_info = bot.get_me()
BOT_NAME = bot_info.username.lower()

chat_memory = {}
last_send_time = {}
# Активные сессии живого разговора: {chat_id: {"user_id": int, "expires_at": float}}
active_sessions = {}

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive 24/7!"


def clean_latex(text):
    if not text:
        return ""
    text = re.sub(r"\\frac\{([^}]+)\}\{([^}]+)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\sqrt\{([^}]+)\}", r"√(\1)", text)
    text = re.sub(r"\\sqrt\[(\d+)\]\{([^}]+)\}", r"\1√(\2)", text)
    text = re.sub(r"\\text\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\mathbf\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\cdot", "·", text)
    text = re.sub(r"\\times", "×", text)
    text = re.sub(r"\\pm", "±", text)
    text = re.sub(r"\\approx", "≈", text)
    text = re.sub(r"\\neq", "≠", text)
    text = re.sub(r"\\le(?:q)?", "≤", text)
    text = re.sub(r"\\ge(?:q)?", "≥", text)
    text = re.sub(r"\\pi", "π", text)
    text = re.sub(r"\\infty", "∞", text)
    text = re.sub(r"\\degree", "°", text)
    text = text.replace("$$", "").replace("$", "")
    return (
        text.replace(r"\[", "")
        .replace(r"\]", "")
        .replace(r"\(", "")
        .replace(r"\)", "")
    )


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


def transcribe_audio_bytes(b64_audio):
    parts_data = [
        {
            "text": (
                "Дословно переведи это голосовое сообщение в текст на русском"
                " языке. Напиши ТОЛЬКО текст того, что сказал человек, без"
                " лишних комментариев и пояснений."
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
    active_sessions.pop(message.chat.id, None)
    bot.reply_to(message, "🧹 Память диалога очищена!")


# 2. Голосовые сообщения
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
        clean_ans = clean_latex(answer)
        bot.reply_to(message, f"{username_tag} {clean_ans}")
        # Открываем сессию диалога
        active_sessions[message.chat.id] = {
            "user_id": user.id,
            "expires_at": time.time() + ACTIVE_SESSION_SECONDS,
        }
    else:
        bot.reply_to(message, "Не удалось распознать голосовое сообщение.")


# 3. Документы
@bot.message_handler(content_types=["document"])
def handle_document(message):
    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    caption = (message.caption or "").lower()
    is_name = any(n in caption for n in ["gemini", "гемини", "геминай"])
    is_mentioned = f"@{BOT_NAME}" in caption

    if not is_private and not (is_reply_to_bot or is_mentioned or is_name):
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

    prompt = (
        message.caption or "Изучи этот документ и подробно ответь, о чем он."
    )
    parts_data = [
        {"text": f"Пользователь {username_tag} отправил файл. Вопрос: {prompt}"},
        {"inlineData": {"mimeType": mime, "data": b64_doc}},
    ]
    data = {
        "systemInstruction": {"parts": [{"text": MATH_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": parts_data}],
    }

    for model in TEXT_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            res = requests.post(url, json=data, timeout=30).json()
            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                wait_slow_mode(message.chat.id)
                clean_ans = clean_latex(answer)
                bot.reply_to(message, f"{username_tag} {clean_ans}")
                active_sessions[message.chat.id] = {
                    "user_id": user.id,
                    "expires_at": time.time() + ACTIVE_SESSION_SECONDS,
                }
                return
        except Exception:
            continue
    bot.reply_to(message, "Не удалось обработать документ.")


# 4. Фотографии
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    is_private = message.chat.type == "private"
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    caption = (message.caption or "").lower()
    is_name = any(n in caption for n in ["gemini", "гемини", "геминай"])
    is_mentioned = f"@{BOT_NAME}" in caption

    if not is_private and not (is_reply_to_bot or is_mentioned or is_name):
        return

    bot.send_chat_action(message.chat.id, "typing")
    user = message.from_user
    username_tag = f"@{user.username}" if user.username else user.first_name
    clean_caption = (
        message.caption or "Опиши подробно, что на фото / реши задачу."
    )

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
    data = {
        "systemInstruction": {"parts": [{"text": MATH_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": parts_data}],
    }

    for model in TEXT_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            res = requests.post(url, json=data, timeout=15).json()
            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                wait_slow_mode(message.chat.id)
                clean_ans = clean_latex(answer)
                bot.reply_to(message, f"{username_tag} {clean_ans}")
                active_sessions[message.chat.id] = {
                    "user_id": user.id,
                    "expires_at": time.time() + ACTIVE_SESSION_SECONDS,
                }
                return
        except Exception:
            continue
    bot.reply_to(message, "Ошибка распознавания фото.")


# 5. ТЕКСТОВЫЕ ОТВЕТЫ (УМНЫЙ РЕЖИМ ЖИВОГО ДИАЛОГА)
@bot.message_handler(func=lambda msg: True)
def answer_text(message):
    if not message.text:
        return

    user = message.from_user
    username_tag = f"@{user.username}" if user.username else user.first_name
    text_lower = message.text.lower()
    now = time.time()
    chat_id = message.chat.id
    is_private = message.chat.type == "private"

    # 🎙️ Расшифровка ГС друга
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
        if any(tr in text_lower for tr in triggers):
            bot.send_chat_action(chat_id, "typing")
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
                    wait_slow_mode(chat_id)
                    reply_text = f"🎤 **Расшифровка для {friend_tag}:**\n\n«{text_result.strip()}»"
                    bot.reply_to(message.reply_to_message, reply_text)
                    return
            except Exception:
                bot.reply_to(
                    message, "Не удалось прослушать голосовое друга 😢"
                )
                return

    # 🛑 ПРОВЕРКА: Активна ли сессия живого диалога в этом чате?
    session = active_sessions.get(chat_id)
    is_in_active_session = False

    if session:
        if now < session["expires_at"]:
            # Если человек ответил реплаем на сообщение ДРУГА (не бота) -> выходим из сессии, бот молчит!
            if (
                message.reply_to_message
                and message.reply_to_message.from_user.id != bot_info.id
            ):
                active_sessions.pop(chat_id, None)
                is_in_active_session = False
            # Если в сообщении явно обращаются к пацанам
            elif any(
                w in text_lower
                for w in [
                    "пацаны",
                    "парни",
                    "го в ",
                    "гоу в ",
                    "илья",
                    "серёга",
                    "женя",
                ]
            ):
                active_sessions.pop(chat_id, None)
                is_in_active_session = False
            else:
                is_in_active_session = True
        else:
            active_sessions.pop(chat_id, None)

    # Слова для быстрого завершения разговора с ботом
    stop_words = ["отбой", "хватит", "молчи", "стоп", "пока", "все спасибо"]
    if is_in_active_session and any(w in text_lower for w in stop_words):
        active_sessions.pop(chat_id, None)
        wait_slow_mode(chat_id)
        bot.reply_to(message, f"{username_tag} Понял, не мешаю разговору! 👍")
        return

    # Базовые триггеры вызова
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    is_mentioned = f"@{BOT_NAME}" in text_lower
    is_name_called = any(
        n in text_lower for n in ["gemini", "гемини", "геминай"]
    )
    is_command = message.text.startswith("/ask") or message.text.startswith(
        "/ai"
    )

    should_respond = (
        is_private
        or is_reply_to_bot
        or is_mentioned
        or is_command
        or is_name_called
        or is_in_active_session
    )

    if not is_private and not should_respond:
        return

    # Очищаем текст от имени бота
    clean_text = message.text
    for prefix in [
        f"@{bot_info.username}",
        f"@{BOT_NAME}",
        "/ask",
        "/ai",
        "gemini",
        "гемини",
        "геминай",
        "Gemini",
        "Гемини",
    ]:
        clean_text = re.sub(
            re.escape(prefix), "", clean_text, flags=re.IGNORECASE
        )
    clean_text = clean_text.strip(" ,:.-?!")

    if not clean_text:
        bot.reply_to(message, f"{username_tag} Я тут! Чем помочь?")
        return

    # Генерация картинок по тексту
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
                generate_free_art(chat_id, art_prompt, message.message_id)
                return

    bot.send_chat_action(chat_id, "typing")
    history = get_or_reset_memory(chat_id)

    tagged_message = (
        f"Пользователь {username_tag} ({user.first_name}) пишет: {clean_text}"
    )
    new_user_message = {"role": "user", "parts": [{"text": tagged_message}]}

    data = {
        "systemInstruction": {"parts": [{"text": MATH_INSTRUCTION}]},
        "contents": history + [new_user_message],
    }

    for model in TEXT_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
            response = requests.post(url, json=data, timeout=12)
            res = response.json()

            if "candidates" in res:
                answer = res["candidates"][0]["content"]["parts"][0]["text"]
                wait_slow_mode(chat_id)

                clean_ans = clean_latex(answer)
                clean_reply = f"{username_tag} {clean_ans}"
                bot.reply_to(message, clean_reply)

                # 🔥 ВКЛЮЧАЕМ/ПРОДЛЕВАЕМ ОКНО ДИАЛОГА НА 90 СЕКУНД:
                active_sessions[chat_id] = {
                    "user_id": user.id,
                    "expires_at": now + ACTIVE_SESSION_SECONDS,
                }

                history.append(new_user_message)
                history.append(
                    {
                        "role": "model",
                        "parts": [
                            {"text": f"Ответ для {username_tag}: {clean_ans}"}
                        ],
                    }
                )
                if len(history) > 20:
                    chat_memory[chat_id]["messages"] = history[-20:]
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
