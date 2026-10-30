import os
import sys
import asyncio
import tempfile
import subprocess
import importlib.util
import shutil
import ipaddress
import logging
import time
import math
from pathlib import Path
from urllib.parse import urlparse

# ==========================================
# 1. تثبيت المكتبات
# ==========================================
def install_package(package, module=None):
    module = module or package
    if importlib.util.find_spec(module) is None:
        print(f"Installing {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", package])

install_package("python-telegram-bot", "telegram")
install_package("yt-dlp", "yt_dlp")

import yt_dlp
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

# ==========================================
# 2. الإعدادات
# ==========================================
# ضع التوكن الخاص بك هنا
BOT_TOKEN = os.getenv("BOT_TOKEN", "8331333368:AAGF7f2IYSUA-1-o56pWDTZwcGxeoGoGwaU")

# ❌ لا يمكن تفعيل هذا على الهاتف (اتركه False)
USE_LOCAL_API = False 

# 📁 الحدود
MAX_DOWNLOAD_SIZE = 2 * 1024 * 1024 * 1024    # 2 جيجا
CHUNK_SIZE = 45 * 1024 * 1024                 # تقسيم كل 45 ميجا

DOWNLOAD_TIMEOUT = 7200  # ساعتان

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("SuperDownloader")

# ==========================================
# 3. التحقق من الرابط
# ==========================================
def validate_url(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global: return False
        except ValueError: pass
        return True
    except (ValueError, TypeError):
        return False

# ==========================================
# 4. الأوامر
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🔱 **البوت الخارق (نسخة الهاتف)** 🔱\n\n"
        f"📥 التحميل: حتى **2 جيجابايت**\n"
        f"📤 الإرسال: مقسم تلقائياً (45 ميجا/جزء)\n\n"
        "أرسل الرابط هكذا:\n"
        "`/download https://example.com/video`",
        parse_mode="Markdown"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ffmpeg_status = "🟢 مثبت" if shutil.which("ffmpeg") else "🔴 غير مثبت (التقسيم معطل)"
    await update.effective_message.reply_text(
        "⚙️ **حالة النظام:**\n"
        f"FFmpeg: {ffmpeg_status}\n\n"
        f"📁 حد التحميل: {MAX_DOWNLOAD_SIZE // (1024*1024)} MB\n"
        f"🧩 التقسيم: {CHUNK_SIZE // (1024*1024)} MB/جزء\n"
        f"📦 yt-dlp: {yt_dlp.version.__version__}",
        parse_mode="Markdown"
    )

# ==========================================
# 5. محرك التحميل مع شريط التقدم
# ==========================================
async def run_download(url, folder, status_msg):
    output_template = os.path.join(folder, "%(id)s.%(ext)s")
    
    last_update = [time.time()]
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            if time.time() - last_update[0] > 5: # كل 5 ثواني
                last_update[0] = time.time()
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                percent = (downloaded / total) * 100 if total else 0
                speed = d.get('speed', 0)
                speed_mb = speed / (1024 * 1024) if speed else 0
                
                text = (
                    f"📥 **جاري التحميل...**\n"
                    f"📊 النسبة: {percent:.1f}%\n"
                    f"📦 الحجم: {downloaded / (1024*1024):.1f} MB / {total / (1024*1024):.1f} MB\n"
                    f"⚡ السرعة: {speed_mb:.1f} MB/s"
                )
                asyncio.create_task(status_msg.edit_text(text, parse_mode="Markdown"))

    options = {
        "outtmpl": output_template,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "socket_timeout": 120,
        "retries": 10,
        "fragment_retries": 10,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "max_filesize": MAX_DOWNLOAD_SIZE,
        "restrictfilenames": True,
        "concurrent_fragment_downloads": 8,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
    }

    def _do_download():
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info: raise Exception("تعذر استخراج معلومات الفيديو")
            
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                base = os.path.splitext(filename)[0]
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(base + ext):
                        filename = base + ext
                        break
            return filename

    filename = await asyncio.to_thread(_do_download)
    if not os.path.exists(filename):
        raise FileNotFoundError("لم يتم العثور على الملف بعد التحميل.")
    return filename

# ==========================================
# 6. التقسيم (الهاتف)
# ==========================================
def split_file(filename, chunk_size=CHUNK_SIZE):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("برنامج ffmpeg غير مثبت على الهاتف! يرجى تثبيته من Termux.")

    folder = os.path.dirname(filename)
    base = os.path.splitext(os.path.basename(filename))[0]
    pattern = os.path.join(folder, f"{base}_part_%03d.mp4")

    # جلب مدة الفيديو
    probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filename]
    try:
        duration = float(subprocess.check_output(probe_cmd).decode().strip())
    except Exception as e:
        raise RuntimeError(f"تعذر قراءة مدة الفيديو: {e}")

    total_size = os.path.getsize(filename)
    parts_needed = math.ceil(total_size / chunk_size)
    segment_time = max(1, int(duration / parts_needed))

    split_cmd = [
        "ffmpeg", "-y", "-i", filename, "-c", "copy", "-map", "0",
        "-segment_time", str(segment_time), "-f", "segment",
        "-reset_timestamps", "1", pattern
    ]
    subprocess.check_call(split_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    parts = sorted(p for p in Path(folder).iterdir() if p.name.startswith(f"{base}_part_") and p.suffix == ".mp4")
    if not parts: raise RuntimeError("فشل تقسيم الملف.")
    return [str(p) for p in parts]

# ==========================================
# 7. أمر التحميل الرئيسي
# ==========================================
async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not context.args:
        await message.reply_text("❌ يرجى إرسال الرابط: `/download https://...`", parse_mode="Markdown")
        return

    url = context.args[0].strip()
    if not validate_url(url):
        await message.reply_text("❌ الرابط غير صالح.")
        return

    status_msg = await message.reply_text("⏳ جاري تجهيز التحميل...")
    folder = tempfile.mkdtemp(prefix="tg_dl_")
    
    try:
        # 1. التحميل
        filename = await asyncio.wait_for(run_download(url, folder, status_msg), timeout=DOWNLOAD_TIMEOUT)
        size = os.path.getsize(filename)
        
        if size > MAX_DOWNLOAD_SIZE:
            raise ValueError("الملف أكبر من 2 جيجا.")

        # 2. الإرسال أو التقسيم
        if size <= 49 * 1024 * 1024:
            await status_msg.edit_text(f"📤 جاري الإرسال ({size / (1024*1024):.1f} MB)...")
            with open(filename, "rb") as media:
                await message.reply_document(document=media, filename=os.path.basename(filename), caption="✅ تم التحميل")
        else:
            await status_msg.edit_text(f"🧩 الملف كبير ({size / (1024*1024):.1f} MB)، جاري تقسيمه...")
            parts = await asyncio.to_thread(split_file, filename)
            total = len(parts)
            
            for idx, part in enumerate(parts, 1):
                await status_msg.edit_text(f"📤 إرسال الجزء {idx}/{total}...")
                with open(part, "rb") as media:
                    await message.reply_document(document=media, filename=os.path.basename(part), caption=f"✅ الجزء {idx}/{total}")

        await status_msg.delete()
        shutil.rmtree(folder, ignore_errors=True)

    except asyncio.TimeoutError:
        await status_msg.edit_text("⏱️ تجاوز وقت التحميل.")
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ خطأ:\n`{str(e)[:300]}`", parse_mode="Markdown")

# ==========================================
# 8. التشغيل
# ==========================================
def main():
    builder = Application.builder().token(BOT_TOKEN)
    app = builder.build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("download", download))

    print("🔱 البوت يعمل الآن على Pydroid!")
    app.run_polling()

if __name__ == "__main__":
    main()
