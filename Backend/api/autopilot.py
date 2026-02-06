"""FastAPI routes for the Autopilot system."""

import base64
import json
import logging
import os
import uuid
import re
import html
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from chat.autopilot_extractor import extract_autopilot_json, get_openai_client
from chat.calendar_extractor import extract_calendar_event
from chat.reply_drafter import generate_reply_draft
from rag.retrieve import retrieve
from actions.dispatcher import dry_run_action, execute_action
from store.runs import create_run, update_run, get_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/autopilot", tags=["autopilot"])

EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


# --- Request / Response Models ---

class AutopilotRunRequest(BaseModel):
    mode: str  # "audio" or "text"
    audio_base64: Optional[str] = None
    text: Optional[str] = None
    locale: Optional[str] = "en"


class AutopilotConfirmRequest(BaseModel):
    run_id: str
    actions: list[dict]


class AutopilotAdjustRequest(BaseModel):
    mode: str  # "audio" or "text"
    text: Optional[str] = None
    audio_base64: Optional[str] = None
    locale: Optional[str] = "en"
    action: dict


# --- POST /autopilot/run ---

@router.post("/run")
async def autopilot_run(req: AutopilotRunRequest):
    run_id = str(uuid.uuid4())

    # Determine input
    if req.mode == "audio":
        if not req.audio_base64:
            raise HTTPException(status_code=400, detail="audio_base64 is required for audio mode")
        raw_input = req.audio_base64[:5000] + "..." if len(req.audio_base64 or "") > 5000 else req.audio_base64
    elif req.mode == "text":
        if not req.text:
            raise HTTPException(status_code=400, detail="text is required for text mode")
        raw_input = req.text
    else:
        raise HTTPException(status_code=400, detail="mode must be 'audio' or 'text'")

    create_run(run_id, req.mode, raw_input or "")

    try:
        # Step 1: Transcription
        transcript = ""
        if req.mode == "audio":
            transcript = await _transcribe_audio(req.audio_base64)
        else:
            transcript = req.text.strip()

        if not transcript:
            raise HTTPException(status_code=400, detail="Empty transcript")

        update_run(run_id, transcript=transcript, status="transcribed")

        # Step 2: Extraction via Tool Calling
        extracted = await extract_autopilot_json(transcript, run_id=run_id)
        entities = extracted.get("entities") or {}
        if not entities.get("email"):
            fallback_email = _extract_email_fallback(transcript)
            if fallback_email:
                entities["email"] = fallback_email
                extracted["entities"] = entities
        update_run(run_id, extracted_json=extracted, status="extracted")

        # Step 3: RAG retrieval
        client = get_openai_client()
        query = _build_rag_query(extracted)
        evidence = await retrieve(query, client)
        update_run(run_id, evidence_json=evidence)

        # Step 4: Reply draft
        draft = await generate_reply_draft(client, transcript, extracted, evidence, run_id=run_id)
        email_content = _build_email_content(draft, extracted)
        reply_payload = {
            "text": draft.get("reply_text", ""),
            "reply_text": draft.get("reply_text", ""),
            "citations": draft.get("citations", []),
            "html": email_content.get("body_html", ""),
            "subject": email_content.get("subject", ""),
            "to": email_content.get("to", ""),
            "from": email_content.get("from_display", ""),
            "body_text": email_content.get("body_text", ""),
        }
        update_run(run_id, reply_draft=reply_payload, status="drafted")

        # Step 5: Enrich & filter actions, then dry_run preview
        actions = extracted.get("next_best_actions", [])
        actions = await _enrich_actions(actions, extracted, draft, email_content, transcript)
        actions_preview = []
        for action in actions:
            preview = await dry_run_action(action)
            actions_preview.append({
                **action,
                "preview": preview.get("preview", ""),
            })
        update_run(run_id, actions_json=actions_preview, status="previewed")
        view_extracted = _merge_extracted_actions(extracted, actions)

        return {
            "run_id": run_id,
            "transcript": transcript,
            "extracted": view_extracted,
            "evidence": evidence,
            "reply_draft": reply_payload,
            "actions_preview": actions_preview,
        }

    except HTTPException:
        raise
    except ValueError as e:
        update_run(run_id, status="error", error=str(e)[:1000])
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("[%s] Autopilot run error", run_id)
        update_run(run_id, status="error", error=str(e)[:1000])
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)[:200]}")


