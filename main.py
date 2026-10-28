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
memory, last_send, sessions, last_media = {}, {}, {}, {}

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


def ask_ai(prompt, mime=None, b64=None, timeout=25, custom_contents=None):
    if custom_contents:
        data = {
            "systemInstruction": {"parts": [{"text": MATH_RULE}]},
            "contents": custom_contents,
        }
    else:
        parts = [{"text": prompt}]
        if mime and b64:
            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        data = {
            "systemInstruction": {"parts": [{"text": MATH_RULE}]},
            "contents": [{"role": "user", "parts": parts}],
        }
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
    return "Сервера Google заняты, попробуй позже!"


def download_b64(fid):
    f = bot.get_file(fid)
    if f.file_size and f.file_size > 20 * 1024 * 1024:
        return None
    b = requests.get(
        f"https://api.telegram.org/file/bot{TOKEN}/{f.file_path}"
    ).content
    return base64.b64encode(b).decode("utf-8")


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
    bot.reply_to(m, "🧹 Память диалога очищена!")


@bot.message_handler(content_types=["voice", "audio"])
def on_voice(m):
    if m.chat.type != "private" and not (
        m.reply_to_message and m.reply_to_message.from_user.id == bot.get_me().id
    ):
        return
    bot.send_chat_action(m.chat.id, "typing")
    try:
        b64 = download_b64((m.voice or m.audio).file_id)
        ans = ask_ai(
            "Дословно переведи голосовое в текст без лишних слов.",
            "audio/ogg",
            b64,
            25,
        )
        wait_slow(m.chat.id)
        bot.reply_to(
            m, f"@{m.from_user.username or m.from_user.first_name} {ans}"
        )
    except:
        bot.reply_to(m, "Не удалось прослушать аудио.")


@bot.message_handler(
    content_types=["photo", "video", "video_note", "document"]
)
def on_media(m):
    cid = m.chat.id
    fid, mime = None, "image/jpeg"
    if m.photo:
        fid, mime = m.photo[-1].file_id, "image/jpeg"
    elif m.video:
        fid, mime = m.video.file_id, "video/mp4"
    elif m.video_note:
        fid, mime = m.video_note.file_id, "video/mp4"
    elif m.document:
        fid, mime = m.document.file_id, (
            m.document.mime_type or "application/pdf"
        )

    last_media[cid] = {"id": fid, "mime": mime, "t": time.time()}

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
    b64 = download_b64(fid)
    if not b64:
        return bot.reply_to(m, "Файл весит больше 20 МБ или не скачался.")

    p = m.caption or (
        "Опиши что на видео"
        if "video" in mime
        else "Опиши подробно или реши задачу."
    )
    ans = ask_ai(f"{u_tag}: {p}", mime, b64, 35)
    wait_slow(cid)
    bot.reply_to(m, f"{u_tag} {ans}")
    sessions[cid] = {"u": m.from_user.id, "exp": time.time() + 90}


