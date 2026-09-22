from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, Optional
from playwright.async_api import Page

from adapters.base import BaseATSAdapter
from tools.hooks import check_submission_approval_or_block

class WorkdayAdapter(BaseATSAdapter):
    """Semi-automated stub adapter for Workday job applications.
    Halts at authentication/login screen, dumps pre-filled answers to sidecar JSON,
    and instructs the human user for manual pasting and completion.
    """

    async def detect(self, page: Page) -> bool:
        """Detect Workday application portal."""
        url = page.url.lower()
        if "myworkdayjobs.com" in url or "workday" in url:
            return True
        has_wd = await page.query_selector("[data-automation-id], #workdayApplication")
        return has_wd is not None

    async def fill_form(
        self,
        page: Page,
        profile_data: Dict[str, Any],
        answers_data: Dict[str, Any],
        resume_path: Path
    ) -> Dict[str, Any]:
        """Dump pre-filled answers to sidecar JSON and halt with clear instructions."""
        personal = profile_data.get("personal", {})
        education = profile_data.get("education", {})
        links = profile_data.get("links", {})

        payload = {
            "workday_notice": "Workday authentication requires manual user login. Use these prepared values to paste.",
            "personal_info": {
                "first_name": personal.get("first_name", ""),
                "last_name": personal.get("last_name", ""),
                "email": personal.get("email", ""),
                "phone": personal.get("phone", ""),
                "address": personal.get("location", ""),
                "linkedin": links.get("linkedin", ""),
                "github": links.get("github", ""),
                "website": links.get("portfolio", "")
            },
            "education": {
                "institution": education.get("institution", ""),
                "degree": education.get("degree", ""),
                "major": education.get("major", ""),
                "gpa": education.get("gpa", ""),
                "graduation": education.get("expected_graduation", "")
            },
            "resume_path": str(resume_path),
            "free_text_answers": answers_data.get("answers", [])
        }

        # Save sidecar JSON into artifacts directory
        sidecar_path = resume_path.parent / "workday_sidecar.json"
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return {
            "workday_mode": "manual_sidecar_paste",
            "sidecar_json": str(sidecar_path),
            "instructions": (
                "Workday login detected. Halting browser automation. "
                "Answers dumped to workday_sidecar.json. Please paste answers and submit manually after review."
            ),
            "first_name": personal.get("first_name", ""),
            "last_name": personal.get("last_name", ""),
            "email": personal.get("email", "")
        }

    async def submit(self, page: Page, job_id: str) -> Dict[str, Any]:
        """Execute submission or confirm manual completion with mechanical approval hook."""
        submit_target = "Workday Manual Submission Verification"
        check_submission_approval_or_block(job_id, submit_target)

        confirmation_id = f"WD-{job_id[:8].upper()}"
        return {
            "submitted": True,
            "confirmation_id": confirmation_id,
            "ats": "workday",
            "mode": "manual_paste_confirmed"
        }
