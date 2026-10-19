import os
import re
import asyncio
import glob
import aiohttp
from aiohttp import web
import yt_dlp
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- টেলিগ্রাম বট কনফিগারেশন ---
API_ID = int(os.environ.get("API_ID", "39912374"))
API_HASH = os.environ.get("API_HASH", "6f21928259361f124863df3912790359")
# 👇 নিচের "এখানে_বট_টোকেন_দিন" লেখাটা মুছে BotFather থেকে পাওয়া টোকেন বসান
BOT_TOKEN = os.environ.get("BOT_TOKEN", "এখানে_বট_টোকেন_দিন")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@sr_downloader_official")

if BOT_TOKEN == "এখানে_বট_টোকেন_দিন":
    BOT_TOKEN = ""
_missing = [n for n, v in (("API_ID", API_ID), ("API_HASH", API_HASH), ("BOT_TOKEN", BOT_TOKEN)) if not v]
if _missing:
    raise SystemExit("❌ এই Environment Variable গুলো সেট করা নেই: " + ", ".join(_missing))
BOT_USERNAME_FALLBACK = "SRdownloader_bot"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# কুকি: Render-এ COOKIES নামে Environment Variable-এ পুরো cookies.txt এর লেখা পেস্ট করুন
# (পাবলিক GitHub-এ cookies.txt রাখবেন না — অ্যাকাউন্ট হ্যাক হতে পারে)।
COOKIE_FILE = None
_env_cookies = os.environ.get("COOKIES", "").strip()
if _env_cookies:
    COOKIE_FILE = "/tmp/cookies.txt"
    with open(COOKIE_FILE, "w") as _f:
        _f.write(_env_cookies.replace("\\n", "\n") + "\n")
    print("🍪 COOKIES env থেকে কুকি লোড হয়েছে", flush=True)
elif os.path.exists(os.path.join(BASE_DIR, "cookies.txt")) and os.path.getsize(os.path.join(BASE_DIR, "cookies.txt")) > 200:
    COOKIE_FILE = os.path.join(BASE_DIR, "cookies.txt")
    print("🍪 cookies.txt ফাইল থেকে কুকি লোড হয়েছে", flush=True)
else:
    print("⚠️ কোনো কুকি নেই — YouTube/Instagram কিছু ভিডিও ব্লক হতে পারে", flush=True)

# চ্যানেল পোস্টের ক্যাপশনে লুকানো লিংকে ভিডিওর file_id রাখা হয়,
# যাতে রিস্টার্ট হলেও পুরনো ডাউনলোড বাটন কাজ করে।
HIDDEN_PREFIX = "https://fid.local/"

VIDEO_CACHE = {}   # post_id -> file_id
pending_urls = {}  # user_id -> url (জয়েন ভেরিফাইয়ের পর প্রসেস হবে)

REACTIONS = [("like", "👍"), ("love", "❤️"), ("fire", "🔥")]
USER_REACTIONS = {}  # (chat_id, msg_id) -> {user_id: kind}
REACT_LOCK = asyncio.Lock()

app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

FRIENDLY_ERROR = (
    "😔 দুঃখিত, এই মুহূর্তে ভিডিওটি ডাউনলোড করা গেল না।\n\n"
    "💡 কিছুক্ষণ পর আবার চেষ্টা করুন, অথবা অন্য একটি লিংক পাঠান।"
)


# ---------------- সহায়ক ফাংশন ----------------
def clean_text(text, limit=150):
    text = re.sub(r"[*_`~\[\]()<>]", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if text else "আকর্ষণীয় ভিডিও"


def build_channel_markup(download_url, counts=None):
    """চ্যানেল পোস্টের বাটন: ডাউনলোড + কাউন্টসহ রিঅ্যাকশন।"""
    counts = counts or {}
    row = []
    for key, emoji in REACTIONS:
        n = counts.get(key, 0)
        row.append(InlineKeyboardButton(f"{emoji} {n}" if n else emoji, callback_data=f"react_{key}"))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 ডাউনলোড করুন", url=download_url)],
        row,
    ])


def build_ydl_opts(out_template):
    opts = {
        # ffmpeg ছাড়াই চলে: অডিও+ভিডিও একসাথে আছে এমন ফাইল, ২GB এর নিচে
        "format": (
            "best[ext=mp4][vcodec!=none][acodec!=none][filesize<1900M]/"
            "best[vcodec!=none][acodec!=none][filesize<1900M]/"
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
            "best[vcodec!=none][acodec!=none]/best"
        ),
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "socket_timeout": 30,
        "geo_bypass": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        },
        "extractor_args": {"youtube": {"player_client": ["default", "tv", "mweb", "web_safari"]}},
    }
    if COOKIE_FILE:
        opts["cookiefile"] = COOKIE_FILE
    return opts