@bot.message_handler(func=lambda m: True)
def on_text(m):
    if not m.text:
        return
    cid, uid, txt, now = m.chat.id, m.from_user.id, m.text, time.time()
    u_tag = f"@{m.from_user.username or m.from_user.first_name}"
    low = txt.lower()

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
            b64 = download_b64(
                (m.reply_to_message.voice or m.reply_to_message.audio).file_id
            )
            ans = ask_ai(
                "Дословно переведи аудио в текст без комментариев.",
                "audio/ogg",
                b64,
                25,
            )
            wait_slow(cid)
            fr = m.reply_to_message.from_user
            bot.reply_to(
                m.reply_to_message,
                f"🎤 Расшифровка для @{fr.username or fr.first_name}:\n\n«{ans.strip()}»",
            )
            return
        except:
            return bot.reply_to(m, "Ошибка при расшифровке.")

    rep_media = None
    if m.reply_to_message:
        rm = m.reply_to_message
        if rm.photo:
            rep_media = (rm.photo[-1].file_id, "image/jpeg")
        elif rm.video:
            rep_media = (rm.video.file_id, "video/mp4")
        elif rm.video_note:
            rep_media = (rm.video_note.file_id, "video/mp4")
        elif rm.document:
            rep_media = (
                rm.document.file_id,
                rm.document.mime_type or "application/pdf",
            )

    in_sess = False
    sess = sessions.get(cid)
    if sess and now < sess["exp"]:
        if (
            m.reply_to_message
            and m.reply_to_message.from_user.id != bot.get_me().id
        ):
            sessions.pop(cid, None)
        elif any(
            x in low
            for x in [
                "пацаны",
                "парни",
                "го в ",
                "гоу в ",
                "илья",
                "серёга",
                "женя",
            ]
        ):
            sessions.pop(cid, None)
        else:
            in_sess = True
    else:
        sessions.pop(cid, None)

    if in_sess and any(
        x in low for x in ["отбой", "хватит", "молчи", "стоп", "пока", "спасибо"]
    ):
        sessions.pop(cid, None)
        return bot.reply_to(m, f"{u_tag} Понял, не мешаю!")

    is_call = (
        m.chat.type == "private"
        or in_sess
        or any(x in low for x in ["gemini", "гемини", "геминай"])
        or f"@{bot_name}" in low
        or m.text.startswith(("/ask", "/ai"))
        or (
            m.reply_to_message
            and m.reply_to_message.from_user.id == bot.get_me().id
        )
        or bool(rep_media)
    )
    if not is_call:
        return

    for x in ["gemini", "гемини", "геминай", f"@{bot_name}", "/ask", "/ai"]:
        txt = re.sub(re.escape(x), "", txt, flags=re.IGNORECASE).strip(
            " ,:.-?!"
        )

    for trig in [
        "нарисуй",
        "сгенерируй",
        "сделай картинку",
        "сделай фото",
        "изобрази",
        "создай фото",
        "создай арт",
        "draw",
    ]:
        if low.startswith(trig):
            prompt = txt[len(trig) :].strip(" :,-")
            if prompt:
                return send_art(cid, prompt, m.message_id)

    target_media = rep_media
    if not target_media and any(
        x in low
        for x in [
            "фото",
            "фотк",
            "видео",
            "кружоч",
            "картинк",
            "выше",
            "пример",
            "задач",
        ]
    ):
        lm = last_media.get(cid)
        if lm and (now - lm["t"] < 1200):
            target_media = (lm["id"], lm["mime"])

    if target_media:
        bot.send_chat_action(cid, "typing")
        b64 = download_b64(target_media[0])
        if b64:
            ans = ask_ai(
                f"{u_tag}: {txt or 'Разбери этот файл / фото / видео.'}",
                target_media[1],
                b64,
                35,
            )
            wait_slow(cid)
            bot.reply_to(m, f"{u_tag} {ans}")
            sessions[cid] = {"u": uid, "exp": now + 90}
            return

    if not txt:
        return bot.reply_to(m, f"{u_tag} Я тут! Чем помочь?")

    bot.send_chat_action(cid, "typing")
    mem = memory.get(cid, {})
    if now - mem.get("t", 0) > 600:
        mem = {"t": now, "msgs": []}
    mem["t"] = now
    hist = mem.get("msgs", [])

    new_msg = {"role": "user", "parts": [{"text": f"{u_tag}: {txt}"}]}
    ans = ask_ai(txt, custom_contents=hist + [new_msg])

    wait_slow(cid)
    bot.reply_to(m, f"{u_tag} {ans}")
    sessions[cid] = {"u": uid, "exp": now + 90}

    hist.append(new_msg)
    hist.append(
        {"role": "model", "parts": [{"text": f"Ответ для {u_tag}: {ans}"}]}
    )
    mem["msgs"] = hist[-20:]
    memory[cid] = mem


if __name__ == "__main__":
    threading.Thread(
        target=lambda: bot.infinity_polling(
            timeout=20, long_polling_timeout=10
        ),
        daemon=True,
    ).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
