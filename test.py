import whisper
import torch, os
print(torch.cuda.is_available())
torch.cuda.init()

dirname=os.path.dirname(__file__)
MODEL = whisper.load_model("small").to('cuda:0')


resp = MODEL.transcribe(os.path.join(dirname, "расправыч 1.mp3"))
print(resp)