from dataclasses import dataclass
from datetime import date, time
from pydantic import BaseModel

@dataclass
class CalendarCommand:
  # NLP 解析后的结构化日程
  date: date
  start_time: time
  end_time: time
  title: str

@dataclass
class CalendarResult:
  # 日历 Agent
  success: bool # 创建成功与否
  conflict: bool # 冲突与否
  message: str  # 给用户的说明

class VoiceResponse(BaseModel):
  # /api/voice
  user_text: str
  ai_text: str
  audio_base64: str  # 回复语音
