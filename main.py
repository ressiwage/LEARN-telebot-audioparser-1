import os
import re
import sys
import time
import html as h
import traceback
import subprocess
import tempfile
from pathlib import Path

import torch, asyncio
import whisper
import moviepy.editor as mp
from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

from conf import BOT_TOKEN, API_ID, API_HASH

# Модели Whisper доступные для выбора
WHISPER_MODELS = {
    'tiny': 'tiny',
    'base': 'base',
    'small': 'small',
    'medium': 'medium',
    'large': 'large',
    'turbo': 'turbo',
    'large-v2': 'large-v2',
    'large-v3': 'large-v3',
    'large-v3-turbo': 'large-v3-turbo',
}
message_lock = asyncio.Lock()

DEFAULT_MODEL = 'tiny'
MODEL = whisper.load_model(DEFAULT_MODEL).to('cpu')
DEVICE = torch.device('cpu')
ALLOWED_USERNAMES = ['ressiwage']

dirname = os.path.dirname(__file__)
join = os.path.join

# Создаем клиент Telethon (без немедленного старта)
bot = TelegramClient('whisper_bot', API_ID, API_HASH)

class Config:
    chat_id = None
    current_model = DEFAULT_MODEL
    is_processing = False  # Флаг для блокировки обработки

conf = Config()

segment_pattern = re.compile(r'\[\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}\.\d{3}\]\s+(.*)')


async def setup_bot_commands():
    """Устанавливает меню команд для бота"""
    from telethon.tl.functions.bots import SetBotCommandsRequest
    from telethon.tl.types import BotCommand, BotCommandScopeDefault
    
    commands = [
        BotCommand(command="start", description="Начать работу с ботом"),
        BotCommand(command="help", description="Показать справку"),
        BotCommand(command="model", description="Сменить модель распознавания")
    ]
    
    await bot(SetBotCommandsRequest(
        scope=BotCommandScopeDefault(),
        lang_code='',
        commands=commands
    ))


def send_help_text():
    return """
<b>Доступные команды:</b>
/start - Начать работу с ботом
/help - Показать это сообщение
/model - Сменить модель распознавания

<b>Поддерживаемые форматы:</b>
- Голосовые сообщения
- Видеосообщения (кружки)
- Аудио файлы (.mp3, .ogg, .wav и др.)
- Ссылки на аудио/видео файлы

<b>Доступные модели:</b>
tiny, base, small, medium, large, turbo, large-v2, large-v3, large-v3-turbo
(текущая модель: {})
    """


