import asyncio

try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp

logging.basicConfig(level=logging.INFO)

# ==================== BOT CONFIG ====================
API_ID = 34635740
API_HASH = "0508f64706b52d5eb40787e9d6162478"
BOT_TOKEN = "8992659315:AAGun4TUhwjGY0oSJncWfOGC7bSbQ4I0Utw"
ALLOWED_CHAT_ID = -1004334857415 
# ====================================================

app = Client("my_bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.chat(ALLOWED_CHAT_ID) & filters.text)
async def ask_quality(client: Client, message: Message):
    url = message.text.strip()

    if not ("youtube.com" in url or "youtu.be" in url):
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 360p", callback_data="360"),
            InlineKeyboardButton("📥 480p", callback_data="480")
        ],
        [
            InlineKeyboardButton("📥 720p HD", callback_data="720"),
            InlineKeyboardButton("📥 Best Quality", callback_data="best")
        ],
        [
            InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data="audio")
        ]
    ])

    await message.reply_text(
        "🎬 **කරුණාකර Download කරගැනීමට Quality එක තෝරන්න:**",
        reply_markup=keyboard,
        quote=True
    )

@app.on_callback_query()
async def download_selected_quality(client: Client, callback_query: CallbackQuery):
    if callback_query.message.chat.id != ALLOWED_CHAT_ID:
        return

    quality = callback_query.data
    url = callback_query.message.reply_to_message.text.strip()

    try:
        await callback_query.message.edit_text(f"⏳ **{quality}** Quality එකෙන් Download වෙමින් පවතී...")
    except Exception:
        pass

    # Cookies file එක සමඟ yt-dlp options
    if quality == "audio":
        ydl_opts = {
            'format': 'bestaudio',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'cookiefile': 'cookies.txt',
            'quiet': True,
        }
    elif quality == "best":
        ydl_opts = {
            'format': 'best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'cookiefile': 'cookies.txt',
            'quiet': True,
        }
    else:
        ydl_opts = {
            'format': f'best[height<={quality}]/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'cookiefile': 'cookies.txt',
            'quiet': True,
        }

    file_path = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            title = info.get('title', 'YouTube Video')

        try:
            await callback_query.message.edit_text("📤 Telegram වෙත Upload වෙමින් පවතී...")
        except Exception:
            pass

        safe_title = title.replace("<", "&lt;").replace(">", "&gt;")

        if quality == "audio":
            await client.send_audio(
                chat_id=callback_query.message.chat.id,
                audio=file_path,
                caption=f"🎵 **{safe_title}**\n\nDone! ✨"
            )
        else:
            await client.send_video(
                chat_id=callback_query.message.chat.id,
                video=file_path,
                caption=f"🎥 **{safe_title}**\n📊 Quality: {quality}p\n\nDone! ✨"
            )

        await callback_query.message.delete()

    except Exception as e:
        try:
            await callback_query.message.edit_text(f"❌ Download කිරීමේදී දෝෂයක් සිදු විය: {e}")
        except Exception:
            pass

    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

async def main():
    await app.start()
    print("Bot සාර්ථකව Start විය!...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop.run_until_complete(main())
