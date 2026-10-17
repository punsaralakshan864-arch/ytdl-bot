
import asyncio
import logging
import os
import shutil
import tempfile
import time
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

# ==================== HELPERS ====================

def is_youtube_url(url: str) -> bool:
    url = url.strip()
    if not url.startswith(("https://", "http://")):
        url = "https://" + url

    host = (urlparse(url).hostname or "").lower()
    return (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtu.be"
    )


def format_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def format_time(seconds):
    if seconds is None or seconds < 0:
        return "ගණනය කරමින්..."

    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def progress_bar(percent):
    filled = max(0, min(10, int(percent // 10)))
    return "█" * filled + "░" * (10 - filled)


# ==================== LINK HANDLER ====================

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
        [
            InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data="audio")
        ],
    ])

    await message.reply_text(
        "🎬 Download කිරීමට Quality එක තෝරන්න:",
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
    )


# ==================== DOWNLOAD ====================

def download_media(url: str, quality: str, state: dict):
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

    temp_dir = Path(tempfile.mkdtemp(
        prefix="download_",
        dir=str(DOWNLOAD_DIR),
    ))

    try:
        def download_hook(data):
            status = data.get("status")

            if status == "downloading":
                state.update({
                    "status": "downloading",
                    "downloaded": data.get("downloaded_bytes", 0),
                    "total": (
                        data.get("total_bytes")
                        or data.get("total_bytes_estimate")
                        or 0
                    ),
                    "speed": data.get("speed") or 0,
                    "eta": data.get("eta"),
                })

            elif status == "finished":
                state.update({
                    "status": "processing",
                    "downloaded": data.get("downloaded_bytes", 0),
                    "total": data.get("total_bytes") or 0,
                    "speed": 0,
                    "eta": None,
                })

        ydl_opts = {
            "format": format_selector,
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "restrictfilenames": True,
            "merge_output_format": "mp4",
            "js_runtimes": {"deno": {}},
            "progress_hooks": [download_hook],

            # Parallel download of supported media fragments.
            "concurrent_fragment_downloads": 4,
        }

        if quality == "audio":
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if not info or not info.get("id"):
            raise RuntimeError("YouTube video තොරතුරු ලබාගත නොහැක.")

        video_id = info["id"]
        candidates = [
            p for p in temp_dir.glob(f"{video_id}.*")
            if p.is_file()
            and not p.name.endswith((".part", ".ytdl"))
        ]

        if quality == "audio":
            candidates = [
                p for p in candidates if p.suffix.lower() == ".mp3"
            ]

        if not candidates:
            raise FileNotFoundError(
                "Download අවසන් වුණත් output file එක හමු වුණේ නැහැ."
            )

        file_path = max(candidates, key=lambda p: p.stat().st_mtime)
        state["status"] = "done"

        return str(file_path), info.get("title") or "YouTube Download", str(temp_dir)

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# ==================== TELEGRAM PROGRESS ====================

async def upload_progress(current, total, status_message, started_at, label):
    now = time.monotonic()
    last_update = getattr(upload_progress, "last_update", 0)

    if current < total and now - last_update < 2:
        return

    upload_progress.last_update = now

    percent = (current * 100 / total) if total else 0
    elapsed = max(now - started_at, 0.1)
    speed = current / elapsed
    remaining = (total - current) / speed if speed > 0 and total else None

    text = (
        f"{label}\n\n"
        f"{progress_bar(percent)} {percent:.1f}%\n"
        f"📦 {format_bytes(current)} / {format_bytes(total)}\n"
        f"⚡ Speed: {format_bytes(speed)}/s\n"
        f"⏱️ ඉතිරි කාලය: {format_time(remaining)}"
    )

    try:
        await status_message.edit_text(text, parse_mode=None)
    except Exception as exc:
        logger.debug("Progress message update skipped: %s", exc)


# ==================== QUALITY BUTTON ====================

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
    if not is_youtube_url(url):
        await callback_query.answer("වලංගු YouTube link එකක් නොවේ.")
        return

    await callback_query.answer("Download ආරම්භ කරනවා...")

    temp_dir = None
    try:
        await message.edit_text(
            f"⏳ {quality} Quality එකෙන් සූදානම් වෙමින්...",
            parse_mode=None,
        )

        state = {"status": "starting"}
        task = asyncio.create_task(asyncio.to_thread(
            download_media, url, quality, state
        ))

        last_text = ""
        while not task.done():
            await asyncio.sleep(2)

            status = state.get("status")
            if status == "downloading":
                downloaded = state.get("downloaded", 0)
                total = state.get("total", 0)
                percent = downloaded * 100 / total if total else 0

                text = (
                    f"⬇️ Download වෙමින් පවතී\n\n"
                    f"{progress_bar(percent)} {percent:.1f}%\n"
                    f"📦 {format_bytes(downloaded)} / "
                    f"{format_bytes(total)}\n"
                    f"⚡ Speed: {format_bytes(state.get('speed', 0))}/s\n"
                    f"⏱️ ඉතිරි කාලය: {format_time(state.get('eta'))}"
                )
            elif status == "processing":
                text = "🔧 Download අවසන්. Video එක සකස් කරමින්..."
            else:
                text = "🔎 YouTube link එක පරීක්ෂා කරමින්..."

            if text != last_text:
                try:
                    await message.edit_text(text, parse_mode=None)
                    last_text = text
                except Exception as exc:
                    logger.debug("Download progress update skipped: %s", exc)

        file_path, title, temp_dir = await task

        await message.edit_text(
            "📤 Telegram වෙත Upload ආරම්භ කරනවා...",
            parse_mode=None,
        )

        upload_progress.last_update = 0
        started_at = time.monotonic()

        async def progress(current, total):
            await upload_progress(
                current, total, message, started_at, "📤 Upload වෙමින් පවතී"
            )

        caption = f"🎥 {title[:800]}\nQuality: {quality}"

        if quality == "audio":
            await client.send_audio(
                chat_id=message.chat.id,
                audio=file_path,
                caption=f"🎵 {title[:800]}",
                parse_mode=None,
                progress=progress,
            )
        elif Path(file_path).suffix.lower() == ".mp4":
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                caption=caption,
                parse_mode=None,
                progress=progress,
            )
        else:
            await client.send_document(
                chat_id=message.chat.id,
                document=file_path,
                caption=caption,
                parse_mode=None,
                progress=progress,
            )

        await message.delete()

    except Exception as exc:
        logger.exception("YouTube download/upload failed")
        error_text = str(exc)
        if len(error_text) > 2000:
            error_text = error_text[:2000] + "..."

        try:
            await message.edit_text(
                f"❌ Download කිරීමේදී දෝෂයක් සිදු විය:\n{error_text}",
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
