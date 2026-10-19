import os
import asyncio
import aiohttp
from aiohttp import web
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- টেলিগ্রাম বট কনফিগারেশন ---
API_ID = 39912374  
API_HASH = "6f21928259361f124863df3912790359"  
BOT_TOKEN = "8972396687:AAFUr-W6sWuW4o1aJFwhEYG10D6NhGc4G84"  
CHANNEL_ID = "@sr_downloader_official"  

app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    user_name = message.from_user.first_name
    welcome_text = (
        f"✨ **স্বাগতম, {user_name}!**\n\n"
        "🎬 আমি একটি **অ্যাডভান্সড ইউনিভার্সাল ভিডিও ডাউনলোডার বট**।\n\n"
        "📥 যেকোনো প্ল্যাটফর্মের (YouTube, Facebook, TikTok, Instagram ইত্যাদি) ভিডিওর লিংক আমাকে পাঠান। "
        "আমি সাথে সাথে আপনাকে ডাইরেক্ট ডাউনলোড লিংক দেব এবং আমাদের চ্যানেলে পোস্ট করে দেব!\n\n"
        "🚀 **শুরু করতে যেকোনো একটি ভিডিওর লিংক পাঠান।**"
    )
    await message.reply_text(welcome_text)

@app.on_message(filters.text & ~filters.command(["start"]))
async def process_video(client, message):
    url = message.text
    
    if not url.startswith("http"):
        await message.reply_text("❌ দয়া করে একটি সঠিক ভিডিওর লিংক (URL) পাঠান।")
        return
        
    processing_msg = await message.reply_text("⏳ **লিংক প্রসেস করা হচ্ছে, দয়া করে একটু অপেক্ষা করুন...**")
    
    try:
        ydl_opts = {'format': 'best', 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_url = info.get('url')
            title = info.get('title', 'আকর্ষণীয় ভিডিও')
            thumbnail = info.get('thumbnail')
            extractor = info.get('extractor_key', 'Social Media')
        
        if not video_url:
            await processing_msg.edit_text("❌ দুঃখিত, এই ভিডিওটির ডাইরেক্ট লিংক সংগ্রহ করা সম্ভব হয়নি।")
            return

        user_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 সরাসরি ফোনে ডাউনলোড করুন", url=video_url)],
            [InlineKeyboardButton("📢 আমাদের অফিশিয়াল চ্যানেল", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")]
        ])
        
        user_caption = (
            f"✨ **ভিডিও সফলভাবে প্রসেস করা হয়েছে!**\n\n"
            f"🎬 **শিরোনাম:** {title}\n"
            f"🌐 **সোর্স:** {extractor}\n\n"
            f"👇 নিচের বাটনে ক্লিক করে সরাসরি আপনার ফোনের গ্যালারিতে ডাউনলোড করে নিন:"
        )
        
        if thumbnail:
            await message.reply_photo(photo=thumbnail, caption=user_caption, reply_markup=user_markup)
        else:
            await message.reply_text(user_caption, reply_markup=user_markup)
            
        bot_username = client.me.username if client.me else "sr_downloader_official"
        
        channel_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 সরাসরি ডাউনলোড করুন", url=video_url)],
            [
                InlineKeyboardButton("👍 লাইক", callback_data="react_like"),
                InlineKeyboardButton("❤️ লাভ", callback_data="react_love"),
                InlineKeyboardButton("🔥 আগুন", callback_data="react_fire")
            ]
        ])
        
        channel_caption = (
            f"📢 **নতুন ভিডিও ডাউনলোড অ্যালার্ট!**\n\n"
            f"🎬 **{title}**\n"
            f"🌐 **প্ল্যাটফর্ম:** {extractor}\n\n"
            f"💡 *এই ভিডিওটি সরাসরি আপনার ফোনে ডাউনলোড করতে নিচের বাটনে ক্লিক করুন!*\n\n"
            f"🤖 বট থেকে আপনার পছন্দের ভিডিও ডাউনলোড করতে ভিজিট করুন: @{bot_username}"
        )
        
        if thumbnail:
            await client.send_photo(
                chat_id=CHANNEL_ID,
                photo=thumbnail,
                caption=channel_caption,
                reply_markup=channel_markup
            )
            
        await processing_msg.delete()
        
    except Exception as e:
        await processing_msg.edit_text(f"❌ একটি ত্রুটি ঘটেছে:\n`{str(e)}`")

@app.on_callback_query(filters.regex("react_"))
async def reaction_handler(client, callback_query: CallbackQuery):
    action = callback_query.data.split("_")[1]
    if action == "like":
        await callback_query.answer("👍 আপনি এই ভিডিওটিতে লাইক দিয়েছেন!", show_alert=False)
    elif action == "love":
        await callback_query.answer("❤️ আপনার ভালোবাসার জন্য ধন্যবাদ!", show_alert=False)
    elif action == "fire":
        await callback_query.answer("🔥 দারুণ রিঅ্যাকশন!", show_alert=False)

# --- aiohttp দিয়ে হালকা ওয়েব সার্ভার (রেন্ডার পোর্টের জন্য) ---
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
    print(f"Web server running on port {port}")

async def main():
    # ওয়েব সার্ভার ও টেলিগ্রাম বট একসাথে স্টার্ট করা হলো
    await start_web_server()
    print("🤖 টেলিগ্রাম বট সফলভাবে চালু হচ্ছে...")
    await app.start()
    
    from pyrogram import idle
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
