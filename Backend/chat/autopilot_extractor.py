"""OpenAI Tool Calling extractor for autopilot structured output."""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

import jsonschema
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
def _load_schema(schema_name: str = "autopilot_schema.json") -> dict:
    path = BUSINESS_DIR / schema_name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=4)
def _load_prompt(prompt_name: str = "autopilot_extraction.txt") -> str:
    path = PROMPT_DIR / prompt_name
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _build_tools(schema: dict) -> list[dict]:
    """Build OpenAI tools definition from the JSON schema."""
    # Remove $schema key which is not valid in function parameters
    params = {k: v for k, v in schema.items() if k != "$schema"}
    return [
        {
            "type": "function",
            "function": {
                "name": "parse_autopilot_conversation",
                "description": "Extract structured fields from a sales/support conversation.",
                "parameters": params,
            },
        }
    ]


async def extract_autopilot_json(
    transcript: str,
    *,
    model: str | None = None,
    schema_name: str = "autopilot_schema.json",
    prompt_name: str = "autopilot_extraction.txt",
    run_id: str = "",
) -> dict:
    """
    Call OpenAI with tool_choice=required to extract structured data.
    Returns validated JSON dict. Raises on persistent validation failure.
    """
    client = get_openai_client()
    model = model or os.getenv("OPENAI_AUTOPILOT_EXTRACT_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    schema = _load_schema(schema_name)
    prompt_template = _load_prompt(prompt_name)
    tools = _build_tools(schema)

    # Inject current datetime so GPT can resolve relative dates
    current_dt = now_toronto()
    system_prompt = prompt_template.format(
        current_datetime=current_dt.strftime("%Y-%m-%d %H:%M (%A)"),
        timezone_name=str(TIMEZONE),
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcript},
    ]

    logger.info("[%s] Extraction request: model=%s, transcript_len=%d", run_id, model, len(transcript))

    response = await _call_with_tools(client, model, messages, tools)

    tool_call = response.choices[0].message.tool_calls[0]
    raw_args = tool_call.function.arguments
    logger.info("[%s] Extraction raw output length: %d", run_id, len(raw_args))

    # First attempt: parse and validate
    validation_error_msg = ""
    try:
        parsed = json.loads(raw_args)
        _auto_fix_actions(parsed)
        _validate(parsed, schema)
        logger.info("[%s] Extraction validated on first pass", run_id)
        return parsed
    except (json.JSONDecodeError, jsonschema.ValidationError) as first_err:
        validation_error_msg = str(first_err)
        logger.warning("[%s] First pass validation failed: %s", run_id, validation_error_msg)

    # Repair pass
    repair_messages = [
        {
            "role": "system",
            "content": (
                "The previous tool call output was invalid JSON or failed schema validation. "
                "Fix ONLY the JSON to conform to the schema. Call the tool again with corrected arguments."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Invalid output:\n```\n{raw_args}\n```\n\n"
                f"Validation error: {validation_error_msg}\n\n"
                f"Schema:\n```json\n{json.dumps(schema, indent=2)}\n```"
            ),
        },
    ]

    logger.info("[%s] Starting repair pass", run_id)

    repair_response = await _call_with_tools(client, model, repair_messages, tools)

    repair_call = repair_response.choices[0].message.tool_calls[0]
    repair_args = repair_call.function.arguments

    try:
        parsed = json.loads(repair_args)
        _auto_fix_actions(parsed)
        _validate(parsed, schema)
        logger.info("[%s] Extraction validated on repair pass", run_id)
        return parsed
    except (json.JSONDecodeError, jsonschema.ValidationError) as repair_err:
        logger.error("[%s] Repair pass also failed: %s", run_id, repair_err)
        raise ValueError(f"Extraction failed after repair pass: {repair_err}") from repair_err


def _auto_fix_actions(data: dict) -> None:
    """Patch common model omissions before schema validation."""
    for action in data.get("next_best_actions", []):
        if "payload" not in action:
            action["payload"] = {}
        if "requires_confirmation" not in action:
            action["requires_confirmation"] = True


async def _call_with_tools(client: AsyncOpenAI, model: str, messages: list, tools: list):
    """Call chat completions with tool_choice; fall back to no temperature if model rejects it."""
    kwargs = dict(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "parse_autopilot_conversation"}},
        temperature=0,
    )
    try:
        return await client.chat.completions.create(**kwargs)
    except BadRequestError as e:
        if "temperature" in str(e):
            logger.info("Model %s does not support temperature=0, retrying without it", model)
            kwargs.pop("temperature")
            return await client.chat.completions.create(**kwargs)
        raise


def _validate(data: dict, schema: dict) -> None:
    """Validate data against JSON schema, resolving local $ref definitions."""
    try:
        from referencing import Registry, Resource
        resource = Resource.from_contents(schema)
        registry = Registry().with_resource("", resource)
        validator = jsonschema.Draft7Validator(schema, registry=registry)
        validator.validate(data)
    except ImportError:
        # Fallback for older jsonschema without referencing
        jsonschema.validate(instance=data, schema=schema)
