python3.10 -m venv venv
source ./venv/bin/activate
sudo apt-get install ffmpeg
pip install -r requirements.txt --no-cache-dir
python3.10 main.py
