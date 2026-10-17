
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ==================== CONFIG ====================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Railway resource usage control
MAX_CONCURRENT_DOWNLOADS = max(
    1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "1"))
)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# Optional private cookie-file path from Railway Variables
COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()

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

def normalize_youtube_url(value: str) -> str | None:
    """Validate and normalize a YouTube URL."""
    value = (value or "").strip()

    if not value or len(value) > 2048:
        return None

    if "://" not in value:
        value = "https://" + value

    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None

    allowed_hosts = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }

    if host not in allowed_hosts:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    if parsed.username is not None or parsed.password is not None:
        return None

    return value


# ==================== URL HANDLER ====================

@app.on_message(filters.chat(ALLOWED_CHAT_ID) & filters.text)
async def ask_quality(client: Client, message: Message):
    url = normalize_youtube_url(message.text or "")

    if not url:
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 360p", callback_data="360"),
                InlineKeyboardButton("📥 480p", callback_data="480"),
            ],
            [
                InlineKeyboardButton("📥 720p HD", callback_data="720"),
                InlineKeyboardButton(
                    "📥 Best Quality", callback_data="best"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎵 Audio Only (MP3)", callback_data="audio"
                )
            ],
        ]
    )

    await message.reply_text(
        "🎬 Download කිරීමට Quality එක තෝරන්න:",
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
    )


# ==================== DOWNLOAD FUNCTION ====================

def download_media(url: str, quality: str) -> tuple[str, str, str]:
    """Download media and return (file_path, title, temp_dir)."""

    if quality == "audio":
        format_selector = "bestaudio/best"

    elif quality == "best":
        format_selector = "bv*+ba/b"

    elif quality in ("360", "480", "720"):
        limit = int(quality)
        format_selector = (
            f"bv*[height<={limit}]+ba/"
            f"b[height<={limit}]"
        )

    else:
        raise ValueError("තෝරාගත් Quality එක වලංගු නැහැ.")

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="download_",
            dir=str(DOWNLOAD_DIR),
        )
    )

    try:
        ydl_opts = {
            "format": format_selector,
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "restrictfilenames": True,
            "merge_output_format": "mp4",
            "js_runtimes": {"deno": {}},
            "overwrites": True,
        }

        # Optional cookies; keep the file private.
        if COOKIES_FILE:
            cookie_path = Path(COOKIES_FILE)

            if cookie_path.is_file():
                ydl_opts["cookiefile"] = str(cookie_path)
            else:
                logger.warning(
                    "Cookie file path is configured but file is missing."
                )

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

        if not info:
            raise RuntimeError(
                "YouTube වෙතින් video තොරතුරු ලබාගත නොහැක."
            )

        video_id = info.get("id")

        if not video_id:
            raise RuntimeError("YouTube video ID එක ලබාගත නොහැක.")

        candidates = [
            path
            for path in temp_dir.glob(f"{video_id}.*")
            if path.is_file()
            and not path.name.endswith((".part", ".ytdl", ".json"))
        ]

        if quality == "audio":
            candidates = [
                path
                for path in candidates
                if path.suffix.lower() == ".mp3"
            ]

        if not candidates:
            raise FileNotFoundError(
                "Output file එක හමු වුණේ නැහැ. Railway logs පරීක්ෂා කරන්න."
            )

        file_path = max(
            candidates,
            key=lambda path: path.stat().st_size,
        )

        if file_path.stat().st_size <= 0:
            raise FileNotFoundError("Download කළ file එක හිස්.")

        title = info.get("title") or "YouTube Download"

        return str(file_path), title, str(temp_dir)

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# ==================== QUALITY BUTTON HANDLER ====================

@app.on_callback_query()
async def download_selected_quality(
    client: Client,
    callback_query: CallbackQuery,
):
    message = callback_query.message

    if not message or message.chat.id != ALLOWED_CHAT_ID:
        await callback_query.answer(
            "මෙම chat එකට අවසර නැහැ.",
            show_alert=True,
        )
        return

    quality = callback_query.data

    if quality not in ("360", "480", "720", "best", "audio"):
        await callback_query.answer(
            "Quality එක වලංගු නැහැ.",
            show_alert=True,
        )
        return

    original_message = message.reply_to_message

    if not original_message or not original_message.text:
        await callback_query.answer(
            "YouTube link එක සොයාගත නොහැක.",
            show_alert=True,
        )
        return

    url = normalize_youtube_url(original_message.text)

    if not url:
        await callback_query.answer(
            "වලංගු YouTube link එකක් නොවේ.",
            show_alert=True,
        )
        return

    await callback_query.answer("Download ආරම්භ කරනවා...")

    temp_dir = None

    try:
        # Queue extra requests to limit resource usage.
        async with DOWNLOAD_SEMAPHORE:
            await message.edit_text(
                f"⏳ {quality} Quality එකෙන් Download වෙමින් පවතී...",
                parse_mode=None,
            )

            file_path, title, temp_dir = await asyncio.to_thread(
                download_media,
                url,
                quality,
            )

            await message.edit_text(
                "📤 Telegram වෙත Upload වෙමින් පවතී...",
                parse_mode=None,
            )

            safe_title = title[:700]

            if quality == "audio":
                await client.send_audio(
                    chat_id=message.chat.id,
                    audio=file_path,
                    caption=f"🎵 {safe_title}",
                    parse_mode=None,
                )

            else:
                quality_label = (
                    "Best Quality"
                    if quality == "best"
                    else f"{quality}p"
                )

                caption = (
                    f"🎥 {safe_title}\n"
                    f"Quality: {quality_label}"
                )

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

    except FloodWait as exc:
        wait_seconds = int(getattr(exc, "value", 0) or 0)

        logger.warning(
            "Telegram FloodWait: %s seconds",
            wait_seconds,
        )

        try:
            await message.edit_text(
                "⏳ Telegram rate limit එකක් ඇතිවුණා. "
                f"තත්පර {wait_seconds} කින් නැවත උත්සාහ කරන්න.",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Could not display FloodWait message")

    except Exception as exc:
        logger.exception("YouTube download/upload failed")

        error_text = str(exc) or exc.__class__.__name__

        if len(error_text) > 1800:
            error_text = error_text[:1800] + "..."

        try:
            await message.edit_text(
                "❌ Download කිරීමේදී දෝෂයක් සිදු විය:\n"
                f"{error_text}",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Could not display error in Telegram")

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ==================== START BOT ====================

if __name__ == "__main__":
    logger.info("Starting Telegram YouTube Downloader Bot...")
    app.run()
