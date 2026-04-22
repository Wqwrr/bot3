import asyncio
import os
import tempfile
import re
import subprocess
import logging
from pathlib import Path

# Імпорти з python-telegram-bot v21.x
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode

# Імпорт yt-dlp
import yt_dlp

# Налаштування логування
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Константи для ConversationHandler ---
WAITING_FOR_URL, SELECTING_QUALITY = range(2)

# --- Допоміжні функції ---

def is_ffmpeg_available():
    """Перевіряє, чи встановлено ffmpeg у системі (потрібно для видалення звуку)."""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

def detect_platform(url: str) -> str:
    """Визначає платформу за посиланням."""
    if re.search(r'(youtube\.com|youtu\.be|youtube\.com/shorts)', url):
        return "YouTube"
    elif re.search(r'(tiktok\.com)', url):
        return "TikTok"
    elif re.search(r'(instagram\.com)', url):
        return "Instagram"
    elif re.search(r'(pinterest\.com|pin\.it)', url):
        return "Pinterest"
    else:
        return "Unknown"

async def get_formats_from_url(url: str):
    """Отримує інформацію про відео та доступні формати."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'listformats': False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            logger.error(f"Помилка вилучення інфи: {e}")
            return None, None

    formats = []
    # Збираємо відео формати
    if 'formats' in info:
        seen_heights = set()
        for f in info['formats']:
            height = f.get('height')
            if height and height not in seen_heights and f.get('vcodec') != 'none':
                has_audio = f.get('acodec') != 'none'
                format_note = f"{height}p {'(зі звуком)' if has_audio else '(без звуку)'}"
                
                formats.append({
                    'id': f['format_id'],
                    'height': height,
                    'label': f"🎬 {format_note}",
                    'has_audio': has_audio,
                    'ext': f.get('ext', 'mp4')
                })
                seen_heights.add(height)
                
    formats.sort(key=lambda x: x['height'], reverse=True)
    
    # Додаємо аудіо опцію
    formats.append({
        'id': 'bestaudio/best',
        'label': '🎵 Тільки аудіо (MP3)',
        'is_audio': True
    })
    
    # Додаємо опцію без звуку
    formats.append({
        'id': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'label': '🔇 Без звуку (MP4)',
        'is_mute': True
    })
    
    video_options = [f for f in formats if 'height' in f][:5]
    final_formats = video_options + [f for f in formats if 'height' not in f]
    
    return info, final_formats

async def process_mute_video(input_path: str, output_path: str):
    """Видаляє аудіо доріжку за допомогою ffmpeg."""
    if not is_ffmpeg_available():
        raise RuntimeError("FFmpeg не встановлено")
    
    cmd = [
        'ffmpeg', '-i', input_path,
        '-c:v', 'copy', '-an',
        '-y',
        output_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    if process.returncode != 0:
        raise RuntimeError("Помилка обробки ffmpeg")

# --- Обробники бота ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start - Вітання українською."""
    await update.message.reply_text(
        "👋 *Вітаю в Universal Video Downloader!*\n\n"
        "Я вмію завантажувати відео та аудіо з:\n"
        "• 📺 YouTube (включно з Shorts)\n"
        "• 🎵 TikTok\n"
        "• 📷 Instagram (Reels, Posts)\n"
        "• 📌 Pinterest (Video Pins)\n\n"
        "Просто *надішліть мені посилання*, і я запропоную доступні формати.\n\n"
        "⚠️ *Обмеження*: Telegram дозволяє завантажувати файли до 50 МБ. Якщо відео більше — я надішлю пряме посилання.",
        parse_mode=ParseMode.MARKDOWN
    )
    return WAITING_FOR_URL

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє надіслане посилання, витягує формати та показує клавіатуру."""
    url = update.message.text.strip()
    platform = detect_platform(url)
    
    if platform == "Unknown":
        await update.message.reply_text("❌ Не вдалося розпізнати платформу. Надішліть пряме посилання на відео з YouTube, TikTok, Instagram або Pinterest.")
        return WAITING_FOR_URL

    context.user_data['url'] = url
    context.user_data['platform'] = platform

    status_msg = await update.message.reply_text(f"🔍 Аналізую посилання ({platform})...")

    try:
        info, formats = await get_formats_from_url(url)
        if not formats:
            await status_msg.edit_text("❌ Не вдалося знайти доступні формати. Можливо, відео приватне або видалене.")
            return WAITING_FOR_URL

        context.user_data['info'] = info
        context.user_data['formats'] = formats

        keyboard = []
        for fmt in formats:
            callback_data = f"format_{formats.index(fmt)}"
            keyboard.append([InlineKeyboardButton(fmt['label'], callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)

        title = info.get('title', 'Без назви')
        duration = info.get('duration', 0)
        duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "N/A"
        
        await status_msg.edit_text(
            f"🎬 *{title[:100]}*\n"
            f"⏱ Тривалість: {duration_str}\n"
            f"📌 Платформа: {platform}\n\n"
            f"Оберіть якість:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        return SELECTING_QUALITY

    except Exception as e:
        logger.error(f"Помилка аналізу URL: {e}")
        await status_msg.edit_text(f"❌ Сталася помилка при отриманні даних. Перевірте посилання.\n\n({str(e)[:100]})")
        return WAITING_FOR_URL

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробка натискання кнопки вибору якості."""
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get('url')
    formats = context.user_data.get('formats')
    
    if not url or not formats:
        await query.edit_message_text("❌ Сесія застаріла. Будь ласка, надішліть посилання ще раз.")
        return WAITING_FOR_URL

    index = int(query.data.split('_')[1])
    selected_format = formats[index]
    
    await query.edit_message_text(f"⏳ Завантажую...")

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)
        output_filename = None
        
        try:
            if selected_format.get('is_audio'):
                await query.edit_message_text("🎵 Завантажую аудіо...")
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'outtmpl': str(temp_path / '%(title)s.%(ext)s'),
                    'quiet': True,
                    'noplaylist': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    output_filename = ydl.prepare_filename(info).replace('.webm', '.mp3').replace('.m4a', '.mp3')
                    
            elif selected_format.get('is_mute'):
                if not is_ffmpeg_available():
                    await query.edit_message_text("⚠️ Функція видалення звуку недоступна на цьому сервері (ffmpeg не встановлено). Спробуйте обрати звичайне відео.")
                    return WAITING_FOR_URL
                
                await query.edit_message_text("🔄 Завантажую відео зі звуком...")
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': str(temp_path / 'input.%(ext)s'),
                    'quiet': True,
                    'noplaylist': True,
                    'merge_output_format': 'mp4',
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    input_file = ydl.prepare_filename(info)
                    if not os.path.exists(input_file):
                        input_file = str(temp_path / 'input.mp4')
                
                await query.edit_message_text("🔇 Видаляю звук...")
                output_filename = str(temp_path / 'muted.mp4')
                await process_mute_video(input_file, output_filename)
                
            else:
                await query.edit_message_text(f"🎬 Завантажую відео ({selected_format.get('height', '')}p)...")
                ydl_opts = {
                    'format': selected_format['id'],
                    'outtmpl': str(temp_path / '%(title)s.%(ext)s'),
                    'quiet': True,
                    'noplaylist': True,
                    'merge_output_format': 'mp4',
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    output_filename = ydl.prepare_filename(info)
                    
            if not output_filename or not os.path.exists(output_filename):
                raise FileNotFoundError("Не вдалося знайти завантажений файл.")
                
            file_size = os.path.getsize(output_filename)
            file_size_mb = file_size / (1024 * 1024)
            
            if file_size > 50 * 1024 * 1024:
                await query.edit_message_text(
                    f"⚠️ Файл завеликий ({file_size_mb:.1f} МБ > 50 МБ).\n"
                    f"🔗 Пряме посилання для скачування в браузері:\n{url}",
                    disable_web_page_preview=True
                )
                return WAITING_FOR_URL
                
            await query.edit_message_text("📤 Відправляю в Telegram...")
            
            if selected_format.get('is_audio'):
                await query.message.reply_document(
                    document=open(output_filename, 'rb'),
                    filename=os.path.basename(output_filename),
                    caption="🎵 Ваше аудіо готове!"
                )
            else:
                await query.message.reply_video(
                    video=open(output_filename, 'rb'),
                    filename=os.path.basename(output_filename),
                    caption="✅ Готово!",
                    supports_streaming=True
                )
                
            await query.delete_message()
            return WAITING_FOR_URL
            
        except Exception as e:
            logger.error(f"Помилка завантаження/відправки: {e}")
            await query.edit_message_text(f"❌ Сталася помилка під час обробки.\n\n{str(e)[:150]}")
            return WAITING_FOR_URL

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Скасування поточного діалогу."""
    await update.message.reply_text("❌ Дію скасовано. Надішліть нове посилання.")
    return WAITING_FOR_URL

# --- Точка входу ---
def main() -> None:
    """Запуск бота."""
    TOKEN = "8710992589:AAFSVZtIy-wkgkhJIsh5X75JrLVjcfJhpL4"
    
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url)
        ],
        states={
            WAITING_FOR_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url)
            ],
            SELECTING_QUALITY: [
                CallbackQueryHandler(button_callback, pattern='^format_')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))

    print("Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()