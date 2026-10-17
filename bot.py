
import asyncio
import logging
import os
from pathlib import Path

import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ==================== CONFIG ====================

API_ID = int(os.environ["34635740"])
API_HASH = os.environ["0508f64706b52d5eb40787e9d6162478"]
BOT_TOKEN = os.environ["8992659315:AAFhS4gNbT3yAivQjSieBNdZ2bpEHUvr_xM"]
ALLOWED_CHAT_ID = int(os.environ["-1004334857415"])

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client(
    "my_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# ==================== URL HANDLER ====================

@app.on_message(filters.chat(ALLOWED_CHAT_ID) & filters.text)
async def ask_quality(client: Client, message: Message):
    url = (message.text or "").strip()

    if not (
        "youtube.com/" in url
        or "youtu.be/" in url
    ):
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 360p", callback_data="360"),
            InlineKeyboardButton("📥 480p", callback_data="480"),
        ],
        [
            InlineKeyboardButton("📥 720p HD", callback_data="720"),
            InlineKeyboardButton("📥 Best Quality", callback_data="best"),
        ],
        [
            InlineKeyboardButton(
                "🎵 Audio Only (MP3)",
                callback_data="audio",
            )
        ],
    ])

    await message.reply_text(
        "🎬 Download කිරීමට Quality එක තෝරන්න:",
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
    )


# ==================== DOWNLOAD FUNCTION ====================

def download_media(url: str, quality: str):
    if quality == "audio":
        format_selector = "bestaudio/best"

    elif quality == "best":
        format_selector = "bv*+ba/b"

    elif quality in ("360", "480", "720"):
        format_selector = (
            f"bv*[height<={quality}]+ba/"
            f"b[height<={quality}]/bv*+ba/b
            )

    else:
        raise ValueError("තෝරාගත් Quality එක වලංගු නැහැ.")

    ydl_opts = {
        "format": format_selector,
        "outtmpl": str(DOWNLOAD_DIR / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }

    # Audio සඳහා MP3 conversion එකක් සිදු කරන්න.
    if quality == "audio":
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info.get("id")
    if not video_id:
        raise RuntimeError("YouTube video ID එක ලබාගත නොහැක.")

    # Merge / conversion එකෙන් පසු ඇති සැබෑ ගොනුව සොයන්න.
    candidates = [
        p for p in DOWNLOAD_DIR.glob(f"{video_id}.*")
        if p.is_file()
        and not p.name.endswith((".part", ".ytdl"))
    ]

    if quality == "audio":
        candidates = [
            p for p in candidates
            if p.suffix.lower() == ".mp3"
        ]

    if not candidates:
        raise FileNotFoundError(
            "Download එක අවසන් වුණත් output file එක සොයාගත නොහැක. "
            "Railway logs පරීක්ෂා කරන්න."
        )

    file_path = max(candidates, key=lambda p: p.stat().st_mtime)
    title = info.get("title") or "YouTube Download"

    return str(file_path), title


# ==================== QUALITY BUTTON HANDLER ====================

@app.on_callback_query()
async def download_selected_quality(
    client: Client,
    callback_query: CallbackQuery,
):
    message = callback_query.message

    if not message or message.chat.id != ALLOWED_CHAT_ID:
        await callback_query.answer("මෙම chat එකට අවසර නැහැ.")
        return

    quality = callback_query.data

    if quality not in ("360", "480", "720", "best", "audio"):
        await callback_query.answer("Quality එක වලංගු නැහැ.")
        return

    original_message = message.reply_to_message

    if not original_message or not original_message.text:
        await callback_query.answer("YouTube link එක සොයාගත නොහැක.")
        return

    url = original_message.text.strip()

    if not ("youtube.com/" in url or "youtu.be/" in url):
        await callback_query.answer("වලංගු YouTube link එකක් නොවේ.")
        return

    await callback_query.answer("Download ආරම්භ කරනවා...")

    try:
        await message.edit_text(
            f"⏳ {quality} Quality එකෙන් Download වෙමින් පවතී..."
        )

        # yt-dlp blocking වැඩසටහන වෙනම thread එකක ධාවනය කරයි.
        file_path, title = await asyncio.to_thread(
            download_media, url, quality
        )

        await message.edit_text("📤 Telegram වෙත Upload වෙමින් පවතී...")

        if quality == "audio":
            await client.send_audio(
                chat_id=message.chat.id,
                audio=file_path,
                caption=f"🎵 {title}",
            )
        else:
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                caption=f"🎥 {title}\nQuality: {quality}",
            )

        await message.delete()

    except Exception as exc:
        logger.exception("YouTube download/upload failed")

        error_text = str(exc)
        if len(error_text) > 2500:
            error_text = error_text[:2500] + "..."

        try:
            await message.edit_text(
                "❌ Download කිරීමේදී දෝෂයක් සිදු විය:\n"
                f"{error_text}"
            )
        except Exception:
            logger.exception("Could not display error to Telegram")

    finally:
        # Download කළ output file එක ඉවත් කරන්න.
        if "file_path" in locals():
            try:
                path = Path(file_path)
                if path.exists():
                    path.unlink()
            except OSError:
                logger.exception("Could not delete downloaded file")


# ==================== START BOT ====================

if __name__ == "__main__":
    logger.info("Starting Telegram YouTube Downloader Bot...")
    app.run()
