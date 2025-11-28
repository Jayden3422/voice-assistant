# voice-assistant
语音驱动的日程助手（Google Calendar 自动化）

后端：

python 3.10

```bash
pip install fastapi uvicorn[standard] python-multipart faster-whisper edge-tts opencc-python-reimplemented dateparser playwright
```

```bash
python -m playwright install chromium
```

之后移动`chrome-win`到`Backend\tools`文件夹
