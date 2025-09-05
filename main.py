import click, time, os, re, requests, telebot, sys, traceback
from telebot import types
from PIL import Image
import html as h
from html.parser import HTMLParser
import whisper
import moviepy.editor as mp
import torch
from conf import BOT_TOKEN
import subprocess
import tempfile

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

DEFAULT_MODEL = 'tiny'
MODEL = whisper.load_model(DEFAULT_MODEL).to('cpu')
DEVICE = torch.device('cpu')
ALLOWED_USERNAMES = ['ressiwage']

dirname = os.path.dirname(__file__)
join = os.path.join

bot = telebot.TeleBot(BOT_TOKEN, num_threads=1)

class Config:
    chat_id = None
    current_model = DEFAULT_MODEL
conf = Config()

segment_pattern = re.compile(r'\[\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}\.\d{3}\]\s+(.*)')

class OutputInterceptor:
    def __init__(self, original_stream, callback):
        self.original_stream = original_stream
        self.callback = callback
        self.buffer = ''

    def write(self, text):
        self.original_stream.write(text)
        self.original_stream.flush()
        self.buffer += text
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            match = segment_pattern.search(line)
            if match:
                segment_text = match.group(1).strip()
                self.callback(segment_text)

    def flush(self):
        self.original_stream.flush()

def setup_bot_commands():
    """Устанавливает меню команд для бота"""
    commands = [
        types.BotCommand("start", "Начать работу с ботом"),
        types.BotCommand("help", "Показать справку"),
        types.BotCommand("model", "Сменить модель распознавания")
    ]
    bot.set_my_commands(commands)

def send_help(chat_id):
    help_text = """
<b>Доступные команды:</b>
/start - Начать работу с ботом
/help - Показать это сообщение
/model - Сменить модель распознавания

<b>Поддерживаемые форматы:</b>
- Голосовые сообщения
- Видеосообщения (кружки)
- Ссылки на аудио/видео файлы

<b>Доступные модели:</b>
tiny, base, small, medium, large
(по умолчанию: small)
    """
    bot.send_message(chat_id, help_text, parse_mode='HTML')

