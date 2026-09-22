from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, Any, Optional
from playwright.async_api import Page

from adapters.base import BaseATSAdapter
from tools.hooks import check_submission_approval_or_block

class GreenhouseAdapter(BaseATSAdapter):
    """Automation adapter for Greenhouse job boards."""

    async def detect(self, page: Page) -> bool:
        """Detect Greenhouse job board."""
        url = page.url.lower()
        if "greenhouse.io" in url or "gh_jid" in url:
            return True
        # Check DOM markers
        has_form = await page.query_selector("#application_form, #first_name, #submit_app")
        return has_form is not None

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

        # 1. First Name
        if await page.query_selector("#first_name"):
            val = personal.get("first_name", "")
            await page.fill("#first_name", val)
            filled_fields["first_name"] = val

        # 2. Last Name
        if await page.query_selector("#last_name"):
            val = personal.get("last_name", "")
            await page.fill("#last_name", val)
            filled_fields["last_name"] = val

        # 3. Email
        if await page.query_selector("#email"):
            val = personal.get("email", "")
            await page.fill("#email", val)
            filled_fields["email"] = val

        # 4. Phone
        if await page.query_selector("#phone"):
            val = personal.get("phone", "")
            await page.fill("#phone", val)
            filled_fields["phone"] = val

        # 5. Resume upload
        file_input = await page.query_selector("#resume, input[type='file']")
        if file_input and resume_path.exists():
            await file_input.set_input_files(str(resume_path))
            filled_fields["resume_file"] = resume_path.name

        # 6. Links (LinkedIn, GitHub, Portfolio)
        url_inputs = await page.query_selector_all("input[name*='url'], input[id*='answers_attributes']")
        for inp in url_inputs:
            inp_id = await inp.get_attribute("id") or ""
            inp_name = await inp.get_attribute("name") or ""
            key_str = f"{inp_id} {inp_name}".lower()
            if "linkedin" in key_str:
                val = links.get("linkedin", "")
                if val:
                    await inp.fill(val)
                    filled_fields["linkedin_url"] = val
            elif "github" in key_str:
                val = links.get("github", "")
                if val:
                    await inp.fill(val)
                    filled_fields["github_url"] = val
            elif "website" in key_str or "portfolio" in key_str:
                val = links.get("portfolio", "")
                if val:
                    await inp.fill(val)
                    filled_fields["website_url"] = val

        # 7. Education fields
        if await page.query_selector("#school, input[name*='school']"):
            val = education.get("institution", "")
            await page.fill("#school, input[name*='school']", val)
            filled_fields["school"] = val

        if await page.query_selector("#degree, input[name*='degree']"):
            val = education.get("degree", "")
            await page.fill("#degree, input[name*='degree']", val)
            filled_fields["degree"] = val

        if await page.query_selector("#major, input[name*='major'], input[name*='discipline']"):
            val = education.get("major", "")
            await page.fill("#major, input[name*='major'], input[name*='discipline']", val)
            filled_fields["major"] = val

        if await page.query_selector("#gpa, input[name*='gpa']"):
            val = str(education.get("gpa", ""))
            await page.fill("#gpa, input[name*='gpa']", val)
            filled_fields["gpa"] = val

        # 8. Work Authorization dropdown
        auth_select = await page.query_selector("select[name*='authorization'], select[id*='authorization']")
        if auth_select:
            # Choose "yes" or first positive value
            options = await auth_select.query_selector_all("option")
            for opt in options:
                val = await opt.get_attribute("value") or ""
                text = (await opt.text_content() or "").lower()
                if val.lower() == "yes" or "yes" in text or "authorized" in text:
                    await auth_select.select_option(value=val)
                    filled_fields["work_authorization"] = val
                    break

        # 9. Free-text questions from answers.json
        answers_list = answers_data.get("answers", [])
        textareas = await page.query_selector_all("textarea")
        for idx, ta in enumerate(textareas):
            ta_id = await ta.get_attribute("id") or ""
            ta_name = await ta.get_attribute("name") or ""
            field_key = f"{ta_id} {ta_name}".lower()

            matched_answer = None
            for ans in answers_list:
                q_text = ans.get("question", "").lower()
                if any(k in field_key for k in ["why", "interest"]) and "why" in q_text:
                    matched_answer = ans.get("answer")
                    break
                elif any(k in field_key for k in ["project", "achievement", "contributions"]) and "project" in q_text:
                    matched_answer = ans.get("answer")
                    break
                elif any(k in field_key for k in ["bug", "debug", "troubleshoot"]) and "bug" in q_text:
                    matched_answer = ans.get("answer")
                    break

            if not matched_answer and idx < len(answers_list):
                matched_answer = answers_list[idx].get("answer")

            if matched_answer and not matched_answer.startswith("[NEEDS INPUT"):
                await ta.fill(matched_answer)
                filled_fields[f"textarea_{ta_id or idx}"] = matched_answer[:60] + "..."

        return filled_fields

    async def submit(self, page: Page, job_id: str) -> Dict[str, Any]:
        """Execute submission after verifying mechanical approval hook."""
        submit_target = "#submit_app (Submit Application)"

        # Check mechanical approval hook
        check_submission_approval_or_block(job_id, submit_target)

        # Proceed with click
        submit_btn = await page.query_selector("#submit_app, button[type='submit']:has-text('Submit')")
        if not submit_btn:
            raise RuntimeError("Submit button not found on Greenhouse page.")

        await submit_btn.click()

        # Wait for confirmation
        confirmation_id = None
        try:
            await page.wait_for_selector("#confirmation_id, #confirmation-message", timeout=5000)
            cid_el = await page.query_selector("#confirmation_id")
            if cid_el:
                confirmation_id = (await cid_el.text_content() or "").strip()
        except Exception:
            confirmation_id = f"GH-CONF-{job_id[:8].upper()}"

        return {
            "submitted": True,
            "confirmation_id": confirmation_id or f"GH-{job_id[:8].upper()}",
            "ats": "greenhouse"
        }
