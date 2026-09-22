from __future__ import annotations
import os
import re
import json
import uuid
import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import yaml
from playwright.async_api import async_playwright, Browser, Page

from db.session import SessionLocal, generate_id
from db.models import Job, Application, Artifact, Event
from adapters.greenhouse import GreenhouseAdapter
from adapters.lever import LeverAdapter
from adapters.ashby import AshbyAdapter
from adapters.workday import WorkdayAdapter
from adapters.base import BaseATSAdapter
from agents.tailor import TailoringAgent, compute_sha256
from tools.hooks import check_submission_approval_or_block, has_human_approval, SubmissionBlockedError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

class DailySubmissionCapExceeded(Exception):
    """Raised when the daily submission cap is exceeded."""
    pass

class AlreadySubmittedError(Exception):
    """Raised when an application with this dedup key is already submitted."""
    pass

class ApplicationAgent:
    """Manages semi-automated pre-filling, review packet generation, and approval-locked submission."""

    def __init__(self):
        prof_p = PROJECT_ROOT / "profile" / "profile.yaml"
        if not prof_p.exists():
            prof_p = PROJECT_ROOT / "profile" / "profile.example.yaml"
        self.profile = load_yaml(prof_p)
        self.filters_cfg = load_yaml(PROJECT_ROOT / "config" / "filters.yaml")
        self.daily_cap = self.filters_cfg.get("limits", {}).get("daily_submission_cap", 100)
        self.adapters: List[BaseATSAdapter] = [
            GreenhouseAdapter(),
            LeverAdapter(),
            AshbyAdapter(),
            WorkdayAdapter()
        ]

    def _get_submissions_today(self, db) -> int:
        """Count applications submitted today."""
        today_start = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        return (
            db.query(Application)
            .filter(Application.status == "submitted", Application.submitted_at >= today_start)
            .count()
        )

    def _build_review_packet(
        self,
        job: Job,
        app: Application,
        filled_fields: Dict[str, Any],
        answers_data: Dict[str, Any],
        flags_text: str,
        screenshot_path: Path
    ) -> str:
        """Construct the Section 5 Human-in-the-Loop review packet markdown."""
        rank_score = f"{job.score.total:.3f}" if job.score else "N/A"
        rank_rationale = job.score.rationale if job.score else "No ranking rationale recorded."

        packet = f"""# 📋 Application Review Packet: {job.company}

**Role:** {job.title}  
**Location:** {job.location_norm} ({job.location_raw})  
**ATS:** {job.ats_type.capitalize()}  
**Application URL:** [{job.apply_url}]({job.apply_url})  
**Rank Score:** `{rank_score}`  

---

## 🎯 Ranking Rationale
{rank_rationale}

---

## 📝 Pre-filled Fields (Field-by-Field Dump)
| Field | Filled Value |
| :--- | :--- |
"""
        for k, v in filled_fields.items():
            packet += f"| `{k}` | {v} |\n"

        packet += f"""
---

## 💬 Free-Text Answers & Facts-Bank Citations
"""
        for ans in answers_data.get("answers", []):
            citations_str = ", ".join(f"`{c}`" for c in ans.get("citations", [])) or "None"
            needs_input_tag = " ⚠️ **[NEEDS INPUT]**" if ans.get("needs_input") else ""
            packet += f"""### Q: {ans['question']}{needs_input_tag}
> {ans['answer']}

*Facts Bank Citations:* {citations_str}

"""

        packet += f"""---

## 🚩 Attention Flags
{flags_text}

---

## 📸 Form Screenshot
Attached: `{screenshot_path.name}` (`{screenshot_path}`)

---

## ⚡ Human Review Commands
To execute an action, enter one of the following commands:
- **`ia approve {job.id}`** — Grants approval and executes form submission.
- **`ia edit {job.id} <field> <new_value>`** — Updates a field value before approval.
- **`ia reject {job.id} --reason "<reason>"`** — Skips this job posting.
"""
        return packet

    async def prefill_application(self, job_id: str, custom_page_url: Optional[str] = None) -> Dict[str, Any]:
        """Pre-fill application form, take screenshot, generate review packet, and halt before submit."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        with SessionLocal() as db:
            job = db.query(Job).filter_by(id=job_id).first()
            if not job:
                raise ValueError(f"Job ID '{job_id}' not found in database.")

            app = job.application
            if app and app.status in ["submitted", "in_process"]:
                raise AlreadySubmittedError(f"Application for {job.company} is already in state '{app.status}'.")

        # 1. Ensure tailoring materials exist
        tailor_agent = TailoringAgent()
        dest_dir = PROJECT_ROOT / "artifacts" / job_id
        if not (dest_dir / "answers.json").exists():
            tailor_agent.tailor_job(job_id)

        with open(dest_dir / "answers.json", "r", encoding="utf-8") as f:
            answers_bundle = json.load(f)

        with open(dest_dir / "flags.md", "r", encoding="utf-8") as f:
            flags_text = f.read()

        resume_path = dest_dir / "resume.pdf"

        # 2. Launch headless browser
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            target_url = custom_page_url or job.apply_url
            if target_url.startswith("file://") or target_url.startswith("http"):
                await page.goto(target_url)
            else:
                # Local file fallback
                fixture_file = PROJECT_ROOT / "tests" / "fixtures" / "greenhouse_form.html"
                await page.goto(f"file://{fixture_file.resolve()}")

            # 3. Detect matching adapter
            matched_adapter = None
            for adapter in self.adapters:
                if await adapter.detect(page):
                    matched_adapter = adapter
                    break

            if not matched_adapter:
                # Default to greenhouse adapter for form filling
                matched_adapter = self.adapters[0]

            # 4. Fill form fields (Halt before submit!)
            filled_fields = await matched_adapter.fill_form(
                page=page,
                profile_data=self.profile,
                answers_data=answers_bundle,
                resume_path=resume_path
            )

            # 5. Take full-page screenshot & form dump
            screenshot_path, form_dump_path = await matched_adapter.snapshot(page, dest_dir, filled_fields)
            await browser.close()

        # 6. Generate Review Packet
        with SessionLocal() as db:
            job = db.query(Job).filter_by(id=job_id).first()
            app = job.application

            packet_text = self._build_review_packet(
                job=job,
                app=app,
                filled_fields=filled_fields,
                answers_data=answers_bundle,
                flags_text=flags_text,
                screenshot_path=screenshot_path
            )
            review_packet_path = dest_dir / "review_packet.md"
            with open(review_packet_path, "w", encoding="utf-8") as f:
                f.write(packet_text)

            # Persist artifacts in DB
            artifacts_to_save = [
                ("screenshot", str(screenshot_path)),
                ("form_dump", str(form_dump_path)),
                ("review_packet", str(review_packet_path))
            ]
            for kind, path_str in artifacts_to_save:
                sha = compute_sha256(Path(path_str))
                art_id = generate_id("art", f"{app.id}-{kind}")
                existing_art = db.query(Artifact).filter_by(id=art_id).first()
                if existing_art:
                    existing_art.path = path_str
                    existing_art.sha256 = sha
                else:
                    db.add(Artifact(id=art_id, application_id=app.id, kind=kind, path=path_str, sha256=sha))

            # Update Application state to ready_for_review
            app.status = "ready_for_review"
            app.last_event_at = now

            evt = Event(
                id=f"evt_{uuid.uuid4().hex[:16]}",
                application_id=app.id,
                ts=now,
                actor="agent",
                type="ready_for_review",
                payload_json=json.dumps({"filled_field_count": len(filled_fields), "packet_path": str(review_packet_path)})
            )
            db.add(evt)
            db.commit()

        return {
            "job_id": job_id,
            "status": "ready_for_review",
            "filled_fields": filled_fields,
            "screenshot": str(screenshot_path),
            "form_dump": str(form_dump_path),
            "review_packet": str(review_packet_path)
        }

    async def approve_and_submit(
        self,
        job_id: str,
        actor: str = "human",
        custom_page_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Submit the application upon explicit human approval, strictly checked by the PreToolUse hook."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        with SessionLocal() as db:
            job = db.query(Job).filter_by(id=job_id).first()
            if not job:
                raise ValueError(f"Job ID '{job_id}' not found.")

            app = job.application
            if not app:
                raise ValueError(f"Application for '{job_id}' not found.")

            # Hard Rule 1: Never submit twice for the same dedup key
            existing_submitted = (
                db.query(Application)
                .join(Job)
                .filter(Job.dedup_key == job.dedup_key, Application.status == "submitted")
                .first()
            )
            if existing_submitted:
                raise AlreadySubmittedError(f"Duplicate submission prevented: Job with dedup_key {job.dedup_key} already submitted.")

            # Hard Rule 2: Daily submission cap check
            submissions_today = self._get_submissions_today(db)
            if submissions_today >= self.daily_cap:
                raise DailySubmissionCapExceeded(f"Daily submission cap reached: {submissions_today}/{self.daily_cap}.")

            approver = self.profile.get("personal", {}).get("full_name", actor) if actor == "human" else actor
            # Register human approval event in DB
            approval_event = Event(
                id=f"evt_{uuid.uuid4().hex[:16]}",
                application_id=app.id,
                ts=now,
                actor=actor,
                type="approval_granted",
                payload_json=json.dumps({"command": f"ia approve {job_id}", "approved_by": approver})
            )
            db.add(approval_event)
            db.commit()

        # Execute submission with Playwright
        dest_dir = PROJECT_ROOT / "artifacts" / job_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            target_url = custom_page_url or job.apply_url
            if target_url.startswith("file://") or target_url.startswith("http"):
                await page.goto(target_url)
            else:
                fixture_file = PROJECT_ROOT / "tests" / "fixtures" / "greenhouse_form.html"
                await page.goto(f"file://{fixture_file.resolve()}")

            # Detect matching adapter
            matched_adapter = None
            for adapter in self.adapters:
                if await adapter.detect(page):
                    matched_adapter = adapter
                    break
            if not matched_adapter:
                matched_adapter = self.adapters[0]

            # Re-fill form if needed
            with open(dest_dir / "answers.json", "r", encoding="utf-8") as f:
                answers_bundle = json.load(f)
            resume_path = dest_dir / "resume.pdf"

            await matched_adapter.fill_form(page, self.profile, answers_bundle, resume_path)

            # Submit through adapter (will verify check_submission_approval_or_block!)
            submission_result = await matched_adapter.submit(page, job_id)

            # Capture confirmation screenshot
            conf_screenshot = dest_dir / "confirmation_screenshot.png"
            await page.screenshot(path=str(conf_screenshot), full_page=True)
            await browser.close()

        # Update database with confirmation ID
        with SessionLocal() as db:
            app = db.query(Application).filter_by(job_id=job_id).first()
            confirmation_id = submission_result.get("confirmation_id", f"CONF-{job_id[:8].upper()}")
            app.status = "submitted"
            app.confirmation_id = confirmation_id
            app.submitted_at = now
            app.last_event_at = now

            conf_sha = compute_sha256(conf_screenshot)
            art_id = generate_id("art", f"{app.id}-conf_screenshot")
            db.add(Artifact(id=art_id, application_id=app.id, kind="screenshot", path=str(conf_screenshot), sha256=conf_sha))

            submit_event = Event(
                id=f"evt_{uuid.uuid4().hex[:16]}",
                application_id=app.id,
                ts=now,
                actor="agent",
                type="submitted",
                payload_json=json.dumps(submission_result)
            )
            db.add(submit_event)
            db.commit()

        return {
            "job_id": job_id,
            "status": "submitted",
            "confirmation_id": confirmation_id,
            "submitted_at": now.isoformat()
        }

    def edit_field(self, job_id: str, field_name: str, new_value: str) -> Dict[str, Any]:
        """Edit a pre-filled field value before approval."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        dest_dir = PROJECT_ROOT / "artifacts" / job_id
        dump_path = dest_dir / "form_dump.json"

        if dump_path.exists():
            with open(dump_path, "r", encoding="utf-8") as f:
                dump_data = json.load(f)
            dump_data.setdefault("fields", {})[field_name] = new_value
            with open(dump_path, "w", encoding="utf-8") as f:
                json.dump(dump_data, f, indent=2)

        with SessionLocal() as db:
            app = db.query(Application).filter_by(job_id=job_id).first()
            if app:
                evt = Event(
                    id=f"evt_{uuid.uuid4().hex[:16]}",
                    application_id=app.id,
                    ts=now,
                    actor="human",
                    type="edited",
                    payload_json=json.dumps({"field": field_name, "value": new_value})
                )
                db.add(evt)
                db.commit()

        return {"job_id": job_id, "field": field_name, "new_value": new_value}

    def reject_application(self, job_id: str, reason: str = "User rejected") -> Dict[str, Any]:
        """Skip this job posting."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        with SessionLocal() as db:
            app = db.query(Application).filter_by(job_id=job_id).first()
            if app:
                app.status = "skipped"
                app.notes = reason
                app.last_event_at = now
                evt = Event(
                    id=f"evt_{uuid.uuid4().hex[:16]}",
                    application_id=app.id,
                    ts=now,
                    actor="human",
                    type="skipped",
                    payload_json=json.dumps({"reason": reason})
                )
                db.add(evt)
                db.commit()

        return {"job_id": job_id, "status": "skipped", "reason": reason}