def download_large_file(url, file_path):
    """Скачивание больших файлов с помощью wget"""
    try:
        result = subprocess.run(['wget', '-O', file_path, url],
                                capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise Exception(f"Ошибка скачивания: {result.stderr}")
        return True
    except subprocess.TimeoutExpired:
        raise Exception("Таймаут скачивания файла")
    except Exception as e:
        raise Exception(f"Ошибка при скачивании: {str(e)}")


def compress_audio(input_path, output_path):
    """Сжатие аудио файла до приемлемого размера"""
    try:
        command = [
            'ffmpeg', '-i', input_path,
            '-acodec', 'libopus',
            '-b:a', '32k',
            '-ac', '1',
            '-y',
            output_path
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise Exception(f"Ошибка сжатия аудио: {result.stderr}")
        return True
    except subprocess.TimeoutExpired:
        raise Exception("Таймаут сжатия аудио")
    except Exception as e:
        raise Exception(f"Ошибка при сжатии аудио: {str(e)}")


async def process_transcription(audio_path, chat_id, filename="unknown"):
    """Обработка транскрипции аудио файла"""
    try:
        # Проверяем размер файла
        file_size = os.path.getsize(audio_path)
        max_size = 50 * 1024 * 1024  # 50 MB

        if file_size > max_size:
            await bot.send_message(chat_id, "⚠️ Файл слишком большой. Пытаюсь сжать...")
            compressed_path = join(dirname, 'compressed_audio.ogg')
            compress_audio(audio_path, compressed_path)
            audio_path = compressed_path

        status_msg = await bot.send_message(chat_id, "Начало транскрипции...")

        async def update_segment(text):
            try:
                await bot.edit_message(chat_id, status_msg.id, text)
            except Exception as e:
                print(f"Ошибка при обновлении сообщения: {e}")

        gen = MODEL.transcribe(audio_path, verbose=False)
        final_text = ""
        
        while True:
            try:
                i = next(gen)
            except StopIteration as e:
                i = e.value
            if isinstance(i, str):
                await update_segment(i)
            else:
                final_text = i['text']
                break

        try:
            await bot.delete_messages(chat_id, status_msg.id)
            
            # Отправляем заголовок с тегами
            first_msg=None
            # Отправляем текст частями, если он слишком длинный
            for x in range(0, len(final_text), 4095):
                message = await bot.send_message(chat_id, final_text[x:x + 4095])
                if x==0:
                    first_msg = message.id

            header = f"#result #{conf.current_model} {filename}"
            await bot.send_message(chat_id, header, reply_to=first_msg)
            
        except Exception as e:
            print(f"Ошибка при отправке финального текста: {e}")

    except Exception as e:
        error_msg = f"❌ Ошибка при транскрипции:\n<code>{h.escape(str(e))}</code>"
        await bot.send_message(chat_id, error_msg, parse_mode='html')
        traceback_msg = f"<code>{h.escape(traceback.format_exc())}</code>"
        for x in range(0, len(traceback_msg), 4095):
            message = await bot.send_message(chat_id, traceback_msg[x:x + 4095], parse_mode='html')
           
    finally:
        # Очистка временных файлов
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except:
            pass


@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Обработчик команды /start"""
    sender = await event.get_sender()
    if sender.username not in ALLOWED_USERNAMES:
        return
    
    conf.chat_id = event.chat_id
    await event.respond("Бот активирован. Отправьте голосовое, аудио или видеосообщение для транскрипции.")
    await event.respond(send_help_text().format(conf.current_model), parse_mode='html')


@bot.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    """Обработчик команды /help"""
    sender = await event.get_sender()
    if sender.username not in ALLOWED_USERNAMES:
        return
    
    conf.chat_id = event.chat_id
    await event.respond(send_help_text().format(conf.current_model), parse_mode='html')


@bot.on(events.NewMessage(pattern='/model'))
async def model_handler(event):
    """Обработчик команды /model"""
    sender = await event.get_sender()
    if sender.username not in ALLOWED_USERNAMES:
        return
    
    conf.chat_id = event.chat_id
    
    # Создаем кнопки для выбора модели
    buttons = []
    for model_name in WHISPER_MODELS:
        buttons.append([Button.inline(model_name, f"set_model_{model_name}")])
    
    await event.respond("Выберите модель для транскрипции:", buttons=buttons)


@bot.on(events.CallbackQuery(pattern=b'set_model_'))
async def set_model_callback(event):
    """Обработчик выбора модели"""
    model_name = event.data.decode('utf-8').replace('set_model_', '')
    
    if model_name in WHISPER_MODELS:
        global MODEL
        MODEL = whisper.load_model(model_name).to('cpu')
        conf.current_model = model_name
        await event.answer()
        await bot.send_message(event.chat_id, 
                               f"✅ Модель успешно изменена на <b>{model_name}</b>", 
                               parse_mode='html')
    else:
        await event.answer("Неизвестная модель", alert=True)


@bot.on(events.NewMessage)
async def voice_and_audio_handler(event):
    """Обработчик голосовых сообщений, аудио и видеозаметок"""
    global message_lock
    sender = await event.get_sender()
    if sender.username not in ALLOWED_USERNAMES:
        return
    
    # Проверяем, обрабатывается ли уже сообщение
    async with message_lock:
    
        conf.chat_id = event.chat_id
        
        try:
            # Проверяем, есть ли медиа в сообщении
            if not event.message.media:
                return
            
            filename = "voice_message"

            if hasattr(event.message.media, 'document'):
                document = event.message.media.document
                
                # Проверяем атрибуты документа
                is_video_note = False
                is_audio = False
                audio_filename = None
                
                for attr in document.attributes:
                    if isinstance(attr, DocumentAttributeVideo) and attr.round_message:
                        is_video_note = True
                        break
                    if isinstance(attr, DocumentAttributeAudio) and not attr.voice:
                        is_audio = True
                        if hasattr(attr, 'title') and attr.title:
                            audio_filename = attr.title
                        elif hasattr(attr, 'performer') and attr.performer:
                            audio_filename = attr.performer

                        for attr in document.attributes:
                            if hasattr(attr, 'file_name'):
                                audio_filename = attr.file_name
                                print(f"Received media with filename: {audio_filename}")
                                break
                        else:
                            print("nf")
            
            # Обработка голосовых сообщений
            if hasattr(event.message.media, 'voice') or \
            (hasattr(event.message, 'voice') and event.message.voice):
                await bot.send_message(conf.chat_id, "⏬ Скачиваю голосовое сообщение...")
                audio_path = join(dirname, 'to_transcribe.ogg')
                await bot.download_media(event.message, audio_path)
                filename =  audio_filename or "voice_message.ogg"
                await process_transcription(audio_path, conf.chat_id, filename)
                return
            
            # Обработка видеозаметок (кружков)
            
            if hasattr(event.message.media, 'document'):
   
                
                # Обработка видеозаметок
                if is_video_note:
                    file_size = document.size
                    if file_size > 20 * 1024 * 1024:
                        await bot.send_message(conf.chat_id, 
                                            "⚠️ Файл слишком большой. Пожалуйста, пришлите прямую ссылку на файл.")
                        return
                    
                    await bot.send_message(conf.chat_id, "⏬ Скачиваю видеосообщение...")
                    video_path = join(dirname, 'video_note.mp4')
                    await bot.download_media(event.message, video_path)
                    
                    await bot.send_message(conf.chat_id, "🎥 Извлекаю аудио из видео...")
                    clip = mp.VideoFileClip(video_path)
                    audio_path = join(dirname, "to_transcribe.ogg")
                    clip.audio.write_audiofile(audio_path)
                    clip.close()
                    
                    os.remove(video_path)
                    filename = "video_note.mp4"
                    await process_transcription(audio_path, conf.chat_id, filename)
                    return
                
                # Обработка аудио файлов (mp3, ogg, wav и т.д.)
                if is_audio:
                    file_size = document.size
                    
                    # Если файл слишком большой, просим прислать ссылку
                    if file_size > 20 * 1024 * 1024:
                        await bot.send_message(conf.chat_id, 
                                            "⚠️ Файл слишком большой для скачивания через Telegram. "
                                            "Пожалуйста, пришлите прямую ссылку на файл.")
                        return
                    
                    await bot.send_message(conf.chat_id, "⏬ Скачиваю аудио файл...")
                    
                    # Получаем расширение файла
                    mime_type = document.mime_type or 'audio/ogg'
                    ext = mime_type.split('/')[-1]
                    if ext == 'mpeg':
                        ext = 'mp3'
                    # Используем имя файла из атрибутов, если есть
                    if not audio_filename:
                        audio_filename = f"audio_file.{ext}"
                    elif not audio_filename.endswith(f'.{ext}'):
                        audio_filename = f"{audio_filename}.{ext}"
                    
                    audio_path = join(dirname, f'to_transcribe.{ext}')
                    await bot.download_media(event.message, audio_path)
                    filename = audio_filename
                    await process_transcription(audio_path, conf.chat_id, filename)
                    return
        
        except Exception as e:
            error_msg = f"❌ Ошибка обработки медиа:\n<code>{h.escape(str(e))}</code>"
            await bot.send_message(conf.chat_id, error_msg, parse_mode='html')
            traceback_msg = f"<code>{h.escape(traceback.format_exc())}</code>"
            for x in range(0, len(traceback_msg), 4095):
                await bot.send_message(conf.chat_id, traceback_msg[x:x + 4095], parse_mode='html')
      


@bot.on(events.NewMessage)
async def url_handler(event):
    """Обработка прямых ссылок на файлы"""
    global message_lock
    sender = await event.get_sender()
    if sender.username not in ALLOWED_USERNAMES:
        return
    
    text = event.message.text
    if not text or not (text.startswith('http://') or text.startswith('https://')):
        return
    
    # Проверяем, обрабатывается ли уже сообщение
    async with message_lock:
    
        try:
            conf.chat_id = event.chat_id
            url = text.strip()
            
            # Извлекаем имя файла из URL
            filename = url.split('/')[-1].split('?')[0] or "downloaded_file"
            
            await bot.send_message(conf.chat_id, "⏬ Скачиваю файл по ссылке...")
            
            # Создаем временный файл для скачивания
            with tempfile.NamedTemporaryFile(delete=False, suffix='.download') as temp_file:
                download_path = temp_file.name
            
            # Скачиваем файл
            download_large_file(url, download_path)
            
            # Определяем тип файла по расширению URL
            url_lower = url.lower()
            
            # Проверяем тип файла и конвертируем если нужно
            if any(url_lower.endswith(ext) for ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm']):
                # Это видео файл
                await bot.send_message(conf.chat_id, "🎥 Извлекаю аудио из видео...")
                audio_path = join(dirname, "extracted_audio.ogg")
                clip = mp.VideoFileClip(download_path)
                clip.audio.write_audiofile(audio_path)
                clip.close()
                os.remove(download_path)
            else:
                # Это аудио файл
                audio_path = download_path
            
            await process_transcription(audio_path, conf.chat_id, filename)
            
        except Exception as e:
            error_msg = f"❌ Ошибка обработки ссылки:\n<code>{h.escape(str(e))}</code>"
            await bot.send_message(conf.chat_id, error_msg, parse_mode='html')
     

async def main():
    """Главная функция запуска бота"""
    try:
        # Запускаем бота с токеном
        await bot.start(bot_token=BOT_TOKEN)
        
        # Устанавливаем меню команд при запуске бота
        await setup_bot_commands()
        
        # Проверяем наличие необходимых утилит
        try:
            subprocess.run(['wget', '--version'], capture_output=True, check=True)
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except:
            print("Предупреждение: wget или ffmpeg не установлены. Большие файлы не будут обрабатываться.")
        
        print("Бот запущен...")
        await bot.run_until_disconnected()
        
    except Exception as e:
        print(f"Бот упал с ошибкой: {e}")
        traceback.print_exc()
    finally:
        await bot.disconnect()


if __name__ == '__main__':
    bot.loop.run_until_complete(main())