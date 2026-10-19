import os
import re
import sys
import glob
import json
import shutil
import asyncio
import sqlite3
import subprocess
from pathlib import Path

import aiohttp
from aiohttp import web
import yt_dlp
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message
)

# ---------------- Environment & Settings ----------------
API_ID = int(os.environ.get("API_ID", "39912374"))
API_HASH = os.environ.get("API_HASH", "6f21928259361f124863df3912790359")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8972396687:AAFUr-W6sWuW4o1aJFwhEYG10D6NhGc4G84").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@sr_downloader_official").strip()

if not BOT_TOKEN or BOT_TOKEN == "এখানে_বট_টোকেন_দিন":
    print("❌ এরর: BOT_TOKEN পাওয়া যায়নি! Environment Variable-এ BOT_TOKEN সেট করুন।", flush=True)
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path("/tmp/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "bot_data.db"

# FFmpeg লোকেশন চিহ্নিতকরণ
FFMPEG_PATH = shutil.which("ffmpeg") or str(BASE_DIR / "ffmpeg")
if not os.path.exists(FFMPEG_PATH):
    FFMPEG_PATH = "ffmpeg"  # fallback

# কুকি সেটআপ (YouTube/Instagram বাইপাসের জন্য)
COOKIE_FILE = None
_env_cookies = os.environ.get("COOKIES", "").strip()
if _env_cookies:
    COOKIE_FILE = "/tmp/cookies.txt"
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(_env_cookies.replace("\\n", "\n") + "\n")
    print("🍪 কুকি লোড সম্পন্ন (Environment Variable থেকে)", flush=True)
elif (BASE_DIR / "cookies.txt").exists():
    COOKIE_FILE = str(BASE_DIR / "cookies.txt")
    print("🍪 কুকি লোড সম্পন্ন (cookies.txt ফাইল থেকে)", flush=True)

# ---------------- ডাটাবেস ইনিশিয়ালাইজেশন ----------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_cache (
                post_id INTEGER PRIMARY KEY,
                file_id TEXT,
                source_url TEXT,
                title TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                chat_id INTEGER,
                msg_id INTEGER,
                user_id INTEGER,
                reaction TEXT,
                PRIMARY KEY (chat_id, msg_id, user_id)
            )
        """)
        conn.commit()

init_db()

def save_cache(post_id: int, file_id: str, url: str = "", title: str = ""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO video_cache (post_id, file_id, source_url, title) VALUES (?, ?, ?, ?)",
            (post_id, file_id, url, title)
        )
        conn.commit()

def get_cached_file_id(post_id: int) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT file_id FROM video_cache WHERE post_id = ?", (post_id,))
        row = cur.fetchone()
        return row[0] if row else None

# ---------------- টেলিগ্রাম ক্লায়েন্ট ----------------
app = Client(
    "video_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(3)  # একসাথে সর্বোচ্চ ৩টি হেভি ডাউনলোড চলবে
pending_urls: dict[int, str] = {}
REACTIONS = [("like", "👍"), ("love", "❤️"), ("fire", "🔥")]

# ---------------- হেল্পার ফাংশন ----------------
def clean_text(text: str, limit: int = 150) -> str:
    text = re.sub(r"[*_`~\[\]()<>#]", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if text else "ভিডিও"

def build_channel_markup(download_url: str, counts: dict = None) -> InlineKeyboardMarkup:
    counts = counts or {}
    react_row = []
    for key, emoji in REACTIONS:
        n = counts.get(key, 0)
        btn_text = f"{emoji} {n}" if n > 0 else emoji
        react_row.append(InlineKeyboardButton(btn_text, callback_data=f"react_{key}"))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 সরাসরি ফোনে ডাউনলোড করুন", url=download_url)],
        react_row
    ])

def get_video_meta(video_path: str):
    """FFprobe দিয়ে ভিডিওর রেজোলিউশন ও সময় বের করা"""
    width, height, duration = 1280, 720, 0
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=width,height", "-of", "json", video_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        data = json.loads(res.stdout)
        if "format" in data and "duration" in data["format"]:
            duration = int(float(data["format"]["duration"]))
        if "streams" in data and len(data["streams"]) > 0:
            for s in data["streams"]:
                if "width" in s and "height" in s:
                    width = int(s["width"])
                    height = int(s["height"])
                    break
    except Exception:
        pass
    return width, height, duration

def generate_thumbnail(video_path: str, uid: str) -> str | None:
    """ভিডিও থেকে দ্রুত কভার থাম্বনেইল তৈরি"""
    thumb_path = str(DOWNLOAD_DIR / f"thumb_{uid}.jpg")
    try:
        cmd = [
            FFMPEG_PATH, "-ss", "00:00:01", "-i", video_path,
            "-vframes", "1", "-q:v", "2", "-y", thumb_path
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 100:
            return thumb_path
    except Exception:
        pass
    return None

def build_ydl_opts(out_template: str) -> dict:
    opts = {
        # অডিও ও ভিডিও মার্জ করে সেরা কোয়ালিটির MP4 ফাইল বানাবে
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "socket_timeout": 30,
        "geo_bypass": True,
        "ffmpeg_location": FFMPEG_PATH,
        # ব্রাউজার ইমিউলেশন ও বাইপাস
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "web"],
                "player_skip": ["js", "configs", "webpage"]
            }
        }
    }
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts

def run_download(url: str, uid: str):
    out_template = str(DOWNLOAD_DIR / f"{uid}.%(ext)s")
    opts = build_ydl_opts(out_template)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    
    files = glob.glob(str(DOWNLOAD_DIR / f"{uid}.*"))
    valid = [f for f in files if not f.endswith((".part", ".ytdl", ".jpg", ".webp"))]
    final_path = valid[0] if valid else None
    return info, final_path

def cleanup_files(uid: str):
    for f in glob.glob(str(DOWNLOAD_DIR / f"*{uid}*")):
        try:
            os.remove(f)
        except Exception:
            pass

async def is_member(client: Client, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(CHANNEL_ID, user_id)
        return member.status in (
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER
        )
    except Exception:
        return False

async def send_join_prompt(client: Client, chat_id: int, payload: str = ""):
    raw_channel = CHANNEL_ID.replace("@", "")
    channel_url = f"https://t.me/{raw_channel}"
    cb = f"verify|{payload}" if payload else "verify"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 আমাদের চ্যানেলে জয়েন করুন", url=channel_url)],
        [InlineKeyboardButton("✅ জয়েন করেছি, আনলক করুন", callback_data=cb)]
    ])
    await client.send_message(
        chat_id,
        "🔒 **ভিডিও ডাউনলোড করতে আমাদের চ্যানেলে জয়েন থাকা আবশ্যক!**\n\n"
        "👉 নিচের চ্যানেলে জয়েন করে **আনলক করুন** বাটনে চাপ দিন।",
        reply_markup=markup
    )

# ---------------- চ্যানেল ও ভিডিও ডেলিভারি ----------------
async def deliver_video(client: Client, chat_id: int, post_id: int):
    file_id = get_cached_file_id(post_id)
    if not file_id:
        try:
            msg = await client.get_messages(CHANNEL_ID, post_id)
            if msg and msg.caption_entities:
                for ent in msg.caption_entities:
                    if ent.url and ent.url.startswith("https://fid.local/"):
                        file_id = ent.url.replace("https://fid.local/", "")
                        break
        except Exception as e:
            print(f"⚠️ চ্যানেল মেসেজ রিড এরর: {e}", flush=True)

    if not file_id:
        await client.send_message(chat_id, "😔 দুঃখিত! ভিডিও ফাইলটি পাওয়া যায়নি। অনুগ্রহ করে লিংকটি পুনরায় পাঠিয়ে ডাউনলোড করুন।")
        return

    try:
        await client.send_video(
            chat_id=chat_id,
            video=file_id,
            caption=(
                "✅ **আপনার কাঙ্ক্ষিত ভিডিও প্রস্তুত!**\n\n"
                "📲 **সরাসরি ফোনে সেভ করার নিয়ম:**\n"
                "ভিডিওর ডানদিকের থ্রি-ডট (⋮) এ ক্লিক করে **'Save to Gallery'** অথবা **'Save to Downloads'** সিলেক্ট করুন।"
            ),
            supports_streaming=True
        )
    except Exception as e:
        print(f"❌ ডেলিভারি এরর: {e}", flush=True)
        await client.send_message(chat_id, "⚠️ ভিডিও পাঠাতে সমস্যা হয়েছে। অনুগ্রহ করে কিছুক্ষণ পর চেষ্টা করুন।")

async def post_to_channel(client: Client, info: dict, file_id: str, bot_username: str):
    title = clean_text(info.get("title", "অসাধারণ ভিডিও"), 120)
    extractor = clean_text(info.get("extractor_key") or "Social Media", 30)
    thumb_url = info.get("thumbnail")
    
    caption = (
        f"🎬 **{title}**\n\n"
        f"🌐 **সোর্স:** #{extractor}\n"
        f"⚡ **ডাউনলোড করতে নিচের লিংকে ক্লিক করুন:**\n"
        f"[\u200b](https://fid.local/{file_id})"
    )

    post = None
    if thumb_url:
        try:
            post = await client.send_photo(CHANNEL_ID, photo=thumb_url, caption=caption)
        except Exception:
            post = None

    if not post:
        post = await client.send_message(CHANNEL_ID, caption, disable_web_page_preview=True)

    save_cache(post.id, file_id, info.get("webpage_url", ""), title)
    markup = build_channel_markup(f"https://t.me/{bot_username}?start=v{post.id}")
    await client.edit_message_reply_markup(CHANNEL_ID, post.id, markup)

async def handle_link(client: Client, chat_id: int, url: str):
    uid = f"{chat_id}_{abs(hash(url)) % 1000000}"
    status_msg = await client.send_message(chat_id, "⚡ **ভিডিও প্রক্রিয়া করা হচ্ছে, দয়া করে কিছুক্ষণ অপেক্ষা করুন...**")

    async with DOWNLOAD_SEMAPHORE:
        try:
            info, path = await asyncio.to_thread(run_download, url, uid)
            if not path or not os.path.exists(path):
                raise RuntimeError("ফাইল ডাউনলোড করা সম্ভব হয়নি")

            file_size_mb = os.path.getsize(path) / (1024 * 1024)
            if file_size_mb > 1950:
                await status_msg.edit_text("❌ দুঃখিত, টেলিগ্রাম বটের মাধ্যমে ২ জিবির বেশি সাইজের ফাইল আপলোড সম্ভব নয়।")
                return

            await status_msg.edit_text("📤 **ভিডিও আপলোড হচ্ছে... সম্পূর্ণ হলে সরাসরি দেখতে পাবেন।**")

            width, height, duration = get_video_meta(path)
            thumb = generate_thumbnail(path, uid)
            title = clean_text(info.get("title", "ভিডিও"), 150)
            extractor = clean_text(info.get("extractor_key") or "Social Media", 30)

            channel_btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 আমাদের অফিশিয়াল চ্যানেল", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")]
            ])

            sent_msg = await client.send_video(
                chat_id=chat_id,
                video=path,
                thumb=thumb,
                duration=duration,
                width=width,
                height=height,
                caption=(
                    f"✨ **ভিডিও সফলভাবে ডাউনলোড হয়েছে!**\n\n"
                    f"🎬 **শিরোনাম:** {title}\n"
                    f"🌐 **সোর্স:** {extractor}\n\n"
                    f"📲 **ফোনের মেমোরিতে সেভ করতে:** ভিডিওর উপরে থাকা (⋮) আইকনে চাপ দিয়ে **'Save to Gallery'** বেছে নিন।"
                ),
                supports_streaming=True,
                reply_markup=channel_btn
            )

            bot_info = await client.get_me()
            try:
                await post_to_channel(client, info, sent_msg.video.file_id, bot_info.username)
            except Exception as pe:
                print(f"⚠️ চ্যানেলে পোস্ট ফেইল: {pe}", flush=True)

            await status_msg.delete()

        except Exception as e:
            print(f"❌ ডাউনলোড এরর [{url}]: {e}", flush=True)
            await status_msg.edit_text(
                "😔 দুঃখিত, এই ভিডিওটি সরাসরি প্রসেস করা সম্ভব হয়নি।\n"
                "💡 লিংকটি সঠিক কি না পরীক্ষা করুন অথবা কিছুক্ষণ পর চেষ্টা করুন।"
            )
        finally:
            cleanup_files(uid)

# ---------------- ইভেন্ট হ্যান্ডলার ----------------
@app.on_message(filters.private & filters.command("start"))
async def start_cmd(client: Client, message: Message):
    user = message.from_user
    if not user:
        return

    payload = message.command[1] if len(message.command) > 1 else ""

    if not await is_member(client, user.id):
        await send_join_prompt(client, message.chat.id, payload)
        return

    if payload.startswith("v") and payload[1:].isdigit():
        await deliver_video(client, message.chat.id, int(payload[1:]))
        return

    await message.reply_text(
        f"👋 **স্বাগতম, {user.first_name}!**\n\n"
        "🎬 যেকোনো প্ল্যাটফর্মের (YouTube, Facebook, Instagram, TikTok, Twitter ইত্যাদি) ভিডিওর লিংক আমাকে পাঠান।\n"
        "আমি দ্রুততম সময়ে সম্পূর্ণ অডিওসহ ভিডিও সরাসরি আপনাকে পাঠাব!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 চ্যানেল ভিজিট করুন", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")]
        ])
    )

@app.on_message(filters.private & filters.text & ~filters.command(["start"]))
async def message_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return

    match = re.search(r"https?://\S+", message.text or "")
    if not match:
        await message.reply_text("❌ অনুগ্রহ করে একটি সঠিক ভিডিও লিংক (URL) পাঠান।")
        return

    url = match.group(0)
    if not await is_member(client, user.id):
        pending_urls[user.id] = url
        await send_join_prompt(client, message.chat.id)
        return

    await handle_link(client, message.chat.id, url)

@app.on_callback_query(filters.regex(r"^verify"))
async def verify_callback(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    if not await is_member(client, uid):
        await cq.answer("❌ আপনি এখনো চ্যানেলে জয়েন করেননি! অনুগ্রহ করে আগে চ্যানেলে জয়েন করুন।", show_alert=True)
        return

    await cq.answer("✅ ভেরিফিকেশন সফল হয়েছে!")
    chat_id = cq.message.chat.id
    try:
        await cq.message.delete()
    except Exception:
        pass

    parts = cq.data.split("|", 1)
    payload = parts[1] if len(parts) > 1 else ""

    if payload.startswith("v") and payload[1:].isdigit():
        await deliver_video(client, chat_id, int(payload[1:]))
    elif uid in pending_urls:
        url = pending_urls.pop(uid)
        await handle_link(client, chat_id, url)
    else:
        await client.send_message(chat_id, "🚀 **আপনি সফলভাবে যুক্ত হয়েছেন! এখন যেকোনো ভিডিওর লিংক পাঠান।**")

@app.on_callback_query(filters.regex(r"^react_"))
async def react_callback(client: Client, cq: CallbackQuery):
    kind = cq.data.replace("react_", "")
    msg = cq.message
    if not msg or not msg.reply_markup:
        await cq.answer("ধন্যবাদ!")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT reaction FROM reactions WHERE chat_id = ? AND msg_id = ? AND user_id = ?",
                    (msg.chat.id, msg.id, cq.from_user.id))
        prev = cur.fetchone()

        if prev and prev[0] == kind:
            await cq.answer("আপনি ইতোমধ্যেই এই রিঅ্যাকশন দিয়েছেন! 👍")
            return

        conn.execute("INSERT OR REPLACE INTO reactions (chat_id, msg_id, user_id, reaction) VALUES (?, ?, ?, ?)",
                     (msg.chat.id, msg.id, cq.from_user.id, kind))
        conn.commit()

        # নতুন কাউন্ট নেওয়া
        cur.execute("SELECT reaction, COUNT(*) FROM reactions WHERE chat_id = ? AND msg_id = ? GROUP BY reaction",
                    (msg.chat.id, msg.id))
        counts = {r: count for r, count in cur.fetchall()}

    download_url = msg.reply_markup.inline_keyboard[0][0].url
    try:
        await msg.edit_reply_markup(build_channel_markup(download_url, counts))
    except Exception:
        pass
    await cq.answer("আপনার প্রতিক্রিয়া গ্রহণ করা হয়েছে! ❤️")

# ---------------- ওয়েব সার্ভার (Render Port Binding) ----------------
async def web_handler(request):
    return web.Response(text="Bot is running active 24/7!", status=200)

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app_web = web.Application()
    app_web.router.add_get("/", web_handler)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 ওয়েব সার্ভার পোর্ট {port}-এ চালু হয়েছে", flush=True)

async def main():
    await start_web_server()
    print("🤖 টেলিগ্রাম বট চালু হচ্ছে...", flush=True)
    await app.start()
    me = await app.get_me()
    print(f"✅ সফলভাবে লগইন হয়েছে: @{me.username}", flush=True)
    from pyrogram import idle
    await idle()
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
