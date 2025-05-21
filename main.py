import click, time, os, re, requests, telebot, sys
from telebot import types
from PIL import Image
import html as h
from html.parser import HTMLParser
import whisper
import moviepy.editor as mp
import torch
from conf import BOT_TOKEN

MODEL = whisper.load_model("small").to('cpu')
DEVICE = torch.device('cpu')
ALLOWED_USERNAMES = ['ressiwage']

dirname = os.path.dirname(__file__)
join = os.path.join

bot = telebot.TeleBot(BOT_TOKEN, num_threads=1)

class Config:
    chat_id = None
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

@bot.message_handler(
    commands=["start"], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def sign_handler(message):
    conf.chat_id = message.chat.id

def process_transcription(audio_path, chat_id):
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
            update_segment( i)
        else:
            final_text = i['text']
            break
            

    try:
        
        bot.delete_message(chat_id, status_msg.message_id)
        for x in range(0, len(final_text), 4095):
            bot.send_message(chat_id, final_text[x:x+4095])
    except Exception as e:
        print(f"Ошибка при отправке финального текста: {e}")

@bot.message_handler(
    content_types=['voice'], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def audio_handler(message):
    conf.chat_id = message.chat.id
    file_info = bot.get_file(message.voice.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    audio_path = join(dirname, 'to_transcribe.ogg')
    with open(audio_path, 'wb') as new_file:
        new_file.write(downloaded_file)
    process_transcription(audio_path, conf.chat_id)

@bot.message_handler(
    content_types=['video_note'], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def circle_handler(message):
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

bot.infinity_polling()