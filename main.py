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

TOKEN = "9129090353:AAFOZU4NEVz1SzXow8JQe5CIx1a0sSam19I"
KEY = os.environ.get("GEMINI_KEY", "")
MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
MATH_RULE = (
    "Никогда не используй LaTeX ($, $$, \\frac, \\sqrt). Пиши формулы обычным"
    " текстом: x², √x, ±, ÷, ×, (a)/(b)."
)

bot = telebot.TeleBot(TOKEN)
bot_name = bot.get_me().username.lower()
memory, last_send, sessions, last_photo, last_video = {}, {}, {}, {}, {}

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive 24/7!"


def clean_latex(t):
    if not t:
        return ""
    t = re.sub(r"\\frac\{([^}]+)\}\{([^}]+)\}", r"(\1)/(\2)", t)
    t = re.sub(r"\\sqrt\{([^}]+)\}", r"√(\1)", t)
    for k, v in [
        ("\\cdot", "·"),
        ("\\times", "×"),
        ("\\pm", "±"),
        ("\\neq", "≠"),
        ("\\le", "≤"),
        ("\\ge", "≥"),
        ("\\pi", "π"),
        ("$$", ""),
        ("$", ""),
    ]:
        t = t.replace(k, v)
    return (
        t.replace(r"\[", "")
        .replace(r"\]", "")
        .replace(r"\(", "")
        .replace(r"\)", "")
    )


def wait_slow(cid):
    d = time.time() - last_send.get(cid, 0)
    if d < 6:
        time.sleep(6 - d)
    last_send[cid] = time.time()


def ask_gemini(data, timeout=25):
    for m in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={KEY}",
                json=data,
                timeout=timeout,
            ).json()
            if "candidates" in r:
                return clean_latex(
                    r["candidates"][0]["content"]["parts"][0]["text"]
                )
        except:
            pass
    return "Сервера Google заняты, попробуй еще раз!"


def send_art(cid, prompt, rid=None):
    wait_slow(cid)
    bot.send_message(chat_id=cid, text="🎨 Рисую арт (без логотипов), подожди...")
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?model=flux&nologo=true&seed={random.randint(1,999999)}&width=1024&height=1024"
    try:
        wait_slow(cid)
        bot.send_photo(
            cid, url, caption=f"✨ {prompt}", reply_to_message_id=rid
        )
    except Exception as e:
        bot.send_message(chat_id=cid, text=f"Ошибка: {e}")


def process_video_analysis(file_id, prompt, u_tag, cid, msg_id):
    """Скачивает видео/кружочек и анализирует через Gemini"""
    bot.send_chat_action(cid, "record_video")
    try:
        f_info = bot.get_file(file_id)
        if f_info.file_size and f_info.file_size > 20 * 1024 * 1024:
            bot.send_message(
                cid,
                f"{u_tag} Видео весит больше 20 МБ! Телеграм не дает боту"
                " скачивать такие тяжелые файлы.",
                reply_to_message_id=msg_id,
            )
            return
        f_bytes = requests.get(
            f"https://api.telegram.org/file/bot{TOKEN}/{f_info.file_path}"
        ).content
        b64 = base64.b64encode(f_bytes).decode("utf-8")
        d = {
            "systemInstruction": {"parts": [{"text": MATH_RULE}]},
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": f"{u_tag}: {prompt or 'Опиши подробно, что происходит на этом видео/кружочке.'}"},
                    {"inlineData": {"mimeType": "video/mp4", "data": b64}},
                ],
            }],
        }
        ans = ask_gemini(d, 40)
        wait_slow(cid)
        bot.send_message(
            cid, f"{u_tag} {ans}", reply_to_message_id=msg_id
        )
        sessions[cid] = {"u": msg_id, "exp": time.time() + 90}
    except Exception as e:
        bot.send_message(
            cid,
            f"{u_tag} Не удалось обработать видео: {e}",
            reply_to_message_id=msg_id,
        )


@bot.message_handler(commands=["img", "pic", "image", "art"])
def cmd_img(m):
    p = m.text.split(maxsplit=1)
    if len(p) > 1:
        send_art(m.chat.id, p[1], m.message_id)
    else:
        bot.reply_to(m, "Напиши, что нарисовать. Пример: /img кот")


@bot.message_handler(commands=["history"])
def cmd_hist(m):
    h = memory.get(m.chat.id, {}).get("msgs", [])
    if not h:
        return bot.reply_to(m, "Память пуста или прошло 10 минут.")
    txt = "📜 История диалога:\n" + "\n".join([
        (
            f"{'👤' if x['role']=='user' else '🤖'}"
            f" {x['parts'][0].get('text','')[:100]}"
        )
        for x in h
    ])
    bot.reply_to(m, txt)