def download_video(url, uid):
    """ব্লকিং ডাউনলোড — আলাদা থ্রেডে চলবে যাতে বট আটকে না যায়।"""
    out_template = os.path.join(DOWNLOAD_DIR, f"{uid}.%(ext)s")
    with yt_dlp.YoutubeDL(build_ydl_opts(out_template)) as ydl:
        info = ydl.extract_info(url, download=True)
    files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{uid}.*"))
    files = [f for f in files if not f.endswith((".part", ".ytdl"))]
    path = files[0] if files else None
    return info, path


def extract_direct_link(url):
    opts = build_ydl_opts(os.path.join(DOWNLOAD_DIR, "tmp.%(ext)s"))
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def cleanup(uid):
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{uid}.*")):
        try:
            os.remove(f)
        except Exception:
            pass


async def is_member(client, user_id):
    try:
        member = await client.get_chat_member(CHANNEL_ID, user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception as e:
        # UserNotParticipant হলে জয়েন করেনি; অন্য এরর হলেও লগে দেখা যাবে
        if "USER_NOT_PARTICIPANT" not in str(e).upper() and "UserNotParticipant" not in type(e).__name__:
            print(f"⚠️ মেম্বারশিপ চেক সমস্যা: {e}", flush=True)
        return False


async def send_join_prompt(client, chat_id, payload=""):
    channel_link = f"https://t.me/{CHANNEL_ID.replace('@', '')}"
    cb = f"verify|{payload}" if payload else "verify"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 চ্যানেলে জয়েন করুন", url=channel_link)],
        [InlineKeyboardButton("✅ জয়েন করেছি, ভেরিফাই করুন", callback_data=cb)],
    ])
    await client.send_message(
        chat_id,
        "🔒 **বটটি ব্যবহার করতে আগে আমাদের অফিশিয়াল চ্যানেলে জয়েন করতে হবে।**\n\n"
        "1️⃣ নিচের বাটনে চেপে চ্যানেলে জয়েন করুন\n"
        "2️⃣ তারপর **ভেরিফাই করুন** বাটনে চাপ দিন",
        reply_markup=markup,
    )


async def send_welcome(client, chat_id, name="বন্ধু"):
    await client.send_message(
        chat_id,
        f"✨ **স্বাগতম, {name}!**\n\n"
        "🎬 আমি একটি **অ্যাডভান্সড ইউনিভার্সাল ভিডিও ডাউনলোডার বট**।\n\n"
        "📥 যেকোনো প্ল্যাটফর্মের (YouTube, Facebook, TikTok, Instagram ইত্যাদি) ভিডিওর লিংক আমাকে পাঠান। "
        "আমি ভিডিওটি সরাসরি আপনাকে পাঠিয়ে দেব!\n\n"
        "🚀 **শুরু করতে যেকোনো একটি ভিডিওর লিংক পাঠান।**",
    )


def extract_file_id(msg):
    if not msg or getattr(msg, "empty", False):
        return None
    for ent in (msg.caption_entities or msg.entities or []):
        url = getattr(ent, "url", None)
        if url and url.startswith(HIDDEN_PREFIX):
            return url[len(HIDDEN_PREFIX):]
    return None


async def deliver_video(client, chat_id, post_id):
    """চ্যানেল পোস্টের ডাউনলোড বাটন থেকে এলে ভিডিও পাঠানো।"""
    file_id = VIDEO_CACHE.get(post_id)
    if not file_id:
        try:
            msg = await client.get_messages(CHANNEL_ID, post_id)
            file_id = extract_file_id(msg)
        except Exception as e:
            print(f"⚠️ চ্যানেল পোস্ট পড়া যায়নি: {e}", flush=True)
    if not file_id:
        await client.send_message(chat_id, "😔 দুঃখিত, ভিডিওটি আর পাওয়া যাচ্ছে না। অনুগ্রহ করে লিংক পাঠিয়ে নতুন করে চেষ্টা করুন।")
        return
    try:
        await client.send_video(
            chat_id,
            file_id,
            caption="✅ **আপনার ভিডিও প্রস্তুত!**\n\n💾 সেভ করতে ভিডিওতে ট্যাপ করে ⋮ মেনু থেকে **Save to Gallery** চাপুন।",
            supports_streaming=True,
        )
    except Exception as e:
        print(f"❌ ভিডিও পাঠানো ব্যর্থ: {e}", flush=True)
        await client.send_message(chat_id, FRIENDLY_ERROR)


