import logging
import re
import sys
import time as _time
from datetime import date as Date, time as Time, datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse  # 正确判断域名

from playwright.sync_api import (
  sync_playwright,
  BrowserContext,
  Page,
  TimeoutError as PlaywrightTimeoutError,
  Error as PlaywrightError,
)

from .models import CalendarCommand, CalendarResult

TOOLS_DIR = Path(__file__).resolve().parent
CHROME_PATH = TOOLS_DIR / "chrome-win" / "chrome.exe"

LOGIN_URL = "https://calendar.google.com/"
DAY_VIEW_BASE = "https://calendar.google.com/calendar/u/0/r/day"

CHROME_PROFILE_DIR = TOOLS_DIR.parent / "chrome_profile"

logger = logging.getLogger(__name__)

def _overlap(
  start1: datetime,
  end1: datetime,
  start2: datetime,
  end2: datetime,
) -> bool:
  return max(start1, start2) < min(end1, end2)

def _parse_event_time_from_label(
  label: str, event_date: datetime
) -> Optional[Tuple[datetime, datetime]]:
  """
    英文：
    - "10am to 11am, 测试：和 CEO 会议, Jayden Liu, ..."
    - "10:00 – 11:30"
    中文：
    - "下午10点 - 下午11点，111111，Jayden Liu，没有地点信息，2025年11月28日"
    - "下午10:00 - 下午11:30，..."
  """
  text_raw = label.strip()
  text = text_raw.lower()

  zh = re.search(
    r'(上午|下午)\s*' # period1: 上午/下午
    r'(\d{1,2})' # h1
    r'(?:[:：](\d{1,2}))?' # :mm，可选
    r'(?:点)?'
    r'(?:\s*(\d{1,2})分)?' # 兼容 “10点30分”
    r'\s*[-–－—~～至到 ]+\s*' # 连接符：-、–、至、到 等
    r'(上午|下午)?\s*' # period2：省略则复用前一个
    r'(\d{1,2})' # h2
    r'(?:[:：](\d{1,2}))?'
    r'(?:点)?'
    r'(?:\s*(\d{1,2})分)?',
    text_raw,
  )
  if zh:
    period1, h1, m1_colon, m1_fen, period2, h2, m2_colon, m2_fen = zh.groups()

    def zh_to_24h(period: str, h: str,
            m_colon: Optional[str],
            m_fen: Optional[str]) -> Tuple[int, int]:
      hour = int(h)
      if m_colon is not None:
        minute = int(m_colon)
      elif m_fen is not None:
        minute = int(m_fen)
      else:
        minute = 0

      if period == "上午":
        if hour == 12:
          hour = 0
      elif period == "下午":
        if hour < 12:
          hour += 12
      return hour, minute

    if period2 is None:
      period2 = period1

    sh, sm = zh_to_24h(period1, h1, m1_colon, m1_fen)
    eh, em = zh_to_24h(period2, h2, m2_colon, m2_fen)

    start = event_date.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = event_date.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start, end

  # 10am to 11am
  m = re.search(
    r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:to|–|-|—)\s*'
    r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',
    text,
  )
  if m:
    h1, m1, ap1, h2, m2, ap2 = m.groups()

    def to_24h(h: str, m: Optional[str], ap: str) -> Tuple[int, int]:
      hour = int(h)
      minute = int(m) if m else 0
      if ap == "pm" and hour < 12:
        hour += 12
      if ap == "am" and hour == 12:
        hour = 0
      return hour, minute

    if ap2 is None:
      ap2 = ap1

    sh, sm = to_24h(h1, m1, ap1)
    eh, em = to_24h(h2, m2, ap2)

    start = event_date.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = event_date.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start, end

  # 10:00 – 11:30
  m = re.search(r'(\d{1,2}:\d{2})\s*[–\-－—]\s*(\d{1,2}:\d{2})', text)
  if m:
    start_str, end_str = m.group(1), m.group(2)

    def to_dt(time_str: str) -> datetime:
      h, m = time_str.split(":")
      return event_date.replace(
        hour=int(h), minute=int(m), second=0, microsecond=0
      )

    start = to_dt(start_str)
    end = to_dt(end_str)
    return start, end

  return None

