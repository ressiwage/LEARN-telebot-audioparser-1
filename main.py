import click, time, os, re, requests, telebot
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
ALLOWED_USERNAMES=['ressiwage']

dirname=os.path.dirname(__file__)
join = os.path.join

bot = telebot.TeleBot(BOT_TOKEN, num_threads=1)

class Config:
    chat_id=None
conf = Config()

@bot.message_handler(
    commands=["start"], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def sign_handler(message):
    # click.secho(text,  fg='green')
    # bot.send_message(message.chat.id, text, parse_mode="Markdown")
    conf.chat_id = message.chat.id

@bot.message_handler(
    content_types=['voice'], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def audio_handler(message):
    # click.secho(text,  fg='green')
    # bot.send_message(message.chat.id, text, parse_mode="Markdown")
    conf.chat_id = message.chat.id
    file_info = bot.get_file(message.voice.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    with open(join(dirname, 'to_transcribe.ogg'), 'wb') as new_file:
        new_file.write(downloaded_file)
    print(join(dirname, "to_transcribe.ogg"))
    resp = MODEL.transcribe(join(dirname, "to_transcribe.ogg"), verbose=True)
    if len(resp['text']) > 4095:
        for x in range(0, len(resp['text']), 4095):
            bot.send_message(conf.chat_id, text=resp['text'][x:x+4095])
    else:
        bot.send_message(conf.chat_id, resp['text'])
    
@bot.message_handler(
    content_types=['video_note'], func=lambda message: message.chat.username in ALLOWED_USERNAMES
)
def circle_handler(message):
    conf.chat_id = message.chat.id
    file_info = bot.get_file(message.video_note.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    src =  file_info.file_path.rsplit('/')[-1]
    with open(join(dirname, src), 'wb') as new_file:
        new_file.write(downloaded_file)
    clip = mp.VideoFileClip(join(dirname, src))
    clip.audio.write_audiofile(join(dirname, "to_transcribe.ogg"))
    resp = MODEL.transcribe(join(dirname, "to_transcribe.ogg"), verbose=True)
    if len(resp['text']) > 4095:
        for x in range(0, len(resp['text']), 4095):
            bot.send_message(conf.chat_id, text=resp['text'][x:x+4095])
    else:
        bot.send_message(conf.chat_id, resp['text'])

bot.infinity_polling()