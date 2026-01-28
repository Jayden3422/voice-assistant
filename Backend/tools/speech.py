from faster_whisper import WhisperModel
import edge_tts
from edge_tts.exceptions import NoAudioReceived, WebSocketError, UnexpectedResponse
import asyncio
import os
from opencc import OpenCC
cc = OpenCC('t2s') # ç¹ä½“è½¬ç®€ä½“

# STT
_model = WhisperModel(
  "small",
  device="cpu",
  compute_type="int8"
)

def _normalize_lang(lang: str) -> str:
  if not lang:
    return "zh"
  lang = lang.lower()
  return "en" if lang.startswith("en") else "zh"

def transcribe_audio(path: str, lang: str = "zh") -> str:
  # éŸ³é¢‘è½¬æ–‡æœ¬
  normalized = _normalize_lang(lang)
  segments, _ = _model.transcribe(path, language=normalized)
  text = "".join(seg.text for seg in segments)
  if normalized == "zh":
    text = cc.convert(text)  # ç¹ä½“è½¬ç®€ä½“
  return text.strip()

# TTS
VOICE_NAME = "zh-CN-XiaoxiaoNeural"
VOICE_BY_LANG = {
  "zh": "zh-CN-XiaoxiaoNeural",
  "en": "en-US-JennyNeural",
}
VOICE_FALLBACKS = {
  "zh": ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural"],
  "en": ["en-US-JennyNeural", "en-US-GuyNeural", "en-CA-ClaraNeural"],
}

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
CONNECT_TIMEOUT = int(os.getenv("EDGE_TTS_CONNECT_TIMEOUT", "10"))
RECEIVE_TIMEOUT = int(os.getenv("EDGE_TTS_RECEIVE_TIMEOUT", "60"))

async def synthesize_speech(text: str, lang: str = "zh") -> bytes:
  # è¯­éŸ³ï¼ˆäºŒè¿›åˆ¶ wavï¼‰
  normalized = _normalize_lang(lang)
  primary_voice = VOICE_BY_LANG.get(normalized, VOICE_NAME)
  voices = [primary_voice] + [v for v in VOICE_FALLBACKS.get(normalized, []) if v != primary_voice]
  last_error = None

  for voice in voices:
    try:
      communicate = edge_tts.Communicate(
        text,
        voice,
        proxy=PROXY,
        connect_timeout=CONNECT_TIMEOUT,
        receive_timeout=RECEIVE_TIMEOUT,
      )
      audio_bytes = b""
      async for chunk in communicate.stream():
        if chunk["type"] == "audio":
          audio_bytes += chunk["data"]
      if audio_bytes:
        return audio_bytes
      last_error = NoAudioReceived("No audio was received.")
    except (NoAudioReceived, WebSocketError, UnexpectedResponse) as e:
      last_error = e
    except Exception as e:
      last_error = e

  if last_error:
    raise last_error
  raise NoAudioReceived("No audio was received.")


if __name__ == "__main__":
  # æµ‹è¯•è¯­éŸ³è¯†åˆ«
  base_dir = os.path.dirname(__file__)
  wav_path = os.path.join(base_dir, "../../Frontend/src/assets/audio/welcome.wav")
  wav_path = os.path.normpath(wav_path)
  print("STT ...")
  text = transcribe_audio(wav_path)
  print(text)

  # æµ‹è¯•è¯­éŸ³åˆæˆ
  async def test_tts():
    text = "æ‚¨å¥½ï¼Œæˆ‘æ˜¯æ‚¨çš„æ—¥ç¨‹åŠ©æ‰‹ï¼Œä½ è¦è®°å½•ä»€ä¹ˆæ—¥ç¨‹ï¼Ÿ"
    print("TTS ...")
    audio_bytes = await synthesize_speech(text, lang="zh")
    output = "test_tts.wav"
    with open(output, "wb") as f:
        f.write(audio_bytes)
    print(f"TTSï¼š{output}")
  
  asyncio.run(test_tts())
