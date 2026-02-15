"""MCP Server for Voice-Autopilot -- exposes connectors, extractors, and tools as MCP tools."""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Ensure Backend is on sys.path so existing bare imports work
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR.parent / ".env")

# ── Eager imports: pay the cost once at startup, not during tool calls ──
# faiss takes ~45s to load on first import; do it here before the event loop starts
import faiss  # noqa: F401  (pre-warm so rag.retrieve doesn't block the event loop)
from actions.dispatcher import execute_action
from chat.autopilot_extractor import extract_autopilot_json, get_openai_client
from chat.reply_drafter import generate_reply_draft
from connectors import email_connector, linear, slack
from rag.retrieve import retrieve
from store.runs import list_runs as _list_runs

from mcp.server.fastmcp import FastMCP

# Logging to stderr only (stdout reserved for JSON-RPC over stdio transport)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_server")

mcp = FastMCP(
    "voice-autopilot",
    instructions="Voice-Autopilot: meeting scheduling, Slack/email/Linear actions, knowledge base search",
)

# ────────────────────────── Tools ──────────────────────────


@mcp.tool()
async def analyze_transcript(transcript: str, model: str | None = None) -> str:
    """Extract structured data (intent, entities, actions, summary) from a meeting or conversation transcript.

    Uses OpenAI Tool Calling to parse the transcript and return structured JSON with:
    intent, urgency, entities, summary, next_best_actions, budget, follow_up_questions, etc.

    Args:
        transcript: The meeting/conversation transcript text to analyze
        model: Optional OpenAI model override (defaults to env OPENAI_MODEL)
    """
    result = await extract_autopilot_json(transcript, model=model)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_knowledge_base(query: str, top_k: int = 5) -> str:
    """Search the indexed knowledge base using semantic similarity (FAISS).

    Returns the most relevant text chunks from the knowledge base markdown files.
    Useful for finding product info, pricing, FAQ answers, support policies, etc.

    Args:
        query: The search query text
        top_k: Number of results to return (default 5, max 20)
    """
    client = get_openai_client()
    top_k = min(max(top_k, 1), 20)
    try:
        results = await asyncio.wait_for(retrieve(query, client, top_k=top_k), timeout=30)
    except asyncio.TimeoutError:
        return json.dumps({"error": "Knowledge base search timed out (30s). Check OPENAI_API_KEY and network."})
    except Exception as e:
        logger.exception("search_knowledge_base error")
        return json.dumps({"error": f"Search failed: {str(e)[:300]}"})
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
async def send_slack_message(message: str, channel: str = "#general") -> str:
    """Send a message to a Slack channel via the configured webhook.

    Args:
        message: The message text to send
        channel: Slack channel name (default: #general)
    """
    result = await slack.execute({"message": message, "channel": channel})
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def send_email(to: str, subject: str, body: str, body_html: str = "") -> str:
    """Send an email via SMTP.

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Plain text email body
        body_html: Optional HTML email body
    """
    payload = {"to": to, "subject": subject, "body_text": body}
    if body_html:
        payload["body_html"] = body_html
    result = await email_connector.execute(payload)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def create_linear_ticket(
    title: str,
    description: str = "",
    priority: str = "medium",
) -> str:
    """Create an issue/ticket in Linear.

    Args:
        title: Ticket title
        description: Ticket description
        priority: Priority level - one of: low, medium, high, urgent (default: medium)
    """
    result = await linear.execute({
        "title": title,
        "description": description,
        "priority": priority,
    })
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def create_calendar_event(
    date: str,
    start_time: str,
    end_time: str,
    title: str,
    lang: str = "en",
) -> str:
    """Create a Google Calendar event via browser automation (Playwright).

    NOTE: Requires a pre-authenticated Google session in the chrome_profile directory.
    This will open a Chromium window.

    Args:
        date: Event date in YYYY-MM-DD format
        start_time: Start time in HH:MM format (24-hour)
        end_time: End time in HH:MM format (24-hour)
        title: Event title
        lang: Language for UI interaction - "en" or "zh" (default: en)
    """
    action = {
        "action_type": "create_meeting",
        "payload": {
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            "title": title,
        },
    }
    result = await execute_action(action, lang=lang)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def draft_reply(
    transcript: str,
    extracted_json: str,
    evidence_json: str = "[]",
) -> str:
    """Generate an AI-powered reply draft with citations based on transcript and extracted data.

    Typically used after analyze_transcript and search_knowledge_base.

    Args:
        transcript: The original conversation transcript
        extracted_json: JSON string output from analyze_transcript
        evidence_json: JSON string of evidence chunks from search_knowledge_base (default: empty list)
    """
    client = get_openai_client()
    extracted = json.loads(extracted_json)
    evidence = json.loads(evidence_json)
    result = await generate_reply_draft(client, transcript, extracted, evidence)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_runs(limit: int = 20, run_type: str | None = None) -> str:
    """List recent autopilot run history from the audit log.

    Args:
        limit: Maximum number of runs to return (default 20, max 100)
        run_type: Filter by type - "autopilot" or "voice_schedule" (default: all)
    """
    limit = min(max(limit, 1), 100)
    runs = _list_runs(limit=limit, run_type=run_type)
    return json.dumps(runs, ensure_ascii=False, indent=2, default=str)


# ────────────────────────── Resources ──────────────────────────


@mcp.resource("autopilot://schema")
def get_autopilot_schema() -> str:
    """The JSON schema used for extracting structured data from conversation transcripts."""
    schema_path = BACKEND_DIR / "business" / "autopilot_schema.json"
    return schema_path.read_text(encoding="utf-8")


@mcp.resource("autopilot://knowledge-base")
def get_knowledge_base_listing() -> str:
    """List of available knowledge base documents."""
    kb_dir = BACKEND_DIR.parent / "knowledge_base"
    if not kb_dir.exists():
        return json.dumps({"documents": [], "message": "Knowledge base directory not found"})
    docs = []
    for md_file in sorted(kb_dir.glob("*.md")):
        docs.append({
            "filename": md_file.name,
            "size_bytes": md_file.stat().st_size,
        })
    return json.dumps({"documents": docs}, indent=2)


# ────────────────────────── Entry point ──────────────────────────


if __name__ == "__main__":
    mcp.run(transport="stdio")