@bot.message_handler(
    commands=["start"], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def sign_handler(message):
    conf.chat_id = message.chat.id
    bot.send_message(conf.chat_id, "Бот активирован. Отправьте голосовое или видеосообщение для транскрипции.")
    send_help(conf.chat_id)

@bot.message_handler(
    commands=["help"], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def help_handler(message):
    conf.chat_id = message.chat.id
    send_help(conf.chat_id)

@bot.message_handler(
    commands=["model"], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def model_handler(message):
    conf.chat_id = message.chat.id
    markup = types.InlineKeyboardMarkup()
    for model_name in WHISPER_MODELS:
        markup.add(types.InlineKeyboardButton(
            text=model_name,
            callback_data=f"set_model_{model_name}"
        ))
    bot.send_message(conf.chat_id, "Выберите модель для транскрипции:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_model_'))
def set_model_callback(call):
    model_name = call.data.replace('set_model_', '')
    if model_name in WHISPER_MODELS:
        global MODEL
        MODEL = whisper.load_model(model_name).to('cpu')
        conf.current_model = model_name
        bot.send_message(call.message.chat.id, f"✅ Модель успешно изменена на <b>{model_name}</b>", parse_mode='HTML')
    else:
        bot.answer_callback_query(call.id, "Неизвестная модель")

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
        # Используем ffmpeg для сжатия аудио
        command = [
            'ffmpeg', '-i', input_path,
            '-acodec', 'libopus',
            '-b:a', '32k',
            '-ac', '1',
            '-y',  # overwrite output file
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

def process_transcription(audio_path, chat_id):
    try:
        # Проверяем размер файла
        file_size = os.path.getsize(audio_path)
        max_size = 50 * 1024 * 1024  # 50 MB - предельный размер для Whisper
        
        if file_size > max_size:
            bot.send_message(chat_id, "⚠️ Файл слишком большой. Пытаюсь сжать...")
            compressed_path = join(dirname, 'compressed_audio.ogg')
            compress_audio(audio_path, compressed_path)
            audio_path = compressed_path
        
        status_msg = bot.send_message(chat_id, "Начало транскрипции...")
        transcribed_segments = []

        def update_segment(text):
            try:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                    text=text
                )
            except Exception as e:
                print(f"Ошибка при обновлении сообщения: {e}")

        gen = MODEL.transcribe(audio_path, verbose=False)
        while True:
            try:
                i = next(gen)
            except StopIteration as e:
                i = e.value
            if isinstance(i, str):
                update_segment(i)
            else:
                final_text = i['text']
                break

        try:
            bot.delete_message(chat_id, status_msg.message_id)
            for x in range(0, len(final_text), 4095):
                bot.send_message(chat_id, final_text[x:x+4095])
        except Exception as e:
            print(f"Ошибка при отправке финального текста: {e}")

    except Exception as e:
        error_msg = f"❌ Ошибка при транскрипции:\n<code>{h.escape(str(e))}</code>"
        bot.send_message(chat_id, error_msg, parse_mode='HTML')
        traceback_msg = f"<code>{h.escape(traceback.format_exc())}</code>"
        for x in range(0, len(traceback_msg), 4095):
            bot.send_message(chat_id, traceback_msg[x:x+4095], parse_mode='HTML')
    finally:
        # Очистка временных файлов
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except:
            pass

@bot.message_handler(
    content_types=['voice', 'audio'], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def audio_handler(message):
    try:
        conf.chat_id = message.chat.id
        
        # Проверяем размер файла
        file_size = 0
        try:
            file_info = bot.get_file(message.voice.file_id)
            file_size = message.voice.file_size
        except:
            file_info = bot.get_file(message.audio.file_id)
            file_size = message.audio.file_size
        
        # Если файл слишком большой, просим прислать ссылку
        if file_size > 20 * 1024 * 1024:  # 20 MB - лимит Telegram
            bot.send_message(conf.chat_id, "⚠️ Файл слишком большой для скачивания через Telegram. Пожалуйста, пришлите прямую ссылку на файл.")
            return
        
        downloaded_file = bot.download_file(file_info.file_path)
        audio_path = join(dirname, 'to_transcribe.ogg')
        with open(audio_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        
        process_transcription(audio_path, conf.chat_id)
        
    except Exception as e:
        error_msg = f"❌ Ошибка обработки голосового:\n<code>{h.escape(str(e))}</code>"
        bot.send_message(conf.chat_id, error_msg, parse_mode='HTML')

@bot.message_handler(
    content_types=['video_note'], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def circle_handler(message):
    try:
        conf.chat_id = message.chat.id
        
        # Проверяем размер файла
        file_size = message.video_note.file_size
        if file_size > 20 * 1024 * 1024:  # 20 MB - лимит Telegram
            bot.send_message(conf.chat_id, "⚠️ Файл слишком большой для скачивания через Telegram. Пожалуйста, пришлите прямую ссылку на файл.")
            return
        
        file_info = bot.get_file(message.video_note.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        src = file_info.file_path.rsplit('/')[-1]
        video_path = join(dirname, src)
        
        with open(video_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        
        clip = mp.VideoFileClip(video_path)
        audio_path = join(dirname, "to_transcribe.ogg")
        clip.audio.write_audiofile(audio_path)
        
        # Очищаем видео файл
        os.remove(video_path)
        
        process_transcription(audio_path, conf.chat_id)
        
    except Exception as e:
        error_msg = f"❌ Ошибка обработки видеосообщения:\n<code>{h.escape(str(e))}</code>"
        bot.send_message(conf.chat_id, error_msg, parse_mode='HTML')

@bot.message_handler(
    func=lambda message: message.chat.username in ALLOWED_USERNAMES and 
                        (message.text.startswith('http://') or message.text.startswith('https://'))
)
def url_handler(message):
    """Обработка прямых ссылок на файлы"""
    try:
        conf.chat_id = message.chat.id
        url = message.text.strip()
        
        bot.send_message(conf.chat_id, "⏬ Скачиваю файл по ссылке...")
        
        # Создаем временный файл для скачивания
        with tempfile.NamedTemporaryFile(delete=False, suffix='.download') as temp_file:
            download_path = temp_file.name
        
        # Скачиваем файл
        download_large_file(url, download_path)
        
        # Проверяем тип файла и конвертируем если нужно
        if download_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
            # Это видео файл
            bot.send_message(conf.chat_id, "🎥 Извлекаю аудио из видео...")
            audio_path = join(dirname, "extracted_audio.ogg")
            clip = mp.VideoFileClip(download_path)
            clip.audio.write_audiofile(audio_path)
            clip.close()
            os.remove(download_path)
        else:
            # Это аудио файл
            audio_path = download_path
        
        process_transcription(audio_path, conf.chat_id)
        
    except Exception as e:
        error_msg = f"❌ Ошибка обработки ссылки:\n<code>{h.escape(str(e))}</code>"
        bot.send_message(conf.chat_id, error_msg, parse_mode='HTML')

if __name__ == '__main__':
    try:
        # Устанавливаем меню команд при запуске бота
        setup_bot_commands()
        
        # Проверяем наличие необходимых утилит
        try:
            subprocess.run(['wget', '--version'], capture_output=True, check=True)
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except:
            print("Предупреждение: wget или ffmpeg не установлены. Большие файлы не будут обрабатываться.")
        
        bot.infinity_polling()
    except Exception as e:
        print(f"Бот упал с ошибкой: {e}")
        traceback.print_exc()