class GoogleCalendarAgent:
  # 持久化 Chrome Profile 操作 Google Calendar
  GOTO_TIMEOUT_MS = 60_000  # 1 分钟
  SMALL_WAIT_MS = 2_000

  def __init__(self):
    if not CHROME_PATH.exists():
      raise FileNotFoundError(
        f"未找到 Chrome 可执行文件：{CHROME_PATH}\n"
        f"请确认已将 Chrome 放在 chrome-win 目录下。"
      )
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

  def check_and_create_event(self, cmd: CalendarCommand) -> CalendarResult:
    # 基本校验
    if not isinstance(cmd.date, Date) or not isinstance(cmd.start_time, Time) or not isinstance(cmd.end_time, Time):
      return CalendarResult(
        success=False,
        conflict=False,
        message="解析到的日期或时间不完整，请再说一遍具体日期和开始/结束时间。",
      )

    if cmd.start_time >= cmd.end_time:
      return CalendarResult(
        success=False,
        conflict=False,
        message="开始时间必须早于结束时间，请重新说一下时间范围。",
      )

    logger.info(
      "处理日程请求：%s %s-%s, 标题=%s",
      cmd.date,
      cmd.start_time,
      cmd.end_time,
      cmd.title,
    )

    try:
      with sync_playwright() as p:
        # 确保已进入 Calendar 主界面
        context, op_page = self._create_or_load_context(p)

        try:
          # 打开指定日期
          self._open_day_view(op_page, cmd)

          # 冲突检测
          if self._detect_conflict(op_page, cmd):
            msg = (
              f"您在 {cmd.date.strftime('%Y-%m-%d')} "
              f"{cmd.start_time.strftime('%H:%M')} 到 {cmd.end_time.strftime('%H:%M')} "
              f"已经有日程安排了，请换一个时间。"
            )
            logger.info("检测到日程冲突")
            return CalendarResult(
              success=False,
              conflict=True,
              message=msg,
            )

          # 无冲突：创建事件
          if getattr(cmd, "end_date", None) and cmd.end_date > cmd.date:
            # undo
            self._create_multi_day_event(op_page, cmd)
          else:
            self._create_event(op_page, cmd)

          msg = (
            f"好的，已经帮你在 {cmd.date.strftime('%Y-%m-%d')} "
            f"{cmd.start_time.strftime('%H:%M')} 到 {cmd.end_time.strftime('%H:%M')} "
            f"创建了日程「{cmd.title}」。"
          )
          logger.info("日程创建成功")
          return CalendarResult(
            success=True,
            conflict=False,
            message=msg,
          )

        finally:
          try:
            context.close()
          except Exception:
            pass

    except PlaywrightTimeoutError:
      logger.exception("访问 Google 日历超时")
      return CalendarResult(
        success=False,
        conflict=False,
        message="连接 Google 日历超时，可能是网络较慢，请稍后再试。",
      )
    except PlaywrightError as e:
      logger.exception("Playwright 异常: %s", e)
      return CalendarResult(
        success=False,
        conflict=False,
        message="在操作 Google 日历时发生错误，请稍后再试。",
      )
    except Exception as e:
      logger.exception("未知异常: %s", e)
      return CalendarResult(
        success=False,
        conflict=False,
        message=f"发生未知错误，无法创建日程：{e}",
      )

  def _create_or_load_context(self, pw) -> tuple[BrowserContext, Page]:
    # 使用持久化用户数据目录
    context: BrowserContext = pw.chromium.launch_persistent_context(
      user_data_dir=str(CHROME_PROFILE_DIR),
      headless=False,  # 保持有窗口
      executable_path=str(CHROME_PATH),
      args=[
        "--start-maximized",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
      ],
    )
    # 免误刷新登录页
    self._ensure_logged_in(context)
    # 新 Tab
    op_page = context.new_page()
    return context, op_page

  def _ensure_logged_in(self, context: BrowserContext) -> None:
    # 确保进入 Calendar 主界面
    # 检查是否已经有真正的 Calendar 主界面
    for p in context.pages:
      try:
        if self._is_logged_in_calendar_page(p):
          logger.info("检测到已登录的 Calendar 页面：%s", p.url)
          return
      except PlaywrightError:
        continue

    # 检查是否已经有登录页
    has_login_page = any(
      (p.url or "").startswith("https://accounts.google.com")
      for p in context.pages
    )

    if not has_login_page:
      # 没有登录页，打开 Google Calendar 入口页面
      if context.pages:
        entry_page: Page = context.pages[0]
      else:
        entry_page = context.new_page()

      logger.info("正在打开 Google Calendar 入口页面...")

      # 只发起导航，不再强制等 load_state
      try:
        # 用较短超时时间
        entry_page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15_000)
      except PlaywrightTimeoutError:
        logger.warning("打开 Google Calendar 入口页面 DOMContentLoaded 超时，继续等待登录状态变化。")
    else:
      logger.info("检测到已有登录页（accounts.google.com），不重新打开 LOGIN_URL。")

    print(
      "\n[GoogleCalendarAgent] 如果你尚未登录，请在刚打开的 Chrome 窗口中手动完成 Google 登录和多因子认证。\n"
      "完成后，确保最终进入 Google Calendar 主界面。\n"
    )

    # 等待任意一个 tab 变成已登录的 Calendar 页面
    calendar_page = self._wait_for_logged_in_calendar(context)
    logger.info("登录完成，检测到 Calendar 页面：%s", calendar_page.url)

  def _wait_for_logged_in_calendar(
    self,
    context: BrowserContext,
    timeout_sec: int = 180,
  ) -> Page:
    deadline = _time.time() + timeout_sec

    while _time.time() < deadline:
      pages = context.pages
      if not pages:
        _time.sleep(1)
        continue

      for p in pages:
        try:
          if self._is_logged_in_calendar_page(p):
            print(f"[GoogleCalendarAgent] 检测到已登录的 Calendar 页面：{p.url}")
            return p
        except PlaywrightError:
          continue
      # 每秒检查一次
      pages[0].wait_for_timeout(1000)
    raise RuntimeError("登录超时：请重新运行并尽快在浏览器中完成登录。")

  @staticmethod
  def _is_logged_in_calendar_page(page: Page) -> bool:
    # 判断是否是已经登录完成的 Google Calendar 界面
    url = page.url or ""
    parsed = urlparse(url)

    # 必须是日历域名，防止登录页误判
    if not parsed.netloc.startswith("calendar.google.com"):
      return False

    try:
      # 日历视图网格
      grid = page.query_selector('[role="grid"][aria-label]')
      if grid:
        return True

      # 新建按钮
      create_btn = page.get_by_role(
        "button",
        name=re.compile(r"(Create|创建|新建)", re.IGNORECASE),
      )
      if create_btn.count() > 0:
        return True

    except PlaywrightError:
      return False

    return False

  def _open_day_view(self, page: Page, cmd: CalendarCommand) -> None:
    # 跳转到指定日期的日视图
    url = f"{DAY_VIEW_BASE}/{cmd.date.year}/{cmd.date.month}/{cmd.date.day}"
    logger.info("打开日视图：%s", url)
    try:
      page.goto(url, wait_until="domcontentloaded", timeout=self.GOTO_TIMEOUT_MS)
    except PlaywrightTimeoutError:
      logger.warning("goto 日视图时 DOMContentLoaded 超时，可能网络较慢，但尝试继续。")

    # 等资源加载，如果 networkidle 达不到就忽略
    try:
      page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
      logger.debug("日视图未达到 networkidle，忽略该错误，继续执行。")

    # 额外，日历 UI 渲染完
    page.wait_for_timeout(self.SMALL_WAIT_MS)

  def _detect_conflict(self, page: Page, cmd: CalendarCommand) -> bool:
    target_start = datetime.combine(cmd.date, cmd.start_time)
    target_end = datetime.combine(cmd.date, cmd.end_time)

    event_buttons = page.query_selector_all('div[role="button"][data-eventchip]')
    logger.info("本日视图中检测到 %d 个事件候选节点", len(event_buttons))

    for btn in event_buttons:
      try:
        info = btn.query_selector('.XuJrye')
        if info:
          label = info.inner_text().strip()
        else:
          # fallback：整个按钮的文本
          label = btn.inner_text().strip()
      except PlaywrightError:
        continue

      if not label:
        continue

      parsed = _parse_event_time_from_label(label, target_start)
      if not parsed:
        continue

      event_start, event_end = parsed
      if _overlap(target_start, target_end, event_start, event_end):
        logger.info("检测到冲突事件：%s", label)
        return True

    logger.info("未发现时间冲突")
    return False

  def _create_event(self, page: Page, cmd: CalendarCommand) -> None:
    logger.info("使用快捷键 'c' 打开创建事件弹窗")
    try:
      grid = page.locator('[role="grid"]').first
      if grid.count() > 0:
        # 点击一下日历区域，确保快捷键生效
        grid.click()
      else:
        page.click("body", position={"x": 50, "y": 50})
    except Exception:
      logger.warning("激活页面失败，继续尝试发送快捷键。")

    page.keyboard.press("c")
    page.wait_for_timeout(1000)

    # 标题输入
    try:
      title_input = page.wait_for_selector(
        'input[aria-label="Title"], '
        'input[aria-label="标题"]',
        timeout=8000,
      )
    except PlaywrightTimeoutError:
      logger.error("没找到事件标题输入框")
      raise RuntimeError("创建事件失败：无法定位标题输入框。")

    title_input.fill(cmd.title)
    logger.info("已填写事件标题：%s", cmd.title)

    # 日期输入框
    start_date_input = None
    end_date_input = None

    possible_start_date_labels = ["Start date", "开始日期"]
    possible_end_date_labels = ["End date", "结束日期"]

    for label in possible_start_date_labels:
      loc = page.locator(f'input[aria-label="{label}"]')
      if loc.count() > 0:
        start_date_input = loc.first
        break

    for label in possible_end_date_labels:
      loc = page.locator(f'input[aria-label="{label}"]')
      if loc.count() > 0:
        end_date_input = loc.first
        break
    
    date_str = cmd.date.strftime("%Y/%m/%d")

    if start_date_input:
      logger.info("填写开始日期：%s", date_str)
      start_date_input.click()
      start_date_input.fill(date_str)
    else:
      logger.warning("未找到开始日期输入框，可能沿用默认日期（容易出现加到 12 号的问题）。")

    if end_date_input:
      logger.info("填写结束日期：%s", date_str)
      end_date_input.click()
      end_date_input.fill(date_str)
    else:
      logger.warning("未找到结束日期输入框，可能沿用默认日期。")

    # 时间输入框
    start_time_input = None
    end_time_input = None

    possible_start_time_labels = ["Start time", "开始时间"]
    possible_end_time_labels = ["End time", "结束时间"]

    for label in possible_start_time_labels:
      loc = page.locator(f'input[aria-label="{label}"]')
      if loc.count() > 0:
        start_time_input = loc.first
        break

    for label in possible_end_time_labels:
      loc = page.locator(f'input[aria-label="{label}"]')
      if loc.count() > 0:
        end_time_input = loc.first
        break

    start_time_str = cmd.start_time.strftime("%H:%M")
    end_time_str = cmd.end_time.strftime("%H:%M")

    if start_time_input:
      logger.info("找到开始时间输入框，填写：%s", start_time_str)
      start_time_input.click()
      start_time_input.fill(start_time_str)
    else:
      logger.warning("未找到开始时间输入框，保留默认开始时间。")

    if end_time_input:
      logger.info("找到结束时间输入框，填写：%s", end_time_str)
      end_time_input.click()
      end_time_input.fill(end_time_str)
    else:
      logger.warning("未找到结束时间输入框，保留默认结束时间。")

    # 保存按钮
    save_button = None

    for label in ("Save", "保存"):
      loc = page.get_by_role("button", name=label)
      if loc.count() > 0:
        save_button = loc.first
        break

    if not save_button:
      for text in ("Save", "保存"):
        loc = page.get_by_text(text, exact=False)
        if loc.count() > 0:
          save_button = loc.first
          break

    if not save_button:
      logger.error("未找到保存按钮，可能按钮文案或结构有变化。")
      raise RuntimeError("创建事件失败：无法定位保存按钮。")

    save_button.click()
    page.wait_for_timeout(self.SMALL_WAIT_MS)
    logger.info("已点击保存事件按钮")

  def _create_multi_day_event(self, page: Page, cmd: CalendarCommand) -> None:
    # undo: 跨多日
    return None

if __name__ == "__main__":
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
  )

  # 测试：明天 10:00 - 11:00
  today = Date.today()
  tomorrow = today + timedelta(days=1)

  test_cmd = CalendarCommand(
    date=tomorrow,
    start_time=Time(hour=10, minute=0),
    end_time=Time(hour=11, minute=0),
    title="测试：和 CEO 会议",
  )

  agent = GoogleCalendarAgent()
  result = agent.check_and_create_event(test_cmd)

  print("=== CalendarResult ===")
  print("success:", result.success)
  print("conflict:", result.conflict)
  print("message:", result.message)
