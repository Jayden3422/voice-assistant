"""GPT-based calendar slot extractor using OpenAI Tool Calling."""

import json
import logging
import os
from datetime import datetime, date, time
from functools import lru_cache
from pathlib import Path

from openai import AsyncOpenAI, BadRequestError

from utils.timezone import now as now_toronto, TIMEZONE

logger = logging.getLogger(__name__)

BUSINESS_DIR = Path(__file__).resolve().parent.parent / "business"
PROMPT_DIR = Path(__file__).resolve().parent / "prompt"

_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


@lru_cache(maxsize=4)
def _load_schema(name: str = "calendar_schema.json") -> dict:
    with open(BUSINESS_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=4)
def _load_prompt_template(name: str = "calendar_extraction.txt") -> str:
    with open(PROMPT_DIR / name, "r", encoding="utf-8") as f:
        return f.read().strip()


def _build_tools(schema: dict) -> list[dict]:
    params = {k: v for k, v in schema.items() if k != "$schema"}
    return [
        {
            "type": "function",
            "function": {
                "name": "extract_calendar_event",
                "description": "Extract calendar event fields (date, start/end time, title, attendees) from user input.",
                "parameters": params,
            },
        }
    ]


async def _call_with_tools(client: AsyncOpenAI, model: str, messages: list, tools: list):
    kwargs = dict(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "extract_calendar_event"}},
        temperature=0,
    )
    try:
        return await client.chat.completions.create(**kwargs)
    except BadRequestError as e:
        if "temperature" in str(e):
            logger.info("Model %s rejects temperature=0, retrying without it", model)
            kwargs.pop("temperature")
            return await client.chat.completions.create(**kwargs)
        raise


async def extract_calendar_event(
    user_text: str,
    *,
    lang: str = "zh",
    model: str | None = None,
    context_event: dict | None = None,
) -> dict:
    """
    Use GPT Tool Calling to extract date/time/title from user input.
    Returns dict with keys: date (str YYYY-MM-DD), start_time (str HH:MM),
    end_time (str HH:MM), title (str), attendees (list[str]).
    """
    client = get_openai_client()
    model = model or os.getenv("OPENAI_CALENDAR_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    schema = _load_schema()
    template = _load_prompt_template()

    current_dt = now_toronto()
    system_prompt = template.format(
        current_datetime=current_dt.strftime("%Y-%m-%d %H:%M (%A)"),
        timezone_name=str(TIMEZONE),
    )

    if context_event:
        context_json = json.dumps(context_event, ensure_ascii=False)
        user_content = (
            "Context Event (use as defaults if not overridden):\n"
            f"{context_json}\n\n"
            f"User Input:\n{user_text}"
        )
    else:
        user_content = user_text

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    tools = _build_tools(schema)

    logger.info(
        "Calendar extraction: model=%s, lang=%s, context=%s, text=%r",
        model,
        lang,
        "yes" if context_event else "no",
        user_text[:200],
    )

    response = await _call_with_tools(client, model, messages, tools)
    tool_call = response.choices[0].message.tool_calls[0]
    raw = tool_call.function.arguments
    logger.info("Calendar extraction raw: %s", raw[:500])

    parsed = json.loads(raw)

    if context_event:
        if not parsed.get("date"):
            parsed["date"] = context_event.get("date", "")
        if not parsed.get("start_time"):
            parsed["start_time"] = context_event.get("start_time", "")
        if not parsed.get("end_time"):
            parsed["end_time"] = context_event.get("end_time", "")
        if not parsed.get("title"):
            parsed["title"] = context_event.get("title", "")
        if "attendees" not in parsed and "attendees" in context_event:
            parsed["attendees"] = context_event.get("attendees", [])

    # Validate & normalise date
    parsed["date"] = _normalise_date(parsed.get("date", ""), current_dt)
    parsed["start_time"] = _normalise_time(parsed.get("start_time", ""))
    parsed["end_time"] = _normalise_time(parsed.get("end_time", ""))
    if not parsed.get("title"):
        parsed["title"] = "Meeting" if lang == "en" else "日程安排"
    if "attendees" not in parsed:
        parsed["attendees"] = []

    return parsed


def _normalise_date(value: str, ref: datetime) -> str:
    """Ensure date is YYYY-MM-DD. GPT resolves relative dates via prompt-injected current datetime."""
    if not value:
        return ref.strftime("%Y-%m-%d")
    # Already ISO
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        pass
    # Lightweight dateparser fallback (no keyword NLP)
    try:
        import dateparser
        dt = dateparser.parse(value, settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": ref.replace(tzinfo=None),
        })
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return ref.strftime("%Y-%m-%d")


def _normalise_time(value: str) -> str:
    """Ensure time is HH:MM."""
    if not value:
        return ""
    # Already HH:MM
    try:
        datetime.strptime(value, "%H:%M")
        return value
    except ValueError:
        pass
    # Try HH:MM:SS
    try:
        t = datetime.strptime(value, "%H:%M:%S")
        return t.strftime("%H:%M")
    except ValueError:
        pass
    # Try 12-hour formats
    for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            t = datetime.strptime(value.strip(), fmt)
            return t.strftime("%H:%M")
        except ValueError:
            continue
    return value
