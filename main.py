import click, time, os, re, requests, telebot, sys, traceback
from telebot import types
from PIL import Image
import html as h
from html.parser import HTMLParser
import whisper
import moviepy.editor as mp
import torch
from conf import BOT_TOKEN

# Модели Whisper доступные для выбора
WHISPER_MODELS = {
    'tiny': 'tiny',
    'base': 'base',
    'small': 'small',
    'medium': 'medium',
    'large': 'large',
    'turbo': 'turbo'    
}

DEFAULT_MODEL = 'small'
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

def send_help(chat_id):
    help_text = """
<b>Доступные команды:</b>
/start - Начать работу с ботом
/help - Показать это сообщение
/model - Сменить модель распознавания

<b>Поддерживаемые форматы:</b>
- Голосовые сообщения
- Видеосообщения (кружки)

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
        # bot.answer_callback_query(call.id, f"Модель изменена на {model_name}")
        bot.send_message(call.message.chat.id, f"✅ Модель успешно изменена на <b>{model_name}</b>", parse_mode='HTML')
    else:
        bot.answer_callback_query(call.id, "Неизвестная модель")

def process_transcription(audio_path, chat_id):
    try:
        status_msg = bot.send_message(chat_id, "Начало транскрипции...")
        transcribed_segments = []

        def update_segment(text):
            transcribed_segments.append(text)
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

@bot.message_handler(
    content_types=['voice'], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def audio_handler(message):
    try:
        conf.chat_id = message.chat.id
        file_info = bot.get_file(message.voice.file_id)
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
        file_info = bot.get_file(message.video_note.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        src = file_info.file_path.rsplit('/')[-1]
        video_path = join(dirname, src)
        with open(video_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        clip = mp.VideoFileClip(video_path)
        audio_path = join(dirname, "to_transcribe.ogg")
        clip.audio.write_audiofile(audio_path)
        process_transcription(audio_path, conf.chat_id)
    except Exception as e:
        error_msg = f"❌ Ошибка обработки видеосообщения:\n<code>{h.escape(str(e))}</code>"
        bot.send_message(conf.chat_id, error_msg, parse_mode='HTML')

if __name__ == '__main__':
    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"Бот упал с ошибкой: {e}")
        traceback.print_exc()