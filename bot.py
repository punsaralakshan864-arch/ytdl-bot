import asyncio
import hashlib
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from pyrogram import Client, filters, idle
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# =========================
# CONFIGURATION
# =========================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()

BASE_DIR = Path(os.getenv("DATA_DIR", "."))
DOWNLOAD_DIR = BASE_DIR / "downloads"
CACHE_DIR = BASE_DIR / "cache"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

WORKER_COUNT = 1
CACHE_MAX_AGE = 7 * 24 * 60 * 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("youtube-bot")

app = Client(
    "youtube_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# Queue is created inside main() so it belongs to the running event loop.
download_queue = None
jobs = {}
active_jobs = {}
job_counter = 0


class DownloadCancelled(Exception):
    pass


def allowed_user(message_or_query):
    """If ALLOWED_CHAT_ID is set, restrict use to that Telegram user ID."""
    if not ALLOWED_CHAT_ID:
        return True
    user = getattr(message_or_query, "from_user", None)
    return bool(user and str(user.id) == ALLOWED_CHAT_ID)


def is_youtube_url(url):
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        valid_hosts = {
            "youtube.com", "www.youtube.com", "m.youtube.com",
            "music.youtube.com", "youtu.be", "www.youtu.be",
        }
        return parsed.scheme in ("http", "https") and host in valid_hosts
    except Exception:
        return False


def format_duration(seconds):
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def format_size(size):
    if not size:
        return "Unknown"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "Unknown"


def cache_key(url, quality):
    raw = f"{url.strip()}|{quality}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cache_file_for(url, quality):
    folder = CACHE_DIR / cache_key(url, quality)
    if not folder.exists():
        return None
    extensions = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus"}
    candidates = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in extensions
        and not p.name.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def clean_old_cache():
    now = time.time()
    for item in CACHE_DIR.iterdir():
        try:
            if now - item.stat().st_mtime > CACHE_MAX_AGE:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
        except Exception:
            logger.exception("Cache cleanup failed")


def extract_info(url):
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "js_runtimes": {"deno": {}},
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def quality_format(quality):
    if quality in {"360", "480", "720", "1080"}:
        return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
    if quality == "best":
        return "bestvideo+bestaudio/best"
    return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"


def download_media(url, quality, job):
    job_dir = CACHE_DIR / cache_key(url, quality)
    job_dir.mkdir(parents=True, exist_ok=True)

    cached = cache_file_for(url, quality)
    if cached:
        job["cached"] = True
        return cached
    job["cached"] = False

    def progress_hook(data):
        if job["cancel_event"].is_set():
            raise yt_dlp.utils.DownloadError("Download cancelled by user")
        if data.get("status") == "downloading":
            downloaded = data.get("downloaded_bytes", 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            if total:
                job["progress"] = min(int(downloaded * 100 / total), 100)
        elif data.get("status") == "finished":
            job["progress"] = 100

    ydl_options = {
        "format": "bestaudio/best" if quality.startswith("mp3_") else quality_format(quality),
        "outtmpl": str(job_dir / "%(title).100s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4",
        "progress_hooks": [progress_hook],
        "continuedl": True,
        "overwrites": False,
        "js_runtimes": {"deno": {}},
    }

    if quality.startswith("mp3_"):
        bitrate = quality.split("_", 1)[1]
        ydl_options["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": bitrate,
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            ydl.download([url])
        if job["cancel_event"].is_set():
            raise DownloadCancelled()

        result = cache_file_for(url, quality)
        if result:
            return result

        candidates = [
            p for p in job_dir.iterdir()
            if p.is_file() and not p.name.endswith((".part", ".ytdl", ".json"))
        ]
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return candidates[0]
        raise RuntimeError("Downloaded file could not be found.")
    except yt_dlp.utils.DownloadError as exc:
        if job["cancel_event"].is_set():
            raise DownloadCancelled() from exc
        raise


def quality_keyboard(job_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("360p", callback_data=f"dl|{job_id}|360"),
            InlineKeyboardButton("480p", callback_data=f"dl|{job_id}|480"),
        ],
        [
            InlineKeyboardButton("720p HD", callback_data=f"dl|{job_id}|720"),
            InlineKeyboardButton("1080p FHD", callback_data=f"dl|{job_id}|1080"),
        ],
        [InlineKeyboardButton("Best Quality", callback_data=f"dl|{job_id}|best")],
        [InlineKeyboardButton("🎵 MP3 128 kbps", callback_data=f"dl|{job_id}|mp3_128")],
        [
            InlineKeyboardButton("🎵 MP3 192 kbps", callback_data=f"dl|{job_id}|mp3_192"),
            InlineKeyboardButton("🎵 MP3 320 kbps", callback_data=f"dl|{job_id}|mp3_320"),
        ],
    ])


def cancel_keyboard(job_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Download", callback_data=f"cancel|{job_id}")]
    ])


@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    if not allowed_user(message):
        return await message.reply_text("⛔ You are not authorized to use this bot.")
    await message.reply_text(
        "👋 **YouTube Downloader Bot**\n\n"
        "Send me a YouTube video link.\n"
        "Choose 360p, 480p, 720p, 1080p, Best Quality, or MP3 audio.\n\n"
        "Downloads are processed through a queue."
    )


@app.on_message(filters.command("queue"))
async def queue_handler(client: Client, message: Message):
    if not allowed_user(message):
        return
    waiting = download_queue.qsize() if download_queue else 0
    await message.reply_text(
        f"📥 **Download Queue**\n\n"
        f"⏳ Waiting: `{waiting}`\n"
        f"⚙️ Currently processing: `{len(active_jobs)}`"
    )


@app.on_message(filters.text & ~filters.command(["start", "queue"]))
async def url_handler(client: Client, message: Message):
    if not allowed_user(message):
        return await message.reply_text("⛔ You are not authorized to use this bot.")

    url = (message.text or "").strip()
    if not is_youtube_url(url):
        return await message.reply_text("⚠️ Please send a valid YouTube video URL.")

    status = await message.reply_text("🔎 Getting video information...")
    try:
        info = await asyncio.to_thread(extract_info, url)
        title = info.get("title") or "Unknown title"
        duration = format_duration(info.get("duration"))
        uploader = info.get("uploader") or "Unknown"
        thumbnail = info.get("thumbnail")
        size = format_size(info.get("filesize") or info.get("filesize_approx"))
        height = info.get("height")
        resolution = f"{height}p" if height else "Varies by quality"
        caption = (
            f"🎬 **{title}**\n\n"
            f"👤 Channel: `{uploader}`\n"
            f"⏱ Duration: `{duration}`\n"
            f"📺 Available resolution: `{resolution}`\n"
            f"📦 Approx. size: `{size}`\n\n"
            "👇 **Choose download quality:**"
        )

        if thumbnail:
            try:
                await message.reply_photo(photo=thumbnail, caption=caption[:1024])
            except Exception:
                await message.reply_text(caption)
        else:
            await message.reply_text(caption)

        global job_counter
        job_counter += 1
        job_id = str(job_counter)
        jobs[job_id] = {
            "url": url,
            "title": title,
            "user_id": message.from_user.id if message.from_user else 0,
            "chat_id": message.chat.id,
            "status": "choosing",
            "cancel_event": threading.Event(),
            "progress": 0,
            "message": None,
            "cached": False,
        }
        await status.edit_text("👇 Select a format:", reply_markup=quality_keyboard(job_id))
    except Exception:
        logger.exception("Failed to extract video information")
        try:
            await status.edit_text("❌ Could not read this video.\nCheck the link or try again later.")
        except Exception:
            pass


async def process_job(job_id, quality):
    job = jobs.get(job_id)
    if not job or not download_queue:
        return
    if job["status"] != "choosing":
        return
    job["status"] = "queued"
    status_message = job.get("message")
    try:
        if status_message:
            await status_message.edit_text(
                f"📥 **Added to queue**\n\n🎬 {job['title']}\n"
                f"⏳ Queue position: {download_queue.qsize()}\n"
                "Use Cancel to stop this job.",
                reply_markup=cancel_keyboard(job_id),
            )
        await download_queue.put((job_id, quality))
    except Exception:
        logger.exception("Failed to queue job")
        job["status"] = "failed"


async def queue_worker():
    while True:
        job_id, quality = await download_queue.get()
        job = jobs.get(job_id)
        try:
            if not job:
                continue
            if job["cancel_event"].is_set():
                job["status"] = "cancelled"
                continue

            job["status"] = "downloading"
            active_jobs[job_id] = job
            status_message = job.get("message")
            if status_message:
                try:
                    await status_message.edit_text(
                        f"⬇️ **Downloading...**\n\n🎬 {job['title']}\n"
                        f"📊 Progress: `{job['progress']}%`",
                        reply_markup=cancel_keyboard(job_id),
                    )
                except Exception:
                    pass

            try:
                file_path = await asyncio.to_thread(download_media, job["url"], quality, job)
                if job["cancel_event"].is_set():
                    raise DownloadCancelled()

                job["status"] = "uploading"
                if status_message:
                    try:
                        label = "⚡ Cache hit" if job.get("cached") else "📤 Uploading"
                        await status_message.edit_text(f"{label}...\n\n🎬 {job['title']}")
                    except Exception:
                        pass

                size_bytes = file_path.stat().st_size
                suffix = file_path.suffix.lower()
                if suffix in {".mp3", ".m4a", ".opus"}:
                    await app.send_audio(
                        chat_id=job["chat_id"],
                        audio=str(file_path),
                        title=job["title"][:64],
                        caption="✅ Download completed",
                    )
                elif suffix in {".mp4", ".mkv", ".webm"}:
                    await app.send_video(
                        chat_id=job["chat_id"],
                        video=str(file_path),
                        caption=f"✅ **Download completed**\n📦 Size: `{format_size(size_bytes)}`",
                        supports_streaming=True,
                    )
                else:
                    await app.send_document(
                        chat_id=job["chat_id"],
                        document=str(file_path),
                        caption="✅ Download completed",
                    )

                job["status"] = "completed"
                if status_message:
                    try:
                        await status_message.edit_text("✅ **Done!** Your file has been sent.")
                    except Exception:
                        pass

            except DownloadCancelled:
                job["status"] = "cancelled"
                if status_message:
                    try:
                        await status_message.edit_text("❌ Download cancelled.")
                    except Exception:
                        pass
            except Exception:
                job["status"] = "failed"
                logger.exception("Download failed for job %s", job_id)
                if status_message:
                    try:
                        await status_message.edit_text(
                            "❌ Download failed.\n\nPossible reasons: unavailable video, "
                            "missing format, FFmpeg/runtime issue, or server resource limits."
                        )
                    except Exception:
                        pass
        finally:
            active_jobs.pop(job_id, None)
            download_queue.task_done()


@app.on_callback_query()
async def callback_handler(client: Client, query: CallbackQuery):
    if not query.data:
        return await query.answer()

    data = query.data.split("|")
    if data[0] == "cancel":
        if len(data) != 2:
            return await query.answer("Invalid request.", show_alert=True)
        job = jobs.get(data[1])
        if not job:
            return await query.answer("Job not found.", show_alert=True)
        if query.from_user.id != job["user_id"]:
            return await query.answer("You cannot cancel someone else's download.", show_alert=True)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return await query.answer("This job has already finished.")
        job["cancel_event"].set()
        if job["status"] in {"choosing", "queued"}:
            job["status"] = "cancelled"
            try:
                await query.message.edit_text("❌ Download cancelled.")
            except Exception:
                pass
        await query.answer("Cancellation requested.")
        return

    if data[0] != "dl" or len(data) != 3:
        return await query.answer("Invalid selection.", show_alert=True)

    _, job_id, quality = data
    job = jobs.get(job_id)
    if not job:
        return await query.answer("Job expired. Send the link again.", show_alert=True)
    if query.from_user.id != job["user_id"]:
        return await query.answer("This selection belongs to another user.", show_alert=True)
    if job["status"] != "choosing":
        return await query.answer("This job has already been selected.")

    job["message"] = query.message
    await query.answer("Added to download queue.")
    await process_job(job_id, quality)


# =========================
# STARTUP / SHUTDOWN
# =========================
async def main():
    global download_queue
    clean_old_cache()
    download_queue = asyncio.Queue()
    workers = []
    app_started = False

    try:
        await app.start()
        app_started = True

        workers = [
            asyncio.create_task(queue_worker(), name=f"download-worker-{i}")
            for i in range(WORKER_COUNT)
        ]
        logger.info("Bot started successfully.")
        await idle()

    finally:
        # Stop workers before stopping Pyrogram so no worker uses the client
        # while its dispatcher/session is being shut down.
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

        if app_started:
            try:
                await app.stop()
            except (RuntimeError, asyncio.CancelledError):
                # Railway may send SIGTERM during deployment/restart. Log
                # shutdown issues instead of printing an uncaught traceback.
                logger.exception("Pyrogram shutdown did not finish cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt.")