async def post_to_channel(client, info, file_id, bot_username):
    """চ্যানেলে শুধু থাম্বনেইল + ডাউনলোড বাটন পোস্ট।"""
    title = clean_text(info.get("title"))
    extractor = clean_text(info.get("extractor_key") or "Social Media", 40)
    thumb = info.get("thumbnail")
    caption = (
        f"🎬 **{title}**\n"
        f"🌐 **প্ল্যাটফর্ম:** {extractor}\n\n"
        f"👇 ভিডিওটি ডাউনলোড করতে নিচের বাটনে চাপ দিন[\u200b]({HIDDEN_PREFIX}{file_id})"
    )

    post = None
    if thumb:
        try:
            post = await client.send_photo(CHANNEL_ID, photo=thumb, caption=caption)
        except Exception as e:
            print(f"⚠️ থাম্বনেইল ইউআরএল ব্যর্থ, ডাউনলোড করে চেষ্টা: {e}", flush=True)
            tmp = os.path.join(DOWNLOAD_DIR, f"thumb_{abs(hash(thumb))}.jpg")
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(thumb, timeout=aiohttp.ClientTimeout(total=20)) as r:
                        data = await r.read()
                with open(tmp, "wb") as f:
                    f.write(data)
                post = await client.send_photo(CHANNEL_ID, photo=tmp, caption=caption)
            except Exception as e2:
                print(f"⚠️ থাম্বনেইল আপলোডও ব্যর্থ: {e2}", flush=True)
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    if post is None:
        post = await client.send_message(CHANNEL_ID, caption, disable_web_page_preview=True)

    VIDEO_CACHE[post.id] = file_id
    markup = build_channel_markup(f"https://t.me/{bot_username}?start=v{post.id}")
    await client.edit_message_reply_markup(CHANNEL_ID, post.id, markup)


async def handle_link(client, chat_id, url):
    uid = f"{chat_id}_{abs(hash(url)) % 10**8}"
    processing_msg = await client.send_message(chat_id, "⏳ **ভিডিও ডাউনলোড হচ্ছে, দয়া করে একটু অপেক্ষা করুন...**")
    try:
        info, path = await asyncio.to_thread(download_video, url, uid)
        if not path or not os.path.exists(path):
            raise RuntimeError("ফাইল পাওয়া যায়নি")

        await processing_msg.edit_text("📤 **ভিডিও আপলোড হচ্ছে...**")
        title = clean_text(info.get("title"), 200)
        extractor = clean_text(info.get("extractor_key") or "Social Media", 40)
        bot_username = client.me.username if client.me else BOT_USERNAME_FALLBACK

        user_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 আমাদের অফিশিয়াল চ্যানেল", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")]
        ])
        sent = await client.send_video(
            chat_id=chat_id,
            video=path,
            caption=(
                f"✨ **ভিডিও সফলভাবে ডাউনলোড হয়েছে!**\n\n"
                f"🎬 **শিরোনাম:** {title}\n"
                f"🌐 **সোর্স:** {extractor}\n\n"
                f"💾 সেভ করতে ভিডিওতে ট্যাপ করে ⋮ মেনু থেকে **Save to Gallery** চাপুন।"
            ),
            reply_markup=user_markup,
            supports_streaming=True,
            duration=int(info.get("duration") or 0),
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
        )

        try:
            await post_to_channel(client, info, sent.video.file_id, bot_username)
        except Exception as ce:
            print(f"⚠️ চ্যানেলে পোস্ট করা যায়নি: {ce}", flush=True)

        await processing_msg.delete()

    except Exception as e:
        print(f"❌ ডাউনলোড ব্যর্থ [{url}]: {e}", flush=True)
        try:
            info = await asyncio.to_thread(extract_direct_link, url)
            video_url = info.get("url")
            if video_url:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("📥 সরাসরি ডাউনলোড করুন", url=video_url)]])
                await processing_msg.edit_text(
                    f"🎬 **{clean_text(info.get('title'), 200)}**\n\n👇 নিচের বাটনে চেপে ডাউনলোড করুন:",
                    reply_markup=markup,
                )
            else:
                await processing_msg.edit_text(FRIENDLY_ERROR)
        except Exception as e2:
            print(f"❌ ব্যাকআপও ব্যর্থ: {e2}", flush=True)
            await processing_msg.edit_text(FRIENDLY_ERROR)
    finally:
        cleanup(uid)


