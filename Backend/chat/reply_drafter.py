"""Generate a reply draft based on transcript, extraction, and RAG evidence."""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from openai import AsyncOpenAI, BadRequestError

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompt"


@lru_cache(maxsize=4)
def _load_prompt(name: str = "autopilot_reply_draft.txt") -> str:
    path = PROMPT_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


async def generate_reply_draft(
    client: AsyncOpenAI,
    transcript: str,
    extracted: dict,
    evidence: list[dict],
    *,
    model: str | None = None,
    run_id: str = "",
) -> dict:
    """
    Generate a reply draft with citations.
    Returns {"reply_text": "...", "citations": [...]}
    """
    model = model or os.getenv("OPENAI_AUTOPILOT_REPLY_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    system_prompt = _load_prompt("autopilot_reply_draft.txt")

    evidence_text = ""
    if evidence:
        chunks = []
        for e in evidence:
            ref = f"{e.get('doc', 'unknown')}#{e.get('chunk', 0)}"
            chunks.append(f"[{ref}] (score={e.get('score', 0):.3f}):\n{e.get('text', '')}")
        evidence_text = "\n\n---\n\n".join(chunks)
    else:
        evidence_text = "(No relevant evidence found in the knowledge base.)"

    user_content = (
        f"## User Transcript\n{transcript}\n\n"
        f"## Structured Extraction\n```json\n{json.dumps(extracted, indent=2, ensure_ascii=False)}\n```\n\n"
        f"## Retrieved Evidence\n{evidence_text}"
    )

    logger.info("[%s] Reply draft request: model=%s", run_id, model)

    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    try:
        response = await client.chat.completions.create(**kwargs)
    except BadRequestError as e:
        if "temperature" in str(e):
            logger.info("Model %s does not support temperature, retrying without it", model)
            kwargs.pop("temperature")
            response = await client.chat.completions.create(**kwargs)
        elif "response_format" in str(e):
            logger.info("Model %s does not support response_format, retrying without it", model)
            kwargs.pop("response_format", None)
            kwargs.pop("temperature", None)
            response = await client.chat.completions.create(**kwargs)
        else:
            raise

    raw = response.choices[0].message.content
    logger.info("[%s] Reply draft generated, length=%d", run_id, len(raw))

    try:
        result = json.loads(raw)
        return {
            "reply_text": result.get("reply_text", raw),
            "citations": result.get("citations", []),
        }
    except json.JSONDecodeError:
        return {"reply_text": raw, "citations": []}
