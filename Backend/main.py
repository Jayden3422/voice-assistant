import asyncio
import base64
import logging
import os
import socket
import time
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tools.models import VoiceResponse, CalendarCommand
from tools.file_utils import save_temp_file
from tools.speech import (
  delta_from_previous,
  segment_tts_text,
  synthesize_speech,
  transcribe_audio,
  transcribe_audio_bytes,
)
from chat.calendar_extractor import extract_calendar_event
from tools.calendar_agent import GoogleCalendarAgent
from api.autopilot import router as autopilot_router
from store.runs import create_run, update_run

app = FastAPI(title="Voice Schedule Assistant")
app.include_router(autopilot_router)
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
    "nlp_failed": "我没完全听懂你的时间或标题，可以再清楚地说一次吗？例如：明天上午十点到十一点和公司CEO会议。",
    "create_ok": "好的，已经帮你在 {date} {start} 到 {end} 创建了日程“{title}”。",
    "conflict": "你在 {date} {start} 到 {end} 已经有日程安排了，请换一个时间。",
    "conflict_retry": "你在 {date} {start} 到 {end} 已经有日程安排了，请告诉我一个新的时间（只说新的时间也可以）。",
    "create_failed": "抱歉，创建日程失败了，请稍后重试。",
  },
  "en": {
    "no_audio": "No audio file received",
    "stt_empty": "I couldn't hear you clearly. Could you say it again?",
    "stt_failed_user": "(transcription failed)",
    "nlp_failed": "I couldn't fully understand the time or title. Please say it again clearly, for example: tomorrow 10-11am meeting with the CEO.",
    "create_ok": "Done. I created an event on {date} from {start} to {end}: \"{title}\".",
    "conflict": "You already have an event on {date} from {start} to {end}. Please choose another time.",
    "conflict_retry": "You already have an event on {date} from {start} to {end}. Tell me a new time (you can just say the new time).",
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

VOICE_SESSION_TTL_SECONDS = 1800
VOICE_SESSIONS: dict[str, dict] = {}

STREAM_STT_UPDATE_MS = int(os.getenv("STREAM_STT_UPDATE_MS", "350"))
STREAM_STT_MIN_BYTES = int(os.getenv("STREAM_STT_MIN_BYTES", "2000"))
STREAM_STT_PARTIAL_DEBOUNCE_N = int(os.getenv("STREAM_STT_PARTIAL_DEBOUNCE_N", "2"))
STREAM_STT_MIN_SPEECH_MS = int(os.getenv("STREAM_STT_MIN_SPEECH_MS", "350"))
STREAM_STT_SILENCE_MS = int(os.getenv("STREAM_STT_SILENCE_MS", "900"))
STREAM_STT_MAX_AUDIO_MS = int(os.getenv("STREAM_STT_MAX_AUDIO_MS", "25000"))
STREAM_STT_ENERGY_THRESHOLD = float(os.getenv("STREAM_STT_ENERGY_THRESHOLD", "0.02"))
STREAM_DEFAULT_CHUNK_MS = int(os.getenv("STREAM_DEFAULT_CHUNK_MS", "80"))
STREAM_TTS_WORKERS = int(os.getenv("STREAM_TTS_WORKERS", "2"))


def _now_utc() -> datetime:
  return datetime.utcnow()


def _get_voice_session(session_id: str | None) -> dict | None:
  if not session_id:
    return None
  session = VOICE_SESSIONS.get(session_id)
  if not session:
    return None
  age = (_now_utc() - session.get("updated_at", _now_utc())).total_seconds()
  if age > VOICE_SESSION_TTL_SECONDS:
    try:
      del VOICE_SESSIONS[session_id]
    except Exception:
      pass
    return None
  return session


def _set_voice_session(session_id: str, event: dict, awaiting_update: bool) -> None:
  VOICE_SESSIONS[session_id] = {
    "event": event,
    "awaiting_update": awaiting_update,
    "updated_at": _now_utc(),
  }


async def _build_voice_response(
  user_text: str,
  ai_text: str,
  lang: str,
  session_id: str | None,
  include_audio: bool,
) -> VoiceResponse:
  audio_b64 = ""
  if include_audio:
    try:
      audio_bytes = await synthesize_speech(ai_text, lang=lang)
      audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
      logger.exception("%s: %s", _msg(lang, "tts_failed", LOG_MESSAGES), e)
      audio_b64 = ""
  return VoiceResponse(
    user_text=user_text,
    ai_text=ai_text,
    audio_base64=audio_b64,
    session_id=session_id,
  )


async def _process_calendar_text(
  user_text: str,
  normalized_lang: str,
  session_id: str | None,
  include_audio: bool,
  input_type: str = "text",
) -> VoiceResponse:
  msgs = MESSAGES[normalized_lang]
  if not user_text.strip():
    return await _build_voice_response(
      msgs["stt_failed_user"],
      msgs["stt_empty"],
      normalized_lang,
      session_id,
      include_audio,
    )

  if not session_id:
    session_id = str(uuid.uuid4())

  run_id = session_id
  from store.runs import get_run
  existing_run = get_run(run_id)

  if existing_run:
    existing_transcript = existing_run.get("transcript", "")
    if existing_transcript:
      full_transcript = f"{existing_transcript}\n---\n{user_text}"
    else:
      full_transcript = user_text
  else:
    full_transcript = user_text
    try:
      create_run(run_id, input_type, user_text, run_type="voice_schedule")
    except Exception:
      pass

  session = _get_voice_session(session_id)
  context_event = session.get("event") if session and session.get("awaiting_update") else None

  try:
    extracted = await extract_calendar_event(
      user_text,
      lang=normalized_lang,
      context_event=context_event,
    )
    update_run(run_id, transcript=full_transcript, extracted_json=extracted, status="extracted")

    cmd = CalendarCommand(
      date=datetime.strptime(extracted["date"], "%Y-%m-%d").date(),
      start_time=datetime.strptime(extracted["start_time"], "%H:%M").time(),
      end_time=datetime.strptime(extracted["end_time"], "%H:%M").time(),
      title=extracted.get("title", "Meeting" if normalized_lang == "en" else "日程安排"),
    )
  except Exception as e:
    logger.exception("%s: %s", _msg(normalized_lang, "nlp_failed", LOG_MESSAGES), e)
    update_run(run_id, status="error", error=str(e)[:1000])
    ai_text = msgs["nlp_failed"]
    return await _build_voice_response(user_text, ai_text, normalized_lang, session_id, include_audio)

  agent = GoogleCalendarAgent(lang=normalized_lang)
  result = await asyncio.to_thread(agent.check_and_create_event, cmd)

  if result.success:
    ai_text = msgs["create_ok"].format(
      date=cmd.date.strftime("%Y-%m-%d"),
      start=cmd.start_time.strftime("%H:%M"),
      end=cmd.end_time.strftime("%H:%M"),
      title=cmd.title,
    )
    _set_voice_session(session_id, extracted, awaiting_update=False)
    update_run(run_id, status="executed", actions_json={"action": "create_calendar", "success": True, "result": ai_text})
  elif result.conflict:
    ai_text = msgs["conflict_retry"].format(
      date=cmd.date.strftime("%Y-%m-%d"),
      start=cmd.start_time.strftime("%H:%M"),
      end=cmd.end_time.strftime("%H:%M"),
    )
    _set_voice_session(session_id, extracted, awaiting_update=True)
    update_run(run_id, status="conflict", actions_json={"action": "create_calendar", "conflict": True})
  else:
    ai_text = result.message or msgs["create_failed"]
    _set_voice_session(session_id, extracted, awaiting_update=False)
    update_run(run_id, status="error", error=result.message or "Failed to create calendar event")

  return await _build_voice_response(user_text, ai_text, normalized_lang, session_id, include_audio)


def _normalize_lang(lang: str) -> str:
  if not lang:
    return "zh"
  lang = lang.lower()
  return "en" if lang.startswith("en") else "zh"


def _msg(lang: str, key: str, table: dict) -> str:
  return table.get(lang, table["zh"]).get(key, key)


def _new_stream_state(lang: str, session_id: str | None, include_audio: bool) -> dict:
  return {
    "lang": _normalize_lang(lang),
    "session_id": session_id or str(uuid.uuid4()),
    "include_audio": include_audio,
    "audio_buffer": bytearray(),
    "last_stt_ts_ms": 0.0,
    "stt_task": None,
    "partial_candidate": "",
    "partial_repeats": 0,
    "last_partial_sent": "",
    "voiced_ms": 0,
    "total_audio_ms": 0,
    "last_voice_ts_ms": 0.0,
  }


def _normalize_energy(value: object) -> float:
  try:
    parsed = float(value)
  except Exception:
    return 0.0
  if parsed < 0:
    return 0.0
  if parsed > 1:
    return 1.0
  return parsed


async def _schedule_partial_stt(state: dict) -> None:
  now_ms = time.monotonic() * 1000
  if (now_ms - state["last_stt_ts_ms"]) < STREAM_STT_UPDATE_MS:
    return
  if len(state["audio_buffer"]) < STREAM_STT_MIN_BYTES:
    return
  task = state.get("stt_task")
  if task and not task.done():
    return

  snapshot = bytes(state["audio_buffer"])
  lang = state["lang"]
  state["last_stt_ts_ms"] = now_ms
  state["stt_task"] = asyncio.create_task(
    asyncio.to_thread(transcribe_audio_bytes, snapshot, lang)
  )


async def _emit_partial_if_ready(websocket: WebSocket, state: dict) -> None:
  task = state.get("stt_task")
  if not task or not task.done():
    return
  state["stt_task"] = None

  try:
    partial_text = (task.result() or "").strip()
  except Exception:
    logger.exception("Partial STT failed")
    return

  if not partial_text:
    return

  if partial_text == state["partial_candidate"]:
    state["partial_repeats"] += 1
  else:
    state["partial_candidate"] = partial_text
    state["partial_repeats"] = 1

  if state["partial_repeats"] < STREAM_STT_PARTIAL_DEBOUNCE_N:
    return

  if partial_text == state["last_partial_sent"]:
    return

  delta = delta_from_previous(state["last_partial_sent"], partial_text)
  state["last_partial_sent"] = partial_text
  await websocket.send_json(
    {
      "type": "stt_partial",
      "text": partial_text,
      "delta": delta,
    }
  )


def _should_finalize_by_silence(state: dict, now_ms: float) -> bool:
  if state["voiced_ms"] < STREAM_STT_MIN_SPEECH_MS:
    return False
  last_voice_ts_ms = state.get("last_voice_ts_ms", 0.0)
  if last_voice_ts_ms <= 0:
    return False
  return (now_ms - last_voice_ts_ms) >= STREAM_STT_SILENCE_MS


async def _stream_tts_chunks(websocket: WebSocket, text: str, lang: str) -> None:
  segments = segment_tts_text(text)
  if not segments:
    await websocket.send_json({"type": "tts_done", "interrupted": False})
    return

  worker_count = max(1, min(STREAM_TTS_WORKERS, len(segments)))
  job_queue: asyncio.Queue = asyncio.Queue()
  result_queue: asyncio.Queue = asyncio.Queue()

  for seq, seg_text in enumerate(segments):
    await job_queue.put((seq, seg_text))
  for _ in range(worker_count):
    await job_queue.put(None)

  async def worker() -> None:
    while True:
      item = await job_queue.get()
      if item is None:
        await result_queue.put(("worker_done", None))
        return

      seq, seg_text = item
      try:
        audio_bytes = await synthesize_speech(seg_text, lang=lang)
        await result_queue.put(("segment", (seq, seg_text, audio_bytes, "")))
      except Exception as e:
        logger.exception("TTS chunk synth failed at seq=%s", seq)
        await result_queue.put(("segment", (seq, seg_text, b"", str(e))))

  workers = [asyncio.create_task(worker()) for _ in range(worker_count)]

  pending: dict[int, tuple[str, bytes, str]] = {}
  next_seq = 0
  last_seq = len(segments) - 1
  done_workers = 0

  try:
    while done_workers < worker_count:
      kind, payload = await result_queue.get()
      if kind == "worker_done":
        done_workers += 1
        continue

      seq, seg_text, audio_bytes, err = payload
      pending[seq] = (seg_text, audio_bytes, err)

      while next_seq in pending:
        chunk_text, chunk_audio, chunk_err = pending.pop(next_seq)
        is_final = next_seq == last_seq

        if chunk_audio:
          await websocket.send_json(
            {
              "type": "tts_chunk",
              "sequence": next_seq,
              "text": chunk_text,
              "audio_base64": base64.b64encode(chunk_audio).decode("utf-8"),
              "is_final": is_final,
            }
          )
        elif chunk_err:
          await websocket.send_json(
            {
              "type": "tts_error",
              "sequence": next_seq,
              "message": chunk_err[:200],
              "is_final": is_final,
            }
          )
        next_seq += 1
  finally:
    for task in workers:
      if not task.done():
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

  await websocket.send_json({"type": "tts_done", "interrupted": False})


async def _finalize_stream(
  websocket: WebSocket,
  state: dict,
  final_reason: str,
) -> None:
  task = state.get("stt_task")
  if task and not task.done():
    try:
      await task
    except Exception:
      pass
  state["stt_task"] = None

  final_text = ""
  if state["audio_buffer"]:
    try:
      final_text = await asyncio.to_thread(
        transcribe_audio_bytes,
        bytes(state["audio_buffer"]),
        state["lang"],
      )
    except Exception:
      logger.exception("Final STT failed")
      final_text = ""
  final_text = (final_text or "").strip()

  final_delta = delta_from_previous(state["last_partial_sent"], final_text)
  await websocket.send_json(
    {
      "type": "stt_final",
      "text": final_text,
      "delta": final_delta,
      "reason": final_reason,
    }
  )

  response = await _process_calendar_text(
    final_text,
    state["lang"],
    state["session_id"],
    include_audio=False,
    input_type="audio",
  )

  await websocket.send_json(
    {
      "type": "ai_response",
      "user_text": response.user_text,
      "ai_text": response.ai_text,
      "session_id": response.session_id,
    }
  )

  if state["include_audio"] and response.ai_text:
    await _stream_tts_chunks(websocket, response.ai_text, state["lang"])

  await websocket.send_json(
    {
      "type": "done",
      "session_id": response.session_id,
    }
  )


class TTSRequest(BaseModel):
  text: str
  lang: str | None = "zh"


class CalendarTextRequest(BaseModel):
  text: str
  lang: str | None = "zh"
  session_id: str | None = None
  include_audio: bool | None = True


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
  return {"audio_base64": audio_b64}


@app.post("/voice", response_model=VoiceResponse)
async def handle_voice(
  audio: UploadFile | None = File(None),
  text: str | None = Form(None),
  lang: str = Form("zh"),
  session_id: str | None = Form(None),
  include_audio: bool | None = Form(True),
):
  normalized_lang = _normalize_lang(lang)
  msgs = MESSAGES[normalized_lang]
  if not audio and not (text or "").strip():
    raise HTTPException(status_code=400, detail=msgs["no_audio"])

  if text and text.strip():
    return await _process_calendar_text(text.strip(), normalized_lang, session_id, bool(include_audio), input_type="text")

  temp_path = save_temp_file(audio)

  try:
    user_text = transcribe_audio(temp_path, lang=normalized_lang)
    return await _process_calendar_text(user_text, normalized_lang, session_id, bool(include_audio), input_type="audio")

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


@app.websocket("/voice/ws")
async def handle_voice_ws(websocket: WebSocket):
  await websocket.accept()
  state = _new_stream_state(lang="zh", session_id=None, include_audio=True)

  try:
    while True:
      await _emit_partial_if_ready(websocket, state)
      packet = await websocket.receive_json()
      packet_type = (packet or {}).get("type")

      if packet_type == "start":
        state = _new_stream_state(
          lang=(packet or {}).get("lang") or "zh",
          session_id=(packet or {}).get("session_id"),
          include_audio=bool((packet or {}).get("include_audio", True)),
        )
        await websocket.send_json(
          {
            "type": "ack",
            "session_id": state["session_id"],
          }
        )
        continue

      if packet_type == "audio_chunk":
        audio_b64 = (packet or {}).get("audio_base64") or ""
        if not audio_b64:
          continue

        try:
          chunk_bytes = base64.b64decode(audio_b64)
        except Exception:
          continue

        state["audio_buffer"].extend(chunk_bytes)

        chunk_ms = int((packet or {}).get("duration_ms") or STREAM_DEFAULT_CHUNK_MS)
        chunk_ms = max(10, min(chunk_ms, 1000))
        state["total_audio_ms"] += chunk_ms

        now_ms = time.monotonic() * 1000
        energy = _normalize_energy((packet or {}).get("energy"))
        if energy >= STREAM_STT_ENERGY_THRESHOLD:
          state["voiced_ms"] += chunk_ms
          state["last_voice_ts_ms"] = now_ms

        await _schedule_partial_stt(state)
        await _emit_partial_if_ready(websocket, state)

        if state["total_audio_ms"] >= STREAM_STT_MAX_AUDIO_MS:
          await _finalize_stream(websocket, state, final_reason="max_duration")
          return

        if _should_finalize_by_silence(state, now_ms):
          await _finalize_stream(websocket, state, final_reason="silence_timeout")
          return

        continue

      if packet_type == "stop":
        await _finalize_stream(websocket, state, final_reason="user_stop")
        return

      if packet_type == "ping":
        await websocket.send_json({"type": "pong"})
        continue

      await websocket.send_json(
        {
          "type": "error",
          "message": "Unsupported message type",
        }
      )

  except WebSocketDisconnect:
    task = state.get("stt_task")
    if task and not task.done():
      task.cancel()
  except Exception as e:
    logger.exception("Voice websocket error: %s", e)
    try:
      await websocket.send_json({"type": "error", "message": str(e)[:200]})
    except Exception:
      pass


@app.post("/calendar/text", response_model=VoiceResponse)
async def handle_calendar_text(request: CalendarTextRequest):
  normalized_lang = _normalize_lang(request.lang or "zh")
  return await _process_calendar_text(
    request.text or "",
    normalized_lang,
    request.session_id,
    bool(request.include_audio),
    input_type="text",
  )


if __name__ == "__main__":
  def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
      return default
    try:
      return int(raw)
    except ValueError:
      return default


  def _is_bindable(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      try:
        sock.bind((host, port))
        return True
      except OSError:
        return False


  def _pick_port(host: str, preferred_port: int, retries: int) -> int:
    if _is_bindable(host, preferred_port):
      return preferred_port

    fallback_base = _int_env("BACKEND_FALLBACK_PORT", 8080)
    fallback_retries = max(0, _int_env("BACKEND_FALLBACK_PORT_RETRIES", 200))
    for offset in range(0, fallback_retries + 1):
      candidate = fallback_base + offset
      if candidate == preferred_port:
        continue
      if _is_bindable(host, candidate):
        logger.warning(
          "Preferred port %s is unavailable; using fallback port %s",
          preferred_port,
          candidate,
        )
        return candidate

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      try:
        sock.bind((host, 0))
      except OSError as e:
        raise RuntimeError(
          f"Failed to find any available port on host {host}. "
          "Check firewall/antivirus policy or run with a different BACKEND_HOST."
        ) from e
      random_port = int(sock.getsockname()[1])

    logger.warning(
      "No available port in configured ranges; using OS-assigned port %s",
      random_port,
    )
    return random_port


  host = os.getenv("BACKEND_HOST", "127.0.0.1")
  preferred_port = _int_env("BACKEND_PORT", 8888)
  port_retries = max(0, _int_env("BACKEND_PORT_RETRIES", 0))
  reload_enabled = os.getenv("BACKEND_RELOAD", "true").lower() in ("1", "true", "yes", "on")

  selected_port = _pick_port(host, preferred_port, port_retries)
  print(f"Starting backend on {host}:{selected_port} (reload={reload_enabled})")
  logger.info("Starting backend on %s:%s (reload=%s)", host, selected_port, reload_enabled)
  uvicorn.run("main:app", host=host, port=selected_port, reload=reload_enabled)

