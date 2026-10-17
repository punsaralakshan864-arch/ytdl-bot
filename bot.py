import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# ==================== CONFIG ====================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = Client(
    "my_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


# ==================== URL VALIDATION ====================

def is_youtube_url(url: str) -> bool:
    """Return True only for supported YouTube hostnames."""
    url = url.strip()
    if not url:
        return False

    if not url.startswith(("https://", "http://")):
        url = "https://" + url

    host = (urlparse(url).hostname or "").lower()
    return (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtu.be"
        or host.endswith(".youtube-nocookie.com")
    )


# ==================== URL HANDLER ====================

@app.on_message(filters.chat(ALLOWED_CHAT_ID) & filters.text)
async def ask_quality(client: Client, message: Message):
    url = (message.text or "").strip()
    if not is_youtube_url(url):
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
        [InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data="audio")],
    ])

    await message.reply_text(
        "🎬 Download කිරීමට Quality එක තෝරන්න:",
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
    )


# ==================== DOWNLOAD FUNCTION ====================

def download_media(url: str, quality: str):
    """Download media and return (file_path, title, temp_dir)."""
    if quality == "audio":
        format_selector = "bestaudio/best"
    elif quality == "best":
        format_selector = "bv*+ba/b"
    elif quality in ("360", "480", "720"):
        format_selector = (
            f"bv*[height<={quality}]+ba/"
            f"b[height<={quality}]/bv*+ba/b"
        )
    else:
        raise ValueError("තෝරාගත් Quality එක වලංගු නැහැ.")

    temp_dir = Path(tempfile.mkdtemp(prefix="download_", dir=str(DOWNLOAD_DIR)))

    try:
        ydl_opts = {
            "format": format_selector,
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "restrictfilenames": True,
            "merge_output_format": "mp4",
            # Deno is installed by the Railway build configuration.
            "js_runtimes": {"deno": {}},
            # Try YouTube clients that may avoid some bot-check challenges.
            # This is not guaranteed to bypass YouTube-side restrictions.
            "extractor_args": {
                "youtube": {
                    "player_client": ["tv", "web_safari"],
                }
            },
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
        }

        if quality == "audio":
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if not info:
            raise RuntimeError("YouTube වෙතින් video තොරතුරු ලබාගත නොහැක.")

        video_id = info.get("id")
        if not video_id:
            raise RuntimeError("YouTube video ID එක ලබාගත නොහැක.")

        candidates = [
            p for p in temp_dir.glob(f"{video_id}.*")
            if p.is_file() and not p.name.endswith((".part", ".ytdl"))
        ]

        if quality == "audio":
            candidates = [p for p in candidates if p.suffix.lower() == ".mp3"]

        if not candidates:
            raise FileNotFoundError(
                "Download එක අවසන් වුණත් output file එක හමු වුණේ නැහැ. "
                "Railway logs පරීක්ෂා කරන්න."
            )

        file_path = max(candidates, key=lambda p: p.stat().st_mtime)
        title = info.get("title") or "YouTube Download"
        return str(file_path), title, str(temp_dir)

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# ==================== QUALITY BUTTON HANDLER ====================

@app.on_callback_query()
async def download_selected_quality(client: Client, callback_query: CallbackQuery):
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
    if not is_youtube_url(url):
        await callback_query.answer("වලංගු YouTube link එකක් නොවේ.")
        return

    await callback_query.answer("Download ආරම්භ කරනවා...")
    temp_dir = None

    try:
        label = "MP3 Audio" if quality == "audio" else quality
        await message.edit_text(
            f"⏳ {label} Quality එකෙන් Download වෙමින් පවතී...",
            parse_mode=None,
        )

        file_path, title, temp_dir = await asyncio.to_thread(
            download_media, url, quality
        )

        await message.edit_text("📤 Telegram වෙත Upload වෙමින් පවතී...", parse_mode=None)

        safe_title = title[:800]
        if quality == "audio":
            await client.send_audio(
                chat_id=message.chat.id,
                audio=file_path,
                caption=f"🎵 {safe_title}",
                parse_mode=None,
            )
        else:
            caption = f"🎥 {safe_title}\nQuality: {label}"
            if Path(file_path).suffix.lower() == ".mp4":
                await client.send_video(
                    chat_id=message.chat.id,
                    video=file_path,
                    caption=caption,
                    parse_mode=None,
                )
            else:
                await client.send_document(
                    chat_id=message.chat.id,
                    document=file_path,
                    caption=caption,
                    parse_mode=None,
                )

        await message.delete()

    except Exception as exc:
        logger.exception("YouTube download/upload failed")
        error_text = str(exc)
        if len(error_text) > 2500:
            error_text = error_text[:2500] + "..."

        try:
            await message.edit_text(
                "❌ Download කිරීමේදී දෝෂයක් සිදු විය:\n" + error_text,
                parse_mode=None,
            )
        except Exception:
            logger.exception("Could not display the error in Telegram")

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ==================== START BOT ====================

if __name__ == "__main__":
    logger.info("Starting Telegram YouTube Downloader Bot...")
    app.run()
