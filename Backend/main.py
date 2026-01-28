import base64
import os
from datetime import datetime
import asyncio
import logging

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tools.models import VoiceResponse
from tools.file_utils import save_temp_file
from tools.speech import transcribe_audio, synthesize_speech
from tools.nlp import parse_calendar_command
from tools.calendar_agent import GoogleCalendarAgent

app = FastAPI(title="Voice Schedule Assistant")
logger = logging.getLogger(__name__)

app.add_middleware(
  CORSMiddleware,
  allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

MESSAGES = {
  "zh": {
    "no_audio": "未收到音频文件",
    "stt_empty": "我没有听清你说的话，可以再说一遍吗？",
    "stt_failed_user": "（识别失败）",
    "nlp_failed": "我没完全听懂你的时间或标题，可以再更清楚地说一次吗？例如：明天上午十点到十一点和公司CEO会议。",
    "create_ok": "好的，已经帮你在 {date} {start} 到 {end} 创建了日程「{title}」。",
    "conflict": "你在 {date} {start} 到 {end} 已经有日程安排了，请换一个时间。",
    "create_failed": "抱歉，创建日程失败了，请稍后重试。",
  },
  "en": {
    "no_audio": "No audio file received",
    "stt_empty": "I couldn't hear you clearly. Could you say it again?",
    "stt_failed_user": "(transcription failed)",
    "nlp_failed": "I couldn't fully understand the time or title. Please say it again clearly, for example: tomorrow 10–11am meeting with the CEO.",
    "create_ok": "Done. I created an event on {date} from {start} to {end}: \"{title}\".",
    "conflict": "You already have an event on {date} from {start} to {end}. Please choose another time.",
    "create_failed": "Sorry, I couldn't create the event. Please try again later.",
  },
}

HTTP_MESSAGES = {
  "zh": {
    "tts_text_required": "text 不能为空",
    "voice_processing_failed": "服务端处理语音失败",
    "tts_failed": "语音合成失败，请稍后重试",
  },
  "en": {
    "tts_text_required": "text is required",
    "voice_processing_failed": "Failed to process voice on the server",
    "tts_failed": "Text-to-speech failed. Please try again later.",
  },
}

LOG_MESSAGES = {
  "zh": {
    "nlp_failed": "NLP 解析失败",
    "voice_error": "处理语音时异常",
    "tts_failed": "语音合成失败",
  },
  "en": {
    "nlp_failed": "NLP parse failed",
    "voice_error": "Error processing voice",
    "tts_failed": "Text-to-speech failed",
  },
}

def _normalize_lang(lang: str) -> str:
  if not lang:
    return "zh"
  lang = lang.lower()
  return "en" if lang.startswith("en") else "zh"

def _msg(lang: str, key: str, table: dict) -> str:
  return table.get(lang, table["zh"]).get(key, key)

class TTSRequest(BaseModel):
  text: str
  lang: str | None = "zh"

@app.post("/tts")
async def tts(request: TTSRequest):
  normalized_lang = _normalize_lang(request.lang or "zh")
  text = (request.text or "").strip()
  if not text:
    raise HTTPException(status_code=400, detail=_msg(normalized_lang, "tts_text_required", HTTP_MESSAGES))
  try:
    audio_bytes = await synthesize_speech(text, lang=normalized_lang)
  except Exception as e:
    logger.exception("%s: %s", _msg(normalized_lang, "tts_failed", LOG_MESSAGES), e)
    raise HTTPException(status_code=500, detail=_msg(normalized_lang, "tts_failed", HTTP_MESSAGES))
  audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
  return { "audio_base64": audio_b64 }

@app.post("/voice", response_model=VoiceResponse)
async def handle_voice(
  audio: UploadFile = File(...),
  lang: str = Form("zh"),
):
  normalized_lang = _normalize_lang(lang)
  msgs = MESSAGES[normalized_lang]
  if not audio:
    raise HTTPException(status_code=400, detail=msgs["no_audio"])

  temp_path = save_temp_file(audio)

  try:
    # STT
    user_text = transcribe_audio(temp_path, lang=normalized_lang)
    if not user_text.strip():
      ai_text = msgs["stt_empty"]
      try:
        audio_bytes = await synthesize_speech(ai_text, lang=normalized_lang)
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
      except Exception as e:
        logger.exception("%s: %s", _msg(normalized_lang, "tts_failed", LOG_MESSAGES), e)
        audio_b64 = ""
      return VoiceResponse(
        user_text=msgs["stt_failed_user"],
        ai_text=ai_text,
        audio_base64=audio_b64,
      )

    # NLP
    now = datetime.now()
    try:
      cmd = parse_calendar_command(user_text, now=now, lang=normalized_lang)
    except Exception as e:
      logger.exception("%s: %s", _msg(normalized_lang, "nlp_failed", LOG_MESSAGES), e)
      ai_text = msgs["nlp_failed"]
      try:
        audio_bytes = await synthesize_speech(ai_text, lang=normalized_lang)
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
      except Exception as e:
        logger.exception("%s: %s", _msg(normalized_lang, "tts_failed", LOG_MESSAGES), e)
        audio_b64 = ""
      return VoiceResponse(
        user_text=user_text,
        ai_text=ai_text,
        audio_base64=audio_b64,
      )
    
    # 日历 Agent
    agent = GoogleCalendarAgent(lang=normalized_lang)
    result = await asyncio.to_thread(agent.check_and_create_event, cmd)
    if normalized_lang == "en":
      if result.success:
        ai_text = msgs["create_ok"].format(
          date=cmd.date.strftime("%Y-%m-%d"),
          start=cmd.start_time.strftime("%H:%M"),
          end=cmd.end_time.strftime("%H:%M"),
          title=cmd.title,
        )
      elif result.conflict:
        ai_text = msgs["conflict"].format(
          date=cmd.date.strftime("%Y-%m-%d"),
          start=cmd.start_time.strftime("%H:%M"),
          end=cmd.end_time.strftime("%H:%M"),
        )
      else:
        ai_text = result.message or msgs["create_failed"]
    else:
      ai_text = result.message

    # TTS
    try:
      audio_bytes = await synthesize_speech(ai_text, lang=normalized_lang)
      audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
      logger.exception("%s: %s", _msg(normalized_lang, "tts_failed", LOG_MESSAGES), e)
      audio_b64 = ""

    return VoiceResponse(
      user_text=user_text,
      ai_text=ai_text,
      audio_base64=audio_b64,
    )

  except HTTPException:
    raise
  except Exception as e:
    logger.exception("%s: %s", _msg(normalized_lang, "voice_error", LOG_MESSAGES), e)
    raise HTTPException(status_code=500, detail=_msg(normalized_lang, "voice_processing_failed", HTTP_MESSAGES))
  finally:
    try:
      os.remove(temp_path)
    except Exception:
      pass

if __name__ == "__main__":
  uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
