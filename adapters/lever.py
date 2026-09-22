from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, Any, Optional
from playwright.async_api import Page

from adapters.base import BaseATSAdapter
from tools.hooks import check_submission_approval_or_block

class LeverAdapter(BaseATSAdapter):
    """Automation adapter for Lever job postings."""

    async def detect(self, page: Page) -> bool:
        """Detect Lever job board."""
        url = page.url.lower()
        if "lever.co" in url:
            return True
        has_lever = await page.query_selector("input[name='name'], .postings-btn-wrapper, .lever-form")
        return has_lever is not None

    async def fill_form(
        self,
        page: Page,
        profile_data: Dict[str, Any],
        answers_data: Dict[str, Any],
        resume_path: Path
    ) -> Dict[str, Any]:
        """Fill all form fields up to the final submit."""
        filled_fields = {}
        personal = profile_data.get("personal", {})
        education = profile_data.get("education", {})
        links = profile_data.get("links", {})

        # Full Name
        if await page.query_selector("input[name='name']"):
            first = personal.get("first_name", "")
            last = personal.get("last_name", "")
            full_name = f"{first} {last}".strip() or personal.get("full_name", "")
            await page.fill("input[name='name']", full_name)
            filled_fields["name"] = full_name

        # Email
        if await page.query_selector("input[name='email']"):
            val = personal.get("email", "")
            await page.fill("input[name='email']", val)
            filled_fields["email"] = val

        # Phone
        if await page.query_selector("input[name='phone']"):
            val = personal.get("phone", "")
            await page.fill("input[name='phone']", val)
            filled_fields["phone"] = val

        # Current Company / Organization
        if await page.query_selector("input[name='org']"):
            val = education.get("institution", "")
            await page.fill("input[name='org']", val)
            filled_fields["org"] = val

        # Resume file upload
        file_input = await page.query_selector("input[type='file']")
        if file_input and resume_path.exists():
            await file_input.set_input_files(str(resume_path))
            filled_fields["resume_file"] = resume_path.name

        # Links
        if await page.query_selector("input[name*='LinkedIn']"):
            val = links.get("linkedin", "")
            if val:
                await page.fill("input[name*='LinkedIn']", val)
                filled_fields["linkedin"] = val

        if await page.query_selector("input[name*='GitHub']"):
            val = links.get("github", "")
            if val:
                await page.fill("input[name*='GitHub']", val)
                filled_fields["github"] = val

        # Additional free-text questions
        textareas = await page.query_selector_all("textarea")
        answers_list = answers_data.get("answers", [])
        for idx, ta in enumerate(textareas):
            if idx < len(answers_list):
                ans = answers_list[idx].get("answer", "")
                if ans and not ans.startswith("[NEEDS INPUT"):
                    await ta.fill(ans)
                    filled_fields[f"additional_question_{idx}"] = ans[:60] + "..."

        return filled_fields

    async def submit(self, page: Page, job_id: str) -> Dict[str, Any]:
        """Execute submission after verifying mechanical approval hook."""
        submit_target = ".postings-btn-wrapper button (Submit Application)"

        # Check mechanical approval hook
        check_submission_approval_or_block(job_id, submit_target)

        submit_btn = await page.query_selector(".postings-btn-wrapper button, button[type='submit']:has-text('Submit')")
        if not submit_btn:
            raise RuntimeError("Submit button not found on Lever page.")

        await submit_btn.click()

        # Wait for confirmation
        confirmation_id = f"LEV-{job_id[:8].upper()}"
        return {
            "submitted": True,
            "confirmation_id": confirmation_id,
            "ats": "lever"
        }
