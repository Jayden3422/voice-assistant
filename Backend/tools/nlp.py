import re
from datetime import datetime, date, time, timedelta
import dateparser
from .models import CalendarCommand

def _normalize_lang(lang: str) -> str:
  if not lang:
    return "zh"
  lang = lang.lower()
  return "en" if lang.startswith("en") else "zh"

_CN_MAP = {
  "零": 0, "〇": 0,
  "一": 1,
  "二": 2, "两": 2,
  "三": 3,
  "四": 4,
  "五": 5,
  "六": 6,
  "七": 7,
  "八": 8,
  "九": 9,
  "十": 10
}

def _cn_num_to_int(s: str) -> int:
  s = s.strip()
  if not s:
    return 0
  if len(s) == 1:
    return _CN_MAP.get(s, 0)
  if "十" in s:
    idx = s.index("十")
    if idx == 0:
      high = 1
    else:
      high = _CN_MAP.get(s[idx - 1], 1)
    if idx == len(s) - 1:
      low = 0
    else:
      low = _CN_MAP.get(s[idx + 1], 0)
    return high * 10 + low
  total = 0
  for ch in s:
    total = total * 10 + _CN_MAP.get(ch, 0)
  return total

def normalize_text(raw: str) -> str:
  text = raw.replace("，", ",").replace("。", ",")
  text = text.replace(" ", "")

  text = re.sub(r"(今天|明天|后天|明早|大后天)的", r"\1", text)
  text = re.sub(r"(上午|下午|早上|中午|晚上|傍晚)的", r"\1", text)
  text = re.sub(r"点的", "点", text)
  text = re.sub(r"的(\d{1,2}点)", r"\1", text)
  text = re.sub(r"的(\d{1,2}分)", r"\1", text)

  def repl_hour(m: re.Match) -> str:
    cn = m.group(1)
    num = _cn_num_to_int(cn)
    return f"{num}点"
  text = re.sub(r"([一二两三四五六七八九十〇零]{1,3})点", repl_hour, text)

  def repl_minute(m: re.Match) -> str:
    cn = m.group(1)
    num = _cn_num_to_int(cn)
    return f"点{num}分"
  text = re.sub(r"点(?:的)?([一二两三四五六七八九十〇零两]{1,3})分", repl_minute, text)

  def repl_half_duration(m: re.Match) -> str:
    cn = m.group(1)
    num = _cn_num_to_int(cn) + 0.5
    s = str(num).rstrip("0").rstrip(".")
    return f"{s}小时"
  text = re.sub(r"([一二两三四五六七八九十〇零两])个?半小时", repl_half_duration, text)

  def repl_full_duration(m: re.Match) -> str:
    cn = m.group(1)
    num = _cn_num_to_int(cn)
    return f"{num}小时"
  text = re.sub(r"([一二两三四五六七八九十〇零两])个?小时", repl_full_duration, text)

  return text

def normalize_text_en(raw: str) -> str:
  text = raw.strip()
  text = re.sub(r"\s+", " ", text)
  return text

def parse_date(text: str, now: datetime) -> date | None:
  if "今天" in text:
    return now.date()
  if "明天" in text or "明早" in text:
    return (now + timedelta(days=1)).date()
  if "大后天" in text:
    return (now + timedelta(days=3)).date()
  if "后天" in text:
    return (now + timedelta(days=2)).date()
  return None

def parse_date_en(text: str, now: datetime) -> date | None:
  text_l = text.lower()
  if "today" in text_l:
    return now.date()
  if "tomorrow" in text_l:
    return (now + timedelta(days=1)).date()
  if "day after tomorrow" in text_l:
    return (now + timedelta(days=2)).date()

  settings = {
    "PREFER_DATES_FROM": "future",
    "RELATIVE_BASE": now,
    "PREFER_DAY_OF_MONTH": "current",
  }
  dt = dateparser.parse(text, languages=["en"], settings=settings)
  if dt:
    return dt.date()
  return None

def parse_relative_days(text: str, now: datetime) -> date | None:
  m = re.search(r"([0-9]{1,3}|[一二两三四五六七八九十〇零]{1,3})天(后|之后)", text)
  if not m:
    return None
  num_str = m.group(1)
  if num_str.isdigit():
    days = int(num_str)
  else:
    days = _cn_num_to_int(num_str)
  return (now + timedelta(days=days)).date()

