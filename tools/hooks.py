from __future__ import annotations
import re
import json
import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from db.session import SessionLocal, generate_id
from db.models import Job, Application, Event

SUBMISSION_REGEX = re.compile(r"(?i)\b(submit|apply\s+now|send\s+application|submit\s+application|complete\s+application)\b")

class SubmissionBlockedError(Exception):
    """Raised when a browser action attempts to submit an application without human approval."""
    pass

def has_human_approval(job_id: str) -> bool:
    """Verify in the SQLite database whether an explicit human approval event exists for this job."""
    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=job_id).first()
        if not app:
            return False

        # Look for human approval event
        approval_event = (
            db.query(Event)
            .filter(
                Event.application_id == app.id,
                Event.actor == "human",
                Event.type == "approval_granted"
            )
            .order_by(Event.ts.desc())
            .first()
        )
        return approval_event is not None

def check_submission_approval_or_block(job_id: str, target_text_or_selector: str) -> bool:
    """Enforce the mechanical semi-automatic guarantee.
    If target text or selector matches submission keywords, block unless approved in DB.
    """
    is_submission_action = bool(SUBMISSION_REGEX.search(target_text_or_selector or ""))
    if not is_submission_action:
        return True

    # Target is a submission action
    if has_human_approval(job_id):
        return True

    # Log blocked attempt to audit trail
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=job_id).first()
        if app:
            evt = Event(
                id=generate_id("evt", f"{app.id}-blocked-{now.isoformat()}"),
                application_id=app.id,
                ts=now,
                actor="agent",
                type="hook_blocked",
                payload_json=json.dumps({
                    "reason": "PreToolUse hook blocked submission attempt",
                    "target": target_text_or_selector,
                    "human_approval_present": False
                })
            )
            db.add(evt)
            db.commit()

    raise SubmissionBlockedError(
        f"MECHANICAL GUARANTEE BLOCKED: Target element '{target_text_or_selector}' matches submission pattern, "
        f"but no explicit human approval event (actor=human, type=approval_granted) exists in database for job_id='{job_id}'."
    )

async def pre_tool_use_approval_hook(input_data: Dict[str, Any], tool_use_id: str, context: Any) -> Dict[str, Any]:
    """Claude Agent SDK PreToolUse hook function for Playwright click actions."""
    tool_input = input_data.get("tool_input", {})
    selector = tool_input.get("selector", "")
    element_text = tool_input.get("text", "")
    job_id = tool_input.get("job_id", "")

    target_str = f"{selector} {element_text}".strip()
    if SUBMISSION_REGEX.search(target_str):
        if not (job_id and has_human_approval(job_id)):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Blocked submission click '{target_str}': No human approval found in DB for {job_id}."
                    )
                }
            }

    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}