# --- POST /autopilot/confirm ---

@router.post("/confirm")
async def autopilot_confirm(req: AutopilotConfirmRequest):
    run = get_run(req.run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {req.run_id} not found")

    results = []
    actions = list(req.actions or [])
    extracted_json = run.get("extracted_json", {}) if isinstance(run.get("extracted_json"), dict) else {}
    locale = extracted_json.get("conversation_language", "en") if isinstance(extracted_json, dict) else "en"
    transcript = run.get("transcript", "") if isinstance(run, dict) else ""
    summary = extracted_json.get("summary", "") if isinstance(extracted_json, dict) else ""

    actions = await _fill_calendar_payloads_from_transcript(actions, transcript, locale)

    # Precompute which actions are executable
    action_meta = []
    for idx, action in enumerate(actions):
        action_type = action.get("action_type", "none")
        skip = action.get("skip", False)
        requires_confirm = action.get("requires_confirmation", True)
        confirmed = action.get("confirmed", False)
        if skip or action_type == "none":
            action_meta.append({"idx": idx, "action": action, "exec": False, "reason": ""})
        elif requires_confirm and not confirmed:
            action_meta.append({"idx": idx, "action": action, "exec": False, "reason": "Not confirmed"})
        else:
            action_meta.append({"idx": idx, "action": action, "exec": True, "reason": ""})

    calendar_indices = [m["idx"] for m in action_meta if m["exec"] and (m["action"].get("action_type") == "create_meeting")]
    results_by_index: dict[int, dict] = {}

    calendar_success = True
    confirmation_text = ""
    confirmation_html = ""
    from utils.timezone import now as now_toronto
    current_dt = now_toronto()

    if calendar_indices:
        for idx in calendar_indices:
            action = actions[idx]
            try:
                payload = action.get("payload") or {}
                payload = _finalize_calendar_payload(payload, summary, locale, current_dt)
                action["payload"] = payload
                result = await execute_action(action, lang=locale)
                results_by_index[idx] = result
                if result.get("status") != "success":
                    calendar_success = False
                    break
                if not confirmation_text:
                    confirm = _build_calendar_confirmation(action.get("payload", {}), locale)
                    confirmation_text = confirm.get("text", "")
                    confirmation_html = confirm.get("html", "")
            except Exception as e:
                logger.exception("Action execution error for %s", action.get("action_type", "create_meeting"))
                results_by_index[idx] = {"action_type": "create_meeting", "status": "failed", "result": {"error": str(e)[:300]}}
                calendar_success = False
                break

        if not calendar_success:
            # Skip all remaining executable actions until calendar succeeds
            for m in action_meta:
                idx = m["idx"]
                if idx in results_by_index:
                    continue
                if not m["exec"]:
                    reason = m["reason"]
                    results_by_index[idx] = {
                        "action_type": actions[idx].get("action_type", "none"),
                        "status": "skipped",
                        "result": {"reason": reason} if reason else {},
                    }
                else:
                    results_by_index[idx] = {
                        "action_type": actions[idx].get("action_type", "none"),
                        "status": "skipped",
                        "result": {"reason": "Calendar not created yet"},
                    }
    # If no calendar action or calendar succeeded, execute remaining actions in order
    if not calendar_indices or calendar_success:
        for m in action_meta:
            idx = m["idx"]
            action = m["action"]
            action_type = action.get("action_type", "none")

            if idx in results_by_index:
                continue
            if not m["exec"]:
                reason = m["reason"]
                results_by_index[idx] = {
                    "action_type": action_type,
                    "status": "skipped",
                    "result": {"reason": reason} if reason else {},
                }
                continue

            # Append final calendar confirmation to Slack/Email after calendar success
            if confirmation_text and action_type == "send_slack_summary":
                payload = {**(action.get("payload") or {})}
                _append_confirmation_to_slack_payload(payload, confirmation_text)
                action = {**action, "payload": payload}
            if confirmation_text and action_type == "send_email_followup":
                payload = {**(action.get("payload") or {})}
                _append_confirmation_to_email_payload(payload, confirmation_text, confirmation_html)
                action = {**action, "payload": payload}

            try:
                result = await execute_action(action, lang=locale)
                results_by_index[idx] = result
            except Exception as e:
                logger.exception("Action execution error for %s", action_type)
                results_by_index[idx] = {"action_type": action_type, "status": "failed", "result": {"error": str(e)[:300]}}

    # Preserve original order of results
    for i in range(len(actions)):
        results.append(results_by_index.get(i, {"action_type": actions[i].get("action_type", "none"), "status": "skipped", "result": {}}))

    update_run(req.run_id, actions_json=results, status="executed")

    return {"run_id": req.run_id, "results": results}


# --- POST /autopilot/adjust-time ---

@router.post("/adjust-time")
async def autopilot_adjust_time(req: AutopilotAdjustRequest):
    action = req.action or {}
    if action.get("action_type") != "create_meeting":
        raise HTTPException(status_code=400, detail="Only create_meeting can be adjusted")

    # Determine input
    if req.mode == "audio":
        if not req.audio_base64:
            raise HTTPException(status_code=400, detail="audio_base64 is required for audio mode")
        user_text = await _transcribe_audio(req.audio_base64)
    elif req.mode == "text":
        if not req.text:
            raise HTTPException(status_code=400, detail="text is required for text mode")
        user_text = req.text.strip()
    else:
        raise HTTPException(status_code=400, detail="mode must be 'audio' or 'text'")
    if not user_text:
        raise HTTPException(status_code=400, detail="Empty transcript")

    locale = _normalize_lang(req.locale)
    payload = action.get("payload") or {}
    context_event = {
        "date": payload.get("date", ""),
        "start_time": payload.get("start_time", ""),
        "end_time": payload.get("end_time", ""),
        "title": payload.get("title", "Meeting" if locale == "en" else "日程安排"),
        "attendees": payload.get("attendees", []),
    }

    extracted = await extract_calendar_event(
        user_text,
        lang=locale,
        context_event=context_event,
    )

    payload.update({
        "date": extracted.get("date", payload.get("date")),
        "start_time": extracted.get("start_time", payload.get("start_time")),
        "end_time": extracted.get("end_time", payload.get("end_time")),
        "title": extracted.get("title", payload.get("title")),
    })
    if "attendees" in extracted:
        payload["attendees"] = extracted.get("attendees", payload.get("attendees", []))

    updated_action = {**action, "payload": payload}
    preview = await dry_run_action(updated_action)
    updated_action["preview"] = preview.get("preview", "")

    return {
        "action": updated_action,
        "user_text": user_text,
    }


# --- POST /autopilot/ingest ---

@router.post("/ingest")
async def autopilot_ingest():
    """Re-ingest the knowledge base into the FAISS index."""
    from rag.ingest import ingest_knowledge_base
    client = get_openai_client()
    result = await ingest_knowledge_base(client)
    return {"status": "ok", **result}


# --- Helpers ---

async def _transcribe_audio(audio_b64: str) -> str:
    """Decode base64 audio and run Whisper STT."""
    import tempfile
    from tools.speech import transcribe_audio

    audio_bytes = base64.b64decode(audio_b64)
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        text = transcribe_audio(tmp_path, lang="en")
        return text.strip()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


async def _enrich_actions(
    actions: list[dict],
    extracted: dict,
    draft: dict,
    email_content: dict | None = None,
    transcript: str = "",
) -> list[dict]:
    """
    Post-process actions: fill in missing payload fields from extracted data,
    resolve relative dates/times, and drop actions that have no viable data.
    """
    from utils.timezone import now as now_toronto

    current_dt = now_toronto()

    summary = extracted.get("summary", "")
    intent = extracted.get("intent", "")
    urgency = extracted.get("urgency", "")
    entities = extracted.get("entities") or {}
    email = entities.get("email")
    email_content = email_content or {}
    contact = entities.get("contact_name", "")
    company = entities.get("company", "")
    lang = extracted.get("conversation_language", "en")

    # Build a rich Slack message from extracted data
    slack_msg_parts = []
    if intent:
        slack_msg_parts.append(f"Intent: {intent.replace('_', ' ')}")
    if urgency:
        slack_msg_parts.append(f"Urgency: {urgency}")
    if company:
        slack_msg_parts.append(f"Company: {company}")
    if contact:
        slack_msg_parts.append(f"Contact: {contact}")
    if summary:
        slack_msg_parts.append(f"Summary: {summary}")
    slack_msg = "\n".join(slack_msg_parts) if slack_msg_parts else summary
    if not slack_msg:
        slack_msg = "Autopilot summary unavailable." if lang == "en" else "Autopilot 摘要暂无。"

    action_list = list(actions or [])
    if not any(a.get("action_type") == "send_slack_summary" for a in action_list):
        action_list.append({
            "action_type": "send_slack_summary",
            "requires_confirmation": True,
            "confidence": 0.9,
            "payload": {},
        })
    if email and not any(a.get("action_type") == "send_email_followup" for a in action_list):
        action_list.append({
            "action_type": "send_email_followup",
            "requires_confirmation": True,
            "confidence": 0.9,
            "payload": {},
        })

    enriched = []
    for action in action_list:
        a = {**action}
        payload = {**(a.get("payload") or {})}
        atype = a.get("action_type", "none")

        if atype == "create_meeting":
            payload = _prepare_calendar_payload_for_preview(payload, summary, lang, current_dt)

        elif atype == "send_slack_summary":
            if not payload.get("message"):
                payload["message"] = slack_msg
            if not payload.get("channel"):
                payload["channel"] = "#general"

        elif atype == "send_email_followup":
            # Only keep if we have a recipient email
            if not payload.get("to"):
                if email:
                    payload["to"] = email
                else:
                    # Skip — no email address available
                    continue
            if not payload.get("subject"):
                subject = email_content.get("subject", "")
                if not subject:
                    subject_prefix = "Re: " if lang == "en" else "回复: "
                    subject = f"{subject_prefix}{summary[:60]}" if summary else ("Follow-up" if lang == "en" else "跟进")
                payload["subject"] = subject
            body_text = email_content.get("body_text") or payload.get("body_text") or payload.get("body") or ""
            if not body_text:
                reply_text = draft.get("reply_text", "") if draft else ""
                body_text = reply_text if reply_text else summary
            payload["body_text"] = body_text
            payload["body"] = body_text
            body_html = email_content.get("body_html") or payload.get("body_html") or ""
            if body_html:
                payload["body_html"] = body_html
            from_name = email_content.get("from_name")
            if from_name:
                payload["from_name"] = from_name

        elif atype == "create_ticket":
            if not payload.get("title"):
                payload["title"] = summary[:120] if summary else "New ticket"
            if not payload.get("description"):
                payload["description"] = summary
            if not payload.get("priority"):
                priority_map = {"high": "high", "medium": "medium", "low": "low"}
                payload["priority"] = priority_map.get(urgency, "medium")

        a["payload"] = payload
        enriched.append(a)

    return enriched


def _resolve_date(value: str, ref_dt, lang: str = "en") -> str:
    """Ensure a date value is in YYYY-MM-DD format. GPT resolves via prompt-injected datetime."""
    from datetime import datetime
    if not value:
        return ref_dt.strftime("%Y-%m-%d")
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
            "RELATIVE_BASE": ref_dt.replace(tzinfo=None),
        })
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return value