# ---------------- হ্যান্ডলার ----------------
@app.on_message(filters.private & filters.command("start"))
async def start_handler(client, message):
    if not message.from_user:
        return
    user = message.from_user
    payload = message.command[1] if len(message.command) > 1 else ""

    if not await is_member(client, user.id):
        await send_join_prompt(client, message.chat.id, payload)
        return

    if payload.startswith("v") and payload[1:].isdigit():
        await deliver_video(client, message.chat.id, int(payload[1:]))
        return

    await send_welcome(client, message.chat.id, user.first_name)


@app.on_message(filters.private & filters.text & ~filters.command(["start"]))
async def link_handler(client, message):
    if not message.from_user:
        return
    match = re.search(r"https?://\S+", message.text or "")
    if not match:
        await message.reply_text("❌ দয়া করে একটি সঠিক ভিডিওর লিংক (URL) পাঠান।")
        return
    url = match.group(0)

    if not await is_member(client, message.from_user.id):
        pending_urls[message.from_user.id] = url
        await send_join_prompt(client, message.chat.id)
        return

    await handle_link(client, message.chat.id, url)


@app.on_callback_query(filters.regex(r"^verify"))
async def verify_handler(client, cq: CallbackQuery):
    uid = cq.from_user.id
    if not await is_member(client, uid):
        await cq.answer("❌ আপনি এখনো চ্যানেলে জয়েন করেননি! আগে জয়েন করুন।", show_alert=True)
        return

    await cq.answer("✅ ভেরিফাই সফল হয়েছে!")
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
        await handle_link(client, chat_id, pending_urls.pop(uid))
    else:
        await send_welcome(client, chat_id, cq.from_user.first_name)


@app.on_callback_query(filters.regex(r"^react_(like|love|fire)$"))
async def reaction_handler(client, cq: CallbackQuery):
    kind = cq.data.split("_", 1)[1]
    msg = cq.message
    if not msg or not msg.reply_markup:
        await cq.answer("ধন্যবাদ!")
        return

    rows = msg.reply_markup.inline_keyboard
    try:
        download_url = rows[0][0].url
        react_row = rows[1]
    except Exception:
        await cq.answer("ধন্যবাদ!")
        return

    # বর্তমান কাউন্ট বাটনের লেখা থেকে পড়া (রিস্টার্টেও হারায় না)
    counts = {}
    for btn in react_row:
        key = (btn.callback_data or "").split("_", 1)[-1]
        m = re.search(r"(\d+)\s*$", btn.text or "")
        counts[key] = int(m.group(1)) if m else 0

    async with REACT_LOCK:
        users = USER_REACTIONS.setdefault((msg.chat.id, msg.id), {})
        prev = users.get(cq.from_user.id)
        if prev == kind:
            await cq.answer("আপনি আগেই এই রিঅ্যাকশন দিয়েছেন ✅")
            return
        if prev:
            counts[prev] = max(0, counts.get(prev, 0) - 1)
        counts[kind] = counts.get(kind, 0) + 1
        users[cq.from_user.id] = kind
        try:
            await msg.edit_reply_markup(build_channel_markup(download_url, counts))
        except Exception as e:
            print(f"⚠️ রিঅ্যাকশন আপডেট ব্যর্থ: {e}", flush=True)

    texts = {"like": "👍 লাইক দেওয়া হয়েছে!", "love": "❤️ ধন্যবাদ!", "fire": "🔥 দারুণ রিঅ্যাকশন!"}
    await cq.answer(texts[kind])


# --- aiohttp দিয়ে হালকা ওয়েব সার্ভার (রেন্ডার পোর্টের জন্য) ---
async def handle_web(request):
    return web.Response(text="Telegram Video Downloader Bot is Active!")


async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server_app = web.Application()
    server_app.router.add_get("/", handle_web)
    runner = web.AppRunner(server_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server running on port {port}", flush=True)


async def main():
    await start_web_server()
    print("🤖 টেলিগ্রাম বট চালু হচ্ছে...", flush=True)
    try:
        await app.start()
        me = await app.get_me()
        print(f"✅ বট চালু হয়েছে: @{me.username}", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ বট চালু করতে সমস্যা: {e}", flush=True)
        raise

    from pyrogram import idle
    await idle()
    await app.stop()


if __name__ == "__main__":
    # Pyrogram Client যে event loop-এ তৈরি হয়েছে, সেই loop-ই ব্যবহার করতে হবে
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())