@bot.message_handler(commands=["clear", "reset"])
def cmd_clear(m):
    memory.pop(m.chat.id, None)
    sessions.pop(m.chat.id, None)
    bot.reply_to(m, "🧹 Память очищена!")


@bot.message_handler(content_types=["voice", "audio"])
def on_voice(m):
    if m.chat.type != "private" and not (
        m.reply_to_message and m.reply_to_message.from_user.id == bot.get_me().id
    ):
        return
    bot.send_chat_action(m.chat.id, "typing")
    try:
        f = requests.get(
            f"https://api.telegram.org/file/bot{TOKEN}/{bot.get_file((m.voice or m.audio).file_id).file_path}"
        ).content
        b64 = base64.b64encode(f).decode("utf-8")
        d = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Дословно переведи в текст без комментариев."},
                    {"inlineData": {"mimeType": "audio/ogg", "data": b64}},
                ],
            }]
        }
        ans = ask_gemini(d, 25)
        wait_slow(m.chat.id)
        bot.reply_to(
            m, f"@{m.from_user.username or m.from_user.first_name} {ans}"
        )
    except:
        bot.reply_to(m, "Не удалось прослушать аудио.")


# 📹 Обработка ВИДЕО и КРУЖОЧКОВ
@bot.message_handler(content_types=["video", "video_note"])
def on_video(m):
    cid = m.chat.id
    vid_id = m.video.file_id if m.video else m.video_note.file_id
    last_video[cid] = {"id": vid_id, "t": time.time()}

    cap = (m.caption or "").lower()
    is_call = (
        any(x in cap for x in ["gemini", "гемини"])
        or f"@{bot_name}" in cap
        or (
            m.reply_to_message
            and m.reply_to_message.from_user.id == bot.get_me().id
        )
    )
    if m.chat.type != "private" and not is_call:
        return

    u_tag = f"@{m.from_user.username or m.from_user.first_name}"
    prompt = m.caption or "Опиши подробно, что происходит на видео."
    process_video_analysis(vid_id, prompt, u_tag, cid, m.message_id)


@bot.message_handler(content_types=["photo", "document"])
def on_media(m):
    cid = m.chat.id
    if m.photo:
        last_photo[cid] = {"id": m.photo[-1].file_id, "t": time.time()}
    cap = (m.caption or "").lower()
    is_call = (
        any(x in cap for x in ["gemini", "гемини"])
        or f"@{bot_name}" in cap
        or (
            m.reply_to_message
            and m.reply_to_message.from_user.id == bot.get_me().id
        )
    )
    if m.chat.type != "private" and not is_call:
        return
    bot.send_chat_action(cid, "typing")
    u_tag = f"@{m.from_user.username or m.from_user.first_name}"
    try:
        fid = m.photo[-1].file_id if m.photo else m.document.file_id
        mime = (
            "image/jpeg" if m.photo else (m.document.mime_type or "application/pdf")
        )
        f = requests.get(
            f"https://api.telegram.org/file/bot{TOKEN}/{bot.get_file(fid).file_path}"
        ).content
        b64 = base64.b64encode(f).decode("utf-8")
        p = m.caption or "Опиши подробно или реши задачу."
        d = {
            "systemInstruction": {"parts": [{"text": MATH_RULE}]},
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": f"{u_tag}: {p}"},
                    {"inlineData": {"mimeType": mime, "data": b64}},
                ],
            }],
        }
        ans = ask_gemini(d, 30)
        wait_slow(cid)
        bot.reply_to(m, f"{u_tag} {ans}")
        sessions[cid] = {"u": m.from_user.id, "exp": time.time() + 90}
    except:
        bot.reply_to(m, "Не удалось обработать файл/фото.")


@bot.message_handler(func=lambda m: True)
def on_text(m):
    if not m.text:
        return
    cid, uid, txt, now = m.chat.id, m.from_user.id, m.text, time.time()
    u_tag = f"@{m.from_user.username or m.from_user.first_name}"
    low = txt.lower()

    # Расшифровка ГС
    if (
        m.reply_to_message
        and (m.reply_to_message.voice or m.reply_to_message.audio)
        and any(
            x in low
            for x in ["расшифруй", "текст", "что сказал", "гемини", "gemini"]
        )
    ):
        bot.send_chat_action(cid, "typing")
        try:
            f = requests.get(
                f"https://api.telegram.org/file/bot{TOKEN}/{bot.get_file((m.reply_to_message.voice or m.reply_to_message.audio).file_id).file_path}"
            ).content
            b64 = base64.b64encode(f).decode("utf-8")
            d = {
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"text": "Дословно переведи в текст без комментариев."},
                        {"inlineData": {"mimeType": "audio/ogg", "data": b64}},
                    ],
                }]
            }