def _resolve_time(value: str) -> str:
    """Ensure a time value is in HH:MM 24-hour format."""
    from datetime import datetime
    if not value:
        return ""
    try:
        datetime.strptime(value, "%H:%M")
        return value
    except ValueError:
        pass
    try:
        t = datetime.strptime(value, "%H:%M:%S")
        return t.strftime("%H:%M")
    except ValueError:
        pass
    for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            t = datetime.strptime(value.strip(), fmt)
            return t.strftime("%H:%M")
        except ValueError:
            continue
    return value


def _prepare_calendar_payload_for_preview(payload: dict, summary: str, lang: str, current_dt) -> dict:
    """Ensure calendar payload has editable fields without forcing defaults or LLM calls."""
    from datetime import datetime, timedelta
    if not payload.get("title"):
        payload["title"] = summary[:80] if summary else ("Meeting" if lang == "en" else "æ—¥ç¨‹å®‰æŽ’")
    if "date" not in payload:
        payload["date"] = ""
    if payload.get("date"):
        payload["date"] = _resolve_date(payload["date"], current_dt, lang)
    if "start_time" not in payload:
        payload["start_time"] = ""
    if payload.get("start_time"):
        payload["start_time"] = _resolve_time(payload["start_time"])
    if "end_time" not in payload:
        if payload.get("start_time"):
            try:
                st = datetime.strptime(payload["start_time"], "%H:%M")
                payload["end_time"] = (st + timedelta(hours=1)).strftime("%H:%M")
            except Exception:
                payload["end_time"] = ""
        else:
            payload["end_time"] = ""
    else:
        if payload.get("end_time"):
            payload["end_time"] = _resolve_time(payload["end_time"])
    if "attendees" not in payload:
        payload["attendees"] = []
    return payload


