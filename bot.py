import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

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
    """Check whether the supplied link belongs to YouTube."""
    url = url.strip()

    if not url.startswith(("https://", "http://")):
        url = "https://" + url

    host = (urlparse(url).hostname or "").lower()

    return (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtu.be"
    )


# ==================== URL HANDLER ====================

@app.on_message(filters.chat(ALLOWED_CHAT_ID) & filters.text)
async def ask_quality(client: Client, message: Message):
    url = (message.text or "").strip()

    if not is_youtube_url(url):
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📥 360p", callback_data="360"
                ),
                InlineKeyboardButton(
                    "📥 480p", callback_data="480"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📥 720p HD", callback_data="720"
                ),
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

def download_media(url: str, quality: str):
    """
    Download media in a separate temporary folder.
    Returns: file path, title, temporary folder path.
    """

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

    # Each download gets its own folder.
    # This prevents two downloads from overwriting each other.
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

            # Merge separate video/audio streams when required.
            "merge_output_format": "mp4",

            # Use Deno for YouTube JavaScript challenges.
            "js_runtimes": {"deno": {}},
        }

        # Convert audio to MP3 using FFmpeg.
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
            raise RuntimeError(
                "YouTube video ID එක ලබාගත නොහැක."
            )

        # Search for the actual output file after merging/conversion.
        candidates = [
            path
            for path in temp_dir.glob(f"{video_id}.*")
            if path.is_file()
            and not path.name.endswith((".part", ".ytdl"))
        ]

        # Audio mode must produce an MP3 file.
        if quality == "audio":
            candidates = [
                path
                for path in candidates
                if path.suffix.lower() == ".mp3"
            ]

        if not candidates:
            raise FileNotFoundError(
                "Download එක අවසන් වුණත් output file එක හමු වුණේ නැහැ. "
                "Railway logs පරීක්ෂා කරන්න."
            )

        file_path = max(
            candidates,
            key=lambda path: path.stat().st_mtime,
        )

        title = info.get("title") or "YouTube Download"

        return str(file_path), title, str(temp_dir)

    except Exception:
        # Remove incomplete files if downloading failed.
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
            "මෙම chat එකට අවසර නැහැ."
        )
        return

    quality = callback_query.data

    if quality not in ("360", "480", "720", "best", "audio"):
        await callback_query.answer(
            "Quality එක වලංගු නැහැ."
        )
        return

    original_message = message.reply_to_message

    if not original_message or not original_message.text:
        await callback_query.answer(
            "YouTube link එක සොයාගත නොහැක."
        )
        return

    url = original_message.text.strip()

    if not is_youtube_url(url):
        await callback_query.answer(
            "වලංගු YouTube link එකක් නොවේ."
        )
        return

    await callback_query.answer(
        "Download ආරම්භ කරනවා..."
    )

    temp_dir = None

    try:
        await message.edit_text(
            f"⏳ {quality} Quality එකෙන් Download වෙමින් පවතී...",
            parse_mode=None,
        )

        # yt-dlp is blocking, so run it outside the event loop.
        file_path, title, temp_dir = await asyncio.to_thread(
            download_media,
            url,
            quality,
        )

        await message.edit_text(
            "📤 Telegram වෙත Upload වෙමින් පවතී...",
            parse_mode=None,
        )

        # Avoid caption parse errors and excessively long captions.
        safe_title = title[:800]

        if quality == "audio":
            await client.send_audio(
                chat_id=message.chat.id,
                audio=file_path,
                caption=f"🎵 {safe_title}",
                parse_mode=None,
            )

        else:
            caption = f"🎥 {safe_title}\nQuality: {quality}"

            # Telegram video previews generally work best with MP4.
            # Other formats are uploaded as documents instead.
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
                "❌ Download කිරීමේදී දෝෂයක් සිදු විය:\n"
                f"{error_text}",
                parse_mode=None,
            )
        except Exception:
            logger.exception(
                "Could not display the error in Telegram"
            )

    finally:
        # Always remove temporary files after upload or failure.
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ==================== START BOT ====================

if __name__ == "__main__":
    logger.info("Starting Telegram YouTube Downloader Bot...")
    app.run()