def parse_explicit_date(expr: str, now: datetime) -> date | None:
  expr = expr.strip()
  m = re.fullmatch(r"(\d{1,2})月(\d{1,2})号?", expr)
  if m:
    month = int(m.group(1))
    day = int(m.group(2))
    try:
      d = date(now.year, month, day)
    except ValueError:
      return None
    if d < now.date():
      try:
        d = date(now.year + 1, month, day)
      except ValueError:
        return None
    return d

  m2 = re.fullmatch(r"([一二两三四五六七八九十〇零]{1,3})月([一二两三四五六七八九十〇零]{1,3})号?", expr)
  if m2:
    month = _cn_num_to_int(m2.group(1))
    day = _cn_num_to_int(m2.group(2))
    try:
      d = date(now.year, month, day)
    except ValueError:
      return None
    if d < now.date():
      try:
        d = date(now.year + 1, month, day)
      except ValueError:
        return None
    return d

  settings = {
    "PREFER_DATES_FROM": "future",
    "RELATIVE_BASE": now,
    "PREFER_DAY_OF_MONTH": "current",
  }
  dt = dateparser.parse(expr, languages=["zh"], settings=settings)
  if dt:
    return dt.date()
  return None

def extract_date_expr(text: str) -> str:
  patterns = [
    r"(今天|明天|后天|明早|大后天)",
    r"([0-9一二两三四五六七八九十〇零]{1,3}天(?:后|之后))",
    r"(星期[一二三四五六日天])",
    r"(周[一二三四五六日天])",
    r"(\d{1,2}月\d{1,2}号?)",
    r"([一二两三四五六七八九十〇零]{1,3}月[一二两三四五六七八九十〇零]{1,3}号?)",
  ]
  for p in patterns:
    m = re.search(p, text)
    if m:
      return m.group(1)
  return ""

def extract_date_expr_en(text: str) -> str:
  patterns = [
    r"\b(today|tomorrow|day after tomorrow)\b",
    r"\b(next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(day)?\b",
    r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
    r"\b(\d{4}-\d{1,2}-\d{1,2})\b",
    r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+\d{1,2}(?:st|nd|rd|th)?\b",
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b",
  ]
  for p in patterns:
    m = re.search(p, text, re.IGNORECASE)
    if m:
      return m.group(0)
  return ""

def extract_date(text: str, now: datetime) -> date:
  d = parse_relative_days(text, now)
  if d:
    return d
  d = parse_date(text, now)
  if d:
    return d

  date_expr = extract_date_expr(text)
  if date_expr:
    d2 = parse_explicit_date(date_expr, now)
    if d2:
      return d2

  return now.date()

def extract_date_en(text: str, now: datetime) -> date:
  date_expr = extract_date_expr_en(text)
  if date_expr:
    d = parse_date_en(date_expr, now)
    if d:
      return d
  d2 = parse_date_en(text, now)
  if d2:
    return d2
  return now.date()

def apply_meridiem(hour: int, text: str) -> int:
  if any(k in text for k in ["下午", "晚上", "傍晚"]) and 1 <= hour <= 11:
    return hour + 12
  return hour

def extract_time_range(text: str) -> tuple[time, time, str]:
  pattern_range = re.compile(
    r"(?P<prefix>(上午|下午|早上|中午|晚上|傍晚)?)的?"
    r"(?P<shour>\d{1,2})点(?:的)?(?:(?P<sminute>\d{1,2})分?|(?P<sminute_cn>[一二两三四五六七八九十〇零]{1,3}))?(?P<shalf>半|一刻|三刻)?"
    r"(到|-|至)"
    r"(?P<ehour>\d{1,2})点?(?:的)?(?:(?P<eminute>\d{1,2})分?|(?P<eminute_cn>[一二两三四五六七八九十〇零]{1,3}))?(?P<ehalf>半|一刻|三刻)?"
  )
  m = pattern_range.search(text)
  if m:
    prefix = m.group("prefix") or ""
    sh = int(m.group("shour"))
    eh = int(m.group("ehour"))
    sminute = m.group("sminute")
    eminute = m.group("eminute")
    sminute_cn = m.group("sminute_cn")
    eminute_cn = m.group("eminute_cn")

    def hour_with_minute(h: int, minute_str: str | None, minute_cn_str: str | None, half: str | None) -> tuple[int, int]:
      if minute_str:
        minute = int(minute_str)
      elif minute_cn_str:
        minute = _cn_num_to_int(minute_cn_str)
      else:
        minute = 0
      if half == "半":
        minute = 30
      elif half == "一刻":
        minute = 15
      elif half == "三刻":
        minute = 45
      return h, minute

    sh, sm = hour_with_minute(sh, sminute, sminute_cn, m.group("shalf"))
    eh, em = hour_with_minute(eh, eminute, eminute_cn, m.group("ehalf"))

    sh = apply_meridiem(sh, prefix or text)
    eh = apply_meridiem(eh, prefix or text)

    start = time(hour=sh, minute=sm)
    end = time(hour=eh, minute=em)
    return start, end, m.group(0)

  pattern_start = re.compile(
    r"(?P<prefix>(上午|下午|早上|中午|晚上|傍晚)?)的?"
    r"(?P<hour>\d{1,2})点(?:的)?(?:(?P<minute>\d{1,2})分?)?(?P<half>半)?"
  )
  m2 = pattern_start.search(text)
  if m2:
    prefix = m2.group("prefix") or ""
    h = int(m2.group("hour"))
    minute_str = m2.group("minute")
    minute = int(minute_str) if minute_str is not None else 0
    if m2.group("half") == "半" and minute == 0:
      minute = 30

    h = apply_meridiem(h, prefix or text)
    start = time(hour=h, minute=minute)

    pattern_duration = re.compile(r"(?P<num>\d+(\.\d+)?)小时|半个?小时")
    d = pattern_duration.search(text)
    if d:
      if "半" in d.group(0) and not d.group("num"):
        delta = timedelta(minutes=30)
      else:
        hours = float(d.group("num"))
        delta = timedelta(hours=hours)
    else:
      delta = timedelta(hours=1)

    dt_dummy = datetime(2000, 1, 1, start.hour, start.minute) + delta
    end = time(hour=dt_dummy.hour, minute=dt_dummy.minute)
    time_expr = m2.group(0)
    return start, end, time_expr

  return time(8, 0), time(9, 0), ""

