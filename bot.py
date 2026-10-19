import os
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- কনফিগারেশন (আপনার নিজস্ব তথ্য এখানে বসান) ---
API_ID = 39912374  # আপনার my.telegram.org থেকে প্রাপ্ত নম্বরটি দিন (সংখ্যারূপে)
API_HASH = "6f21928259361f124863df3912790359"  # আপনার API Hash দিন
BOT_TOKEN = "8972396687:AAFUr-W6sWuW4o1aJFwhEYG10D6NhGc4G84"  # BotFather থেকে পাওয়া বট টোকেন দিন
CHANNEL_ID = "@sr_downloader_official"  # আপনার টেলিগ্রাম চ্যানেলের ইউজারনেম (বটকে অবশ্যই চ্যানেলের অ্যাডমিন বানাতে হবে)

app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
def start_handler(client, message):
    user_name = message.from_user.first_name
    welcome_text = (
        f"✨ **স্বাগতম, {user_name}!**\n\n"
        "🎬 আমি একটি **অ্যাডভান্সড ইউনিভার্সাল ভিডিও ডাউনলোডার বট**।\n\n"
        "📥 যেকোনো প্ল্যাটফর্মের (YouTube, Facebook, TikTok, Instagram ইত্যাদি) ভিডিওর লিংক আমাকে পাঠান। "
        "আমি সাথে সাথে আপনাকে ডাইরেক্ট ডাউনলোড লিংক দেব এবং আমাদের চ্যানেলে পোস্ট করে দেব!\n\n"
        "🚀 **শুরু করতে যেকোনো একটি ভিডিওর লিংক পাঠান।**"
    )
    message.reply_text(welcome_text)

@app.on_message(filters.text & ~filters.command(["start"]))
def process_video(client, message):
    url = message.text
    
    if not url.startswith("http"):
        message.reply_text("❌ দয়া করে একটি সঠিক ভিডিওর লিংক (URL) পাঠান।")
        return
        
    processing_msg = message.reply_text("⏳ **লিংক প্রসেস করা হচ্ছে, দয়া করে একটু অপেক্ষা করুন...**")
    
    try:
        # yt-dlp দিয়ে সার্ভারে ডাউনলোড ছাড়াই ডাইরেক্ট লিংক ও ইনফো এক্সট্রাক্ট করা
        ydl_opts = {'format': 'best', 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_url = info.get('url')
            title = info.get('title', 'আকর্ষণীয় ভিডিও')
            thumbnail = info.get('thumbnail')
            extractor = info.get('extractor_key', 'Social Media')
        
        if not video_url:
            processing_msg.edit_text("❌ দুঃখিত, এই ভিডিওটির ডাইরেক্ট লিংক সংগ্রহ করা সম্ভব হয়নি।")
            return

        # ১. ইউজারের ইনবক্সের জন্য সুন্দর ফরম্যাট ও বাটন
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
            message.reply_photo(photo=thumbnail, caption=user_caption, reply_markup=user_markup)
        else:
            message.reply_text(user_caption, reply_markup=user_markup)
            
        # ২. টেলিগ্রাম চ্যানেলের জন্য প্রিমিয়াম পোস্ট (ডাউনলোড বাটন ও রিঅ্যাকশন সহ)
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
            f"🤖 বট থেকে আপনার পছন্দের ভিডিও ডাউনলোড করতে ভিজিট করুন: @{app.me.username}"
        )
        
        if thumbnail:
            client.send_photo(
                chat_id=CHANNEL_ID,
                photo=thumbnail,
                caption=channel_caption,
                reply_markup=channel_markup
            )
            
        processing_msg.delete()
        
    except Exception as e:
        processing_msg.edit_text(f"❌ একটি ত্রুটি ঘটেছে:\n`{str(e)}`")

@app.on_callback_query(filters.regex("react_"))
def reaction_handler(client, callback_query: CallbackQuery):
    action = callback_query.data.split("_")[1]
    if action == "like":
        callback_query.answer("👍 আপনি এই ভিডিওটিতে লাইক দিয়েছেন!", show_alert=False)
    elif action == "love":
        callback_query.answer("❤️ আপনার ভালোবাসার জন্য ধন্যবাদ!", show_alert=False)
    elif action == "fire":
        callback_query.answer("🔥 দারুণ রিঅ্যাকশন!", show_alert=False)

if __name__ == "__main__":
    print("🤖 ভিডিও ডাউনলোডার বট সফলভাবে চালু হচ্ছে...")
    app.run()