async def _fill_calendar_payloads_from_transcript(actions: list[dict], transcript: str, lang: str) -> list[dict]:
    if not transcript:
        return actions
    needs = any(
        a.get("action_type") == "create_meeting"
        and (not (a.get("payload") or {}).get("date") or not (a.get("payload") or {}).get("start_time"))
        for a in actions
    )
    if not needs:
        return actions
    try:
        extracted = await extract_calendar_event(transcript, lang=lang)
    except Exception:
        return actions

    for action in actions:
        if action.get("action_type") != "create_meeting":
            continue
        payload = {**(action.get("payload") or {})}
        if not payload.get("date") and extracted.get("date"):
            payload["date"] = extracted.get("date")
        if not payload.get("start_time") and extracted.get("start_time"):
            payload["start_time"] = extracted.get("start_time")
        if not payload.get("end_time") and extracted.get("end_time"):
            payload["end_time"] = extracted.get("end_time")
        if not payload.get("title") and extracted.get("title"):
            payload["title"] = extracted.get("title")
        if "attendees" not in payload and "attendees" in extracted:
            payload["attendees"] = extracted.get("attendees", [])
        action["payload"] = payload
    return actions


def _finalize_calendar_payload(payload: dict, summary: str, lang: str, current_dt) -> dict:
    """Fill missing fields with defaults right before execution."""
    from datetime import datetime, timedelta
    if not payload.get("title"):
        payload["title"] = summary[:80] if summary else ("Meeting" if lang == "en" else "æ—¥ç¨‹å®‰æŽ’")
    if payload.get("date"):
        payload["date"] = _resolve_date(payload["date"], current_dt, lang)
    else:
        payload["date"] = (current_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    if payload.get("start_time"):
        payload["start_time"] = _resolve_time(payload["start_time"])
    else:
        payload["start_time"] = "10:00"
    if payload.get("end_time"):
        payload["end_time"] = _resolve_time(payload["end_time"])
    else:
        try:
            st = datetime.strptime(payload["start_time"], "%H:%M")
            payload["end_time"] = (st + timedelta(hours=1)).strftime("%H:%M")
        except Exception:
            payload["end_time"] = "11:00"
    if "attendees" not in payload:
        payload["attendees"] = []
    return payload


def _build_rag_query(extracted: dict) -> str:
    """Build a search query from extracted fields."""
    parts = []
    intent = extracted.get("intent", "")
    if intent:
        parts.append(intent.replace("_", " "))

    products = extracted.get("product_interest", [])
    if products:
        parts.append(" ".join(products))

    summary = extracted.get("summary", "")
    if summary:
        parts.append(summary)

    return " ".join(parts) if parts else "general inquiry"


def _build_calendar_confirmation(payload: dict, lang: str = "en") -> dict:
    title = payload.get("title", "Meeting" if lang == "en" else "日程安排")
    date = payload.get("date", "")
    start = payload.get("start_time", "")
    end = payload.get("end_time", "")
    if lang.startswith("zh"):
        text = f"日历已创建：{title}，{date} {start}-{end}。"
    else:
        text = f"Calendar confirmed: {title} on {date} {start}-{end}."
    html_text = f"<p><strong>{html.escape(text)}</strong></p>"
    return {"text": text, "html": html_text}


def _append_confirmation_to_slack_payload(payload: dict, confirmation_text: str) -> None:
    msg = (payload.get("message") or "").strip()
    if msg:
        payload["message"] = f"{msg}\n\n{confirmation_text}"
    else:
        payload["message"] = confirmation_text


def _append_confirmation_to_email_payload(payload: dict, confirmation_text: str, confirmation_html: str) -> None:
    body_text = (payload.get("body_text") or payload.get("body") or "").strip()
    if body_text:
        payload["body_text"] = f"{body_text}\n\n{confirmation_text}"
    else:
        payload["body_text"] = confirmation_text
    payload["body"] = payload.get("body_text", "")

    body_html = (payload.get("body_html") or "").strip()
    if body_html:
        payload["body_html"] = f"{body_html}\n{confirmation_html}"
    else:
        payload["body_html"] = confirmation_html


def _merge_extracted_actions(extracted: dict, enriched_actions: list[dict]) -> dict:
    """Merge enriched action payloads back into extracted output for display."""
    try:
        merged = json.loads(json.dumps(extracted))
    except Exception:
        merged = dict(extracted or {})
    extracted_actions = list(merged.get("next_best_actions", []) or [])
    pool = list(enriched_actions or [])
    for ex in extracted_actions:
        atype = ex.get("action_type")
        match_idx = next((i for i, a in enumerate(pool) if a.get("action_type") == atype), None)
        if match_idx is None:
            continue
        matched = pool.pop(match_idx)
        payload = matched.get("payload") or ex.get("payload") or {}
        ex["payload"] = payload
    merged["next_best_actions"] = extracted_actions
    return merged


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return "en"
    return "en" if lang.lower().startswith("en") else "zh"


def _extract_email_fallback(text: str) -> str | None:
    if not text:
        return None
    match = EMAIL_REGEX.search(text)
    return match.group(0) if match else None


def _starts_with_greeting(text: str, lang: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return False
    if lang == "zh":
        return s.startswith(("你好", "您好", "嗨", "哈喽"))
    return s.startswith(("hi", "hello", "dear"))


def _text_to_html(text: str) -> str:
    if not text:
        return ""
    paragraphs = []
    for block in text.strip().split("\n\n"):
        lines = [html.escape(line) for line in block.split("\n")]
        paragraphs.append("<p>" + "<br/>".join(lines) + "</p>")
    return "\n".join(paragraphs)


def _build_email_content(draft: dict, extracted: dict) -> dict:
    lang = _normalize_lang(extracted.get("conversation_language", "en"))
    entities = extracted.get("entities") or {}
    to_addr = entities.get("email") or ""
    contact = entities.get("contact_name") or ""

    reply_text = (draft or {}).get("reply_text", "").strip()
    subject_prefix = "Re: " if lang == "en" else "回复: "
    summary = extracted.get("summary", "")
    subject = f"{subject_prefix}{summary[:60]}" if summary else ("Follow-up" if lang == "en" else "跟进")

    greeting = ""
    if not _starts_with_greeting(reply_text, lang):
        if lang == "zh":
            greeting = f"您好{contact}：" if contact else "您好："
        else:
            greeting = f"Hi {contact}," if contact else "Hello,"

    signature = "Voice Autopilot (noreply)" if lang == "en" else "Voice Autopilot（noreply）"
    footer = (
        "This is an automated message from noreply. Please do not reply."
        if lang == "en"
        else "此邮件由 noreply 自动发送，请勿直接回复。"
    )

    body_parts = []
    if greeting:
        body_parts.append(greeting)
    if reply_text:
        body_parts.append(reply_text)
    body_parts.append(signature)
    body_parts.append(footer)
    body_text = "\n\n".join(body_parts).strip()

    body_html = "\n".join(
        filter(
            None,
            [
                f"<p>{html.escape(greeting)}</p>" if greeting else "",
                _text_to_html(reply_text),
                f"<p><strong>{html.escape(signature)}</strong></p>",
                f"<p class=\"email-footer\">{html.escape(footer)}</p>",
            ],
        )
    )

    from_addr = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "noreply@example.com"
    from_name = os.getenv("SMTP_FROM_NAME", "Voice Autopilot")
    from_name_display = from_name
    if "noreply" not in (from_name_display or "").lower():
        from_name_display = f"{from_name_display} (noreply)"
    from_display = f"{from_name_display} <{from_addr}>"

    return {
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "to": to_addr,
        "from_display": from_display,
        "from_name": from_name_display,
    }