def extract_time_range_en(text: str) -> tuple[time, time, str]:
  text_l = text.lower()

  def parse_time_token(token: str, default_ampm: str | None = None) -> tuple[int, int, str | None]:
    token = token.strip().lower()
    token = token.replace(".", "")
    token = token.replace("o'clock", "").strip()
    if token in ("noon",):
      return 12, 0, "pm"
    if token in ("midnight",):
      return 0, 0, "am"
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", token)
    if not m:
      return 0, 0, None
    h = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    ap = m.group(3) or default_ampm
    if ap == "pm" and 1 <= h <= 11:
      h += 12
    if ap == "am" and h == 12:
      h = 0
    return h, minute, ap

  range_patterns = [
    r"(?:from\s*)?(?P<t1>\d{1,2}(?::\d{2})?\s*(?:o'clock)?\s*(?:am|pm)?)\s*(?:to|until|–|—|-|~)\s*(?P<t2>\d{1,2}(?::\d{2})?\s*(?:o'clock)?\s*(?:am|pm)?)",
    r"between\s*(?P<t1>\d{1,2}(?::\d{2})?\s*(?:o'clock)?\s*(?:am|pm)?)\s*and\s*(?P<t2>\d{1,2}(?::\d{2})?\s*(?:o'clock)?\s*(?:am|pm)?)",
  ]
  for p in range_patterns:
    m = re.search(p, text_l, re.IGNORECASE)
    if m:
      t1 = m.group("t1")
      t2 = m.group("t2")
      h1, m1, ap1 = parse_time_token(t1)
      h2, m2, _ = parse_time_token(t2, default_ampm=ap1)
      start = time(hour=h1, minute=m1)
      end = time(hour=h2, minute=m2)
      return start, end, m.group(0)

  m_at = re.search(r"(?:at|starting at|start at)\s*(?P<t>\d{1,2}(?::\d{2})?\s*(?:o'clock)?\s*(?:am|pm)?)", text_l, re.IGNORECASE)
  if m_at:
    h, m, _ = parse_time_token(m_at.group("t"))
    start = time(hour=h, minute=m)

    m_dur = re.search(r"(?:for\s*)?(?P<num>\d+(?:\.\d+)?)\s*(hours|hour|hrs|hr)\b", text_l)
    if m_dur:
      hours = float(m_dur.group("num"))
      delta = timedelta(hours=hours)
    else:
      m_min = re.search(r"(?:for\s*)?(?P<num>\d+)\s*(minutes|minute|mins|min)\b", text_l)
      if m_min:
        delta = timedelta(minutes=int(m_min.group("num")))
      elif re.search(r"half[-\s]?hour", text_l):
        delta = timedelta(minutes=30)
      else:
        delta = timedelta(hours=1)

    dt_dummy = datetime(2000, 1, 1, start.hour, start.minute) + delta
    end = time(hour=dt_dummy.hour, minute=dt_dummy.minute)
    return start, end, m_at.group(0)

  m_simple = re.search(r"\b(?P<t>\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", text_l)
  if m_simple:
    h, m, _ = parse_time_token(m_simple.group("t"))
    start = time(hour=h, minute=m)
    dt_dummy = datetime(2000, 1, 1, start.hour, start.minute) + timedelta(hours=1)
    end = time(hour=dt_dummy.hour, minute=dt_dummy.minute)
    return start, end, m_simple.group("t")

  m_oclock = re.search(r"\b(?P<t>\d{1,2}(?::\d{2})?\s*o'clock)\b", text_l)
  if m_oclock:
    h, m, _ = parse_time_token(m_oclock.group("t"))
    start = time(hour=h, minute=m)
    dt_dummy = datetime(2000, 1, 1, start.hour, start.minute) + timedelta(hours=1)
    end = time(hour=dt_dummy.hour, minute=dt_dummy.minute)
    return start, end, m_oclock.group("t")

  return time(8, 0), time(9, 0), ""

