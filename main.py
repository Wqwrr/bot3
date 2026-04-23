import asyncio
import os
import tempfile
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

# Налаштування логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Твій токен
TOKEN = '7098418047:AAFe5-A5D8rC9a85Cq0-8fS-83z4rLq-5W8'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Привіт! Надішліть мені посилання, і я спробую завантажити відео.')

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not url.startswith(('http://', 'https://')):
        return

    status_msg = await update.message.reply_text('⏳ Завантажую відео...')

    with tempfile.TemporaryDirectory() as temp_dir:
        ydl_opts = {
            # Вибираємо найкращий формат, де відео вже зі звуком (зазвичай mp4)
            'format': 'best[ext=mp4]/best',
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'noplaylist': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)

            await status_msg.edit_text('🚀 Надсилаю у Telegram...')
            with open(file_path, 'rb') as video:
                await update.message.reply_video(video=video, caption=info.get('title', ''))
            
            await status_msg.delete()

        except Exception as e:
            logging.error(f"Error: {e}")
            await status_msg.edit_text(f'❌ Помилка: {e}')

def main():
    # Створюємо додаток
    application = Application.builder().token(TOKEN).build()
    
    # Додаємо обробники
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    
    # Запуск
    application.run_polling()

if __name__ == '__main__':
    main()
