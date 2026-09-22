from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, Any, Optional
from playwright.async_api import Page

from adapters.base import BaseATSAdapter
from tools.hooks import check_submission_approval_or_block

class AshbyAdapter(BaseATSAdapter):
    """Automation adapter for Ashby job postings."""

    async def detect(self, page: Page) -> bool:
        """Detect Ashby job board."""
        url = page.url.lower()
        if "ashbyhq.com" in url:
            return True
        has_ashby = await page.query_selector("input[name='name'], [data-ashby-input], .ashby-application-form")
        return has_ashby is not None

    async def fill_form(
        self,
        page: Page,
        profile_data: Dict[str, Any],
        answers_data: Dict[str, Any],
        resume_path: Path
    ) -> Dict[str, Any]:
        """Fill all standard form fields up to the final submit."""
        filled_fields = {}
        personal = profile_data.get("personal", {})
        education = profile_data.get("education", {})
        links = profile_data.get("links", {})

        # Full Name or First/Last
        if await page.query_selector("input[name='name'], input[id*='name']"):
            first = personal.get("first_name", "")
            last = personal.get("last_name", "")
            full_name = f"{first} {last}".strip() or personal.get("full_name", "")
            await page.fill("input[name='name'], input[id*='name']", full_name)
            filled_fields["name"] = full_name

        # Email
        if await page.query_selector("input[type='email'], input[name='email']"):
            val = personal.get("email", "")
            await page.fill("input[type='email'], input[name='email']", val)
            filled_fields["email"] = val

        # Phone
        if await page.query_selector("input[type='tel'], input[name='phone']"):
            val = personal.get("phone", "")
            await page.fill("input[type='tel'], input[name='phone']", val)
            filled_fields["phone"] = val

        # Resume file upload
        file_input = await page.query_selector("input[type='file']")
        if file_input and resume_path.exists():
            await file_input.set_input_files(str(resume_path))
            filled_fields["resume_file"] = resume_path.name

        # Links
        if await page.query_selector("input[name*='linkedin'], input[id*='linkedin']"):
            val = links.get("linkedin", "")
            if val:
                await page.fill("input[name*='linkedin'], input[id*='linkedin']", val)
                filled_fields["linkedin"] = val

        if await page.query_selector("input[name*='github'], input[id*='github']"):
            val = links.get("github", "")
            if val:
                await page.fill("input[name*='github'], input[id*='github']", val)
                filled_fields["github"] = val

        # Textareas
        textareas = await page.query_selector_all("textarea")
        answers_list = answers_data.get("answers", [])
        for idx, ta in enumerate(textareas):
            if idx < len(answers_list):
                ans = answers_list[idx].get("answer", "")
                if ans and not ans.startswith("[NEEDS INPUT"):
                    await ta.fill(ans)
                    filled_fields[f"question_{idx}"] = ans[:60] + "..."

        return filled_fields

    async def submit(self, page: Page, job_id: str) -> Dict[str, Any]:
        """Execute submission after verifying mechanical approval hook."""
        submit_target = "button[type='submit'] (Submit Application)"

        # Check mechanical approval hook
        check_submission_approval_or_block(job_id, submit_target)

        submit_btn = await page.query_selector("button[type='submit']:has-text('Submit'), button:has-text('Submit Application')")
        if not submit_btn:
            raise RuntimeError("Submit button not found on Ashby page.")

        await submit_btn.click()

        confirmation_id = f"ASH-{job_id[:8].upper()}"
        return {
            "submitted": True,
            "confirmation_id": confirmation_id,
            "ats": "ashby"
        }