TRIGGER_PATTERNS = [
  r"给.*?(谷歌日历|googlecalendar|Google日历).*?(加上|添加|新增).*?(日程|安排)?",
  r"(给)(Google)(日历)(新增|添加)(日程|安排|事项)[，,]?",
  r"(帮我)在谷歌日历加上",
  r"(帮我)(在)?(谷歌日历)?(加上|添加|新增).*?(日程|安排)?",
  r"(帮我)添加一个日程[，,]?",
]

TRIGGER_PATTERNS_EN = [
  r"\b(add|create|schedule|set up|set|book|plan)\b.*\b(calendar|google calendar)\b",
  r"\b(add|create|schedule|set up|set|book|plan)\b.*\b(event|meeting|appointment)\b",
  r"\bremind me\b",
  r"\bput\b.*\b(on|in)\b.*\bcalendar\b",
  r"\badd\b.*\bto\b.*\bcalendar\b",
]

def remove_patterns(text: str, patterns: list[str]) -> str:
  t = text
  for p in patterns:
    t = re.sub(p, "", t, flags=re.IGNORECASE)
  return t

def extract_title(text: str, date_expr: str, time_expr: str) -> str:
  t = text
  t = remove_patterns(t, TRIGGER_PATTERNS)

  if time_expr:
    idx = t.find(time_expr)
    if idx != -1:
      t = t[idx + len(time_expr):]

  if date_expr:
    t = t.replace(date_expr, "")
  if time_expr:
    t = t.replace(time_expr, "")

  t = re.sub(r"\d+(\.\d+)?小时", "", t)
  t = re.sub(r"半个?小时", "", t)

  t = t.strip("，,。,.")
  if t.startswith(("和", "跟", "在")):
    t = t[1:]
  return t or "日程安排"

def extract_title_en(text: str, date_expr: str, time_expr: str) -> str:
  t = text
  t = remove_patterns(t, TRIGGER_PATTERNS_EN)
  if time_expr:
    idx = t.lower().find(time_expr.lower())
    if idx != -1:
      t = t[:idx] + t[idx + len(time_expr):]
  if date_expr:
    t = re.sub(re.escape(date_expr), "", t, flags=re.IGNORECASE)
  t = re.sub(r"\b(for|from|to|on|at|with|and|about)\b", " ", t, flags=re.IGNORECASE)
  t = re.sub(r"\s+", " ", t).strip(" ,.")
  return t or "Schedule"

def parse_calendar_command(raw_text: str, now: datetime | None = None, lang: str = "zh") -> CalendarCommand:
  now = now or datetime.now()
  normalized_lang = _normalize_lang(lang)

  if normalized_lang == "en":
    text = normalize_text_en(raw_text)
    d = extract_date_en(text, now)
    start, end, time_expr = extract_time_range_en(text)
    date_expr = extract_date_expr_en(text)
    title = extract_title_en(text, date_expr, time_expr)
    return CalendarCommand(date=d, start_time=start, end_time=end, title=title)

  text = normalize_text(raw_text)
  d = extract_date(text, now)
  start, end, time_expr = extract_time_range(text)
  date_expr = extract_date_expr(text)
  title = extract_title(text, date_expr, time_expr)
  return CalendarCommand(date=d, start_time=start, end_time=end, title=title)

if __name__ == "__main__":
  now = datetime(2025, 11, 26, 14, 0, 0)
  test_cases = [
    "帮我在谷歌日历加上明天上午十点到十一点和公司CEO开会",
    "明天上午十点到十一点和公司CEO开会",
    "三天后的十点到12点30和客户开会",
    "星期三下午两点到四点三十项目讨论",
    "周日早上九点半到十点半健身",
    "11月29号下午三点到五点和朋友聚会",
    "明天早上9点开会一个小时",
  ]
  for text in test_cases:
    cmd = parse_calendar_command(text, now=now, lang="zh")
    print("原始文本:", text)
    print("  日期:", cmd.date)
    print("  开始时间:", cmd.start_time)
    print("  结束时间:", cmd.end_time)
    print("  标题:", cmd.title)
    print("-" * 40)

  en_cases = [
    "add to google calendar tomorrow 10am to 11am meeting with CEO",
    "schedule meeting at 3pm for 2 hours",
    "book a call on Jan 5 at 9:30am",
  ]
  for text in en_cases:
    cmd = parse_calendar_command(text, now=now, lang="en")
    print("EN:", text)
    print("  date:", cmd.date)
    print("  start:", cmd.start_time)
    print("  end:", cmd.end_time)
    print("  title:", cmd.title)
    print("-" * 40)
