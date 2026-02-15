# Voice-Autopilot

[Chinese README](README_zh.md)

<div align="center">

**Production-Grade AI Workflow Automation System**  
*Voice-first scheduling + sales/support autopilot with structured extraction, RAG grounding, and modular action routing*

[![Tests](https://img.shields.io/badge/tests-12%20passing-success)](Backend/tests/test_autopilot.py)
[![Python](https://img.shields.io/badge/python-3.10.11-blue)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-19-61dafb)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.122.0-009688)](https://fastapi.tiangolo.com/)

</div>

---

## Core Differentiated Value

This is not a voice assistant demo. It is an AI workflow system designed for production use.

### Three Core Technical Principles

1. **Schema-Driven Reliability**
   - JSON Schema `oneOf` constrains structured outputs
   - OpenAI Tool Calling generates type-safe payloads
   - Automatic repair pass when validation fails
   - Goal: reduce parsing errors and field ambiguity

2. **Context-Aware Interaction**
   - Prompt injects `{current_datetime}`
   - `context_event` supports partial updates (for example, "move it to 3pm")
   - Multi-turn conversations work without repeatedly restating known context

3. **Production-Ready Extensible Architecture**
   - RAG retrieval with source citations
   - Modular connectors (Calendar/Slack/Email/Linear)
   - End-to-end SQLite audit logging
   - Parallel dry-run preview using `asyncio.gather`

### Business Problem It Solves

Traditional workflows require manual switching between conversations, calendars, Slack, and email.
This system consolidates that into one confirmable and traceable automation pipeline:

```text
Speak or paste a conversation
  ↓
AI structured extraction (intent/budget/entities/time)
  ↓
RAG retrieval from grounding documents
  ↓
Generate citation-backed reply draft
  ↓
Auto-enrich action payloads
  ↓
Human preview and confirmation
  ↓
Parallel execution + audit logging
```

---

## Core Workflows

### 1. Voice/Text Scheduling

```text
User: "Schedule a demo next Tuesday at 2pm"
  ↓ Whisper STT (for voice input)
  ↓ GPT Tool Calling + Schema validation
  ↓ Timezone-aware datetime resolution
  ↓ Conflict detection via Playwright
  ↓ Google Calendar automation
```

Conflict handling supports minimal update input: `"Move it to 3pm"`.
On Home, live transcription is shown while recording (browser SpeechRecognition); after stop, audio is uploaded to `/voice` for final recognition and execution.

### 2. Sales/Support Autopilot

```text
Conversation text/audio
  ↓ Strict schema extraction
  ↓ RAG retrieval
  ↓ Citation-backed reply drafting
  ↓ Action enrichment (Meeting/Slack/Email/Ticket)
  ↓ Human confirmation
  ↓ Execute and persist to SQLite
```

Auto-enrichment example: calendar title can include `{company} - {product} - {budget}`.
On Autopilot, `Start Recording` only performs live transcription into the input box; analysis is triggered manually by clicking Analyze.

---

## Architecture Highlights and Directory

### Key Design Decisions

| Component | Design Decision | Benefit |
|------|------|------|
| JSON Schema | Use `oneOf` for each action payload | Type safety, reduced ambiguity |
| Prompt datetime injection | Current time in system prompt | More stable parsing of "tomorrow/next week" |
| Context propagation | `context_event` passthrough | "Only change time" works naturally |
| RAG citations | FAISS + source tracing | Better grounding and verification |
| Routing layer | Unified routing in `actions/dispatcher.py` | Easy connector extension |
| Execution strategy | dry_run + parallel checks | Faster and safer |
| Audit layer | Full lifecycle logging in `store/runs.py` | Observable and traceable |
| Calendar automation | Playwright persistent context | No OAuth flow, MFA compatible |

### Project Structure (Deduplicated)

```text
Voice-Autopilot/
├── Frontend/
│   └── src/
│       ├── pages/               # Home / Autopilot / Record
│       ├── i18n/                # zh/en
│       ├── utils/               # Axios wrapper
│       └── router/              # route config
├── Backend/
│   ├── main.py                  # FastAPI entry (/voice, /voice/ws, /calendar/text, /tts)
│   ├── api/autopilot.py         # orchestration + APIs
│   ├── chat/                    # extraction, drafting, prompts
│   ├── rag/                     # indexing + retrieval
│   ├── actions/dispatcher.py    # action routing (dry_run/execute)
│   ├── connectors/              # Slack / Email / Linear
│   ├── tools/                   # speech / calendar_agent / models
│   ├── business/                # autopilot_schema / calendar_schema
│   ├── store/                   # SQLite init + runs CRUD
│   ├── tests/test_autopilot.py  # 12 tests
│   ├── mcp/                     # MCP server and test client
│   │   ├── mcp_server.py        # MCP Server (stdio transport)
│   │   └── test_mcp_client.py   # MCP test client
├── knowledge_base/              # RAG docs
├── .env.example
└── README.md / README_zh.md
```

### Audit Table (Core Fields)

```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL DEFAULT 'autopilot',
  input_type TEXT,
  transcript TEXT,
  extracted_json TEXT,
  evidence_json TEXT,
  reply_draft TEXT,
  actions_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  created_at TEXT,
  updated_at TEXT
);
```

---

## Tech Stack and Versions

### Frontend

| Tech | Version | Purpose |
|------|------|------|
| React | 19.0.0 | Frontend framework |
| Vite | 7.0.3 | Build and development |
| Ant Design | 6.x | UI components |
| Axios | ^1.7.9 | HTTP client |
| React Router | ^7.1.3 | Routing |
| SCSS | - | Style preprocessing |

### Backend

| Tech | Version | Purpose |
|------|------|------|
| FastAPI | ^0.122.0 | Web API |
| Uvicorn | ^0.34.0 | ASGI server |
| OpenAI | ^1.59.7 | Tool Calling / Embeddings |
| faster-whisper | ^1.1.0 | Speech recognition |
| edge-tts | ^6.1.19 | Speech synthesis |
| Playwright | ^1.50.1 | Google Calendar automation |
| FAISS (CPU) | - | Vector retrieval |
| MCP SDK | ^1.26.0 | Model Context Protocol server |
| jsonschema | ^4.23.0 | Output validation |
| pytest | ^9.0.2 | Test framework |

---

## Quick Demo

### Example: Autopilot

Input:

```text
Hi, I'm Jack from TheBestTech. We want to schedule a demo next Friday at 10am.
Budget is around $3000/month. My email is jack@example.com.
```

Extraction result (example):

```json
{
  "intent": "sales_lead",
  "urgency": "medium",
  "budget": {"currency": "CAD", "range_min": 3000, "range_max": 3000},
  "entities": {"company": "TheBestTech", "contact_name": "Jack", "email": "jack@example.com"},
  "next_best_actions": [
    {"action_type": "create_meeting", "payload": {"date": "2026-02-14", "start_time": "10:00", "end_time": "11:00", "title": "Demo"}},
    {"action_type": "send_slack_summary", "payload": {"channel": "#sales", "message": "..."}},
    {"action_type": "send_email_followup", "payload": {"to": "jack@example.com", "subject": "...", "body": "..."}}
  ]
}
```

Result: create meeting, send Slack summary, send follow-up email, and persist full run to SQLite.  
Note: all actions require human confirmation before execution (`dry_run` preview -> edit -> confirm).

---

## Environment Setup

### Frontend

`node` v20.19.5

```bash
cd Frontend
npm i
```

### Backend

`Python` 3.10.11

```bash
pip install fastapi uvicorn[standard] python-multipart faster-whisper edge-tts opencc-python-reimplemented dateparser playwright python-dotenv openai jsonschema faiss-cpu numpy httpx pytest pytest-asyncio tzdata mcp[cli]
```

Install browser runtime (required for Calendar automation):

```bash
python -m playwright install chromium
```

Copy `.env.example` to `.env` at project root:

```bash
cp .env.example .env
```

Required:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
TIMEZONE=America/Toronto
```

Optional (to enable connectors):

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
LINEAR_API_KEY=lin_api_...
LINEAR_TEAM_ID=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASS=your-app-password
SMTP_FROM=noreply@yourdomain.com
SMTP_FROM_NAME=Voice Autopilot
SMTP_SSL=false
SMTP_TIMEOUT=30
```

Note: streaming STT/TTS tuning variables (for example `STREAM_STT_*`, `TTS_SEGMENT_*`) are available in `.env.example`.

---

## Run

```bash
cd Frontend
npm run dev
```

```bash
cd Backend
python main.py
```

Build the knowledge base index (required for RAG search, only needed once; re-run after updating `knowledge_base/*.md`):

```bash
curl -X POST http://localhost:8888/ingest
```

Open: `http://localhost:5173`

---

## MCP Server

The project exposes all core capabilities as an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server, allowing Claude Desktop, Claude Code, or any MCP-compatible client to call them directly.

### Available Tools

| Tool | Description |
|------|-------------|
| `analyze_transcript` | Extract structured data (intent, entities, actions) from a conversation transcript |
| `search_knowledge_base` | Semantic search over the FAISS-indexed knowledge base |
| `send_slack_message` | Send a message to Slack via webhook |
| `send_email` | Send an email via SMTP |
| `create_linear_ticket` | Create an issue in Linear |
| `create_calendar_event` | Create a Google Calendar event via Playwright |
| `draft_reply` | Generate an AI-powered reply draft with citations |
| `list_runs` | Query autopilot run history |

### Resources

| URI | Content |
|-----|---------|
| `autopilot://schema` | The JSON extraction schema |
| `autopilot://knowledge-base` | List of knowledge base documents |

### Setup for Claude Desktop

Add to `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "voice-autopilot": {
      "command": "python",
      "args": ["D:\\Projects\\Voice-Autopilot\\Backend\\mcp\\mcp_server.py"],
      "env": {
        "PYTHONPATH": "D:\\Projects\\Voice-Autopilot\\Backend"
      }
    }
  }
}
```

### Testing

```bash
# Interactive inspector
mcp dev Backend/mcp/mcp_server.py

# Automated test client (10 tests)
python Backend/mcp/test_mcp_client.py

# Test a specific tool
python Backend/mcp/test_mcp_client.py search_knowledge_base
```

Note: first startup may take ~60s while FAISS loads. Subsequent tool calls are instant.

---

## Key Features

### 1. Bilingual Support

Covers UI, logs, errors, AI extraction, and autopilot output in Chinese and English.

### 2. Voice/Text Scheduling (AI-Driven)

- Supports natural date/time expressions in both Chinese and English (for example: "tomorrow", "next Tuesday", "2pm to 3pm")
- Home shows live transcript while recording, then runs backend recognition/scheduling after stop
- Tool Calling extracts calendar slots with schema validation
- Automatic conflict detection
- Voice or text rescheduling on conflicts

![image-20260206010955719](assets/image-20260206010955719.png)

### 3. Sales/Support Autopilot

Page: `/autopilot`

- Accepts text or audio, then extracts intent/urgency/budget/entities
- Main recording button performs live transcription into the input box only (no auto-run)
- Retrieves RAG evidence and drafts citation-backed replies
- Auto-populates action payloads with human-edit controls
- Includes `send_slack_summary` by default
- Adds `send_email_followup` when an email is detected
- Supports conflict rescheduling via `adjust-time`

![image-20260206023537765](assets/image-20260206023537765.png)

Meeting in calendar:
![image-20260206023600869](assets/image-20260206023600869.png)

In Slack:
![image-20260206023621965](assets/image-20260206023621965.png)

Response email:
![image-20260206023639686](assets/image-20260206023639686.png)

Calendar conflict and reschedule:
![image-20260206023805388](assets/image-20260206023805388.png)
![image-20260206023948432](assets/image-20260206023948432.png)

### 4. History Record

Page: `/record`, with type filtering, detail view, and failed-action retry.

![image-20260207003609207](assets/image-20260207003609207.png)
![image-20260207003643433](assets/image-20260207003643433.png)
![image-20260207003627971](assets/image-20260207003627971.png)

---

## API Quick Reference

| Endpoint | Method | Description |
|------|------|------|
| `/voice` | POST | Voice scheduling (supports `session_id` for conflict rescheduling) |
| `/voice/ws` | WebSocket | Streaming voice channel (`stt_partial/stt_final` and chunked TTS events) |
| `/calendar/text` | POST | Text scheduling (supports `session_id` for conflict rescheduling) |
| `/autopilot/run` | POST | Analyze conversation and return action preview |
| `/autopilot/confirm` | POST | Execute confirmed actions |
| `/autopilot/adjust-time` | POST | Adjust conflicting meeting time and return updated preview |
| `/autopilot/retry/{run_id}` | POST | Retry failed actions |
| `/autopilot/runs` | GET | Run history list (pagination/filtering) |
| `/autopilot/runs/{run_id}` | GET | Single run details |
| `/autopilot/ingest` | POST | Re-index knowledge base |

---

## Testing and Quality Assurance

### Testing Strategy

- Covers critical paths: Schema, RAG, connectors, dispatcher, SQLite
- Uses dry_run to avoid heavy external API mocking
- Fast feedback: 12 tests in around 0.79 seconds

### Coverage Matrix

| Category | Count | Focus |
|------|------|------|
| Schema Validation | 3 | type checks, missing fields, error messages |
| Knowledge Base (RAG) | 2 | file existence, chunking logic |
| Connector Dry Run | 5 | Slack/Linear/Email/Calendar/None action |
| Dispatcher | 1 | routing and fault tolerance |
| SQLite CRUD | 1 | create/update/query run records |

Run tests:

```bash
cd Backend
python -m pytest tests/test_autopilot.py -v
```

Recommended CI: `GitHub Actions + pytest`.

Future expansion suggestions: E2E tests, performance benchmarks, concurrent load tests, frontend unit tests.

---

## Known Issues and Limitations

- First-time Google Calendar use requires manual login + MFA
- Playwright is sensitive to network quality
- Whisper `small` can be slow on CPU (consider `tiny` for speed)
- Current implementation supports same-day events only

---

## Playwright Calendar Automation Deep Dive

### Why Playwright

Compared with OAuth-heavy Calendar API integration, this approach is faster to operationalize:
- No OAuth client configuration
- Reuses real user login state + MFA
- Persistent sessions reduce repeated login overhead

### Implementation Highlights

1. **Persistent context**: `launch_persistent_context` + `Backend/chrome_profile/`.
2. **Login detection**: URL + core DOM signals.
3. **Selector strategy**: prioritize `role/aria-label`, then data attributes, then CSS fallback.
4. **Conflict detection**: parse `data-eventchip` time ranges and detect overlap.
5. **Form fill automation**: open modal via `c` shortcut and fill by bilingual label matching.
6. **Error handling**: layered handling for timeout, Playwright errors, and generic fallback.

### Production Notes

- Prefer semantic selectors to reduce breakage after UI updates
- Save failure screenshots and integrate alerting
- Consider context pooling and rate limiting for high concurrency
- Protect `chrome_profile` (contains sensitive session credentials)

---

## Code Entry Points

- Orchestration: `Backend/api/autopilot.py`
- Structured extraction: `Backend/chat/autopilot_extractor.py`
- Calendar slot extraction: `Backend/chat/calendar_extractor.py`
- Reply drafting: `Backend/chat/reply_drafter.py`
- Schema definitions: `Backend/business/autopilot_schema.json`
- Calendar automation: `Backend/tools/calendar_agent.py`
- RAG: `Backend/rag/ingest.py`, `Backend/rag/retrieve.py`
- Audit logs: `Backend/store/db.py`, `Backend/store/runs.py`
- MCP Server: `Backend/mcp/mcp_server.py`
- MCP test client: `Backend/mcp/test_mcp_client.py`
- Tests: `Backend/tests/test_autopilot.py`

---

## Links

- GitHub: https://github.com/Jayden3422/Voice-Autopilot
- Chinese Docs: [README_zh.md](README_zh.md)
