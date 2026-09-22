from __future__ import annotations
import json
import abc
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from playwright.async_api import Page

from tools.hooks import check_submission_approval_or_block

class BaseATSAdapter(abc.ABC):
    """Abstract base class for ATS automation adapters."""

    @abc.abstractmethod
    async def detect(self, page: Page) -> bool:
        """Detect if the active page matches this ATS system."""
        pass

    @abc.abstractmethod
    async def fill_form(
        self,
        page: Page,
        profile_data: Dict[str, Any],
        answers_data: Dict[str, Any],
        resume_path: Path
    ) -> Dict[str, Any]:
        """Fill application fields up to, but NOT including, the final submit button."""
        pass

    async def snapshot(self, page: Page, dest_dir: Path, filled_fields: Dict[str, Any]) -> Tuple[Path, Path]:
        """Capture full-page screenshot and dump filled form fields."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = dest_dir / "screenshot.png"
        form_dump_path = dest_dir / "form_dump.json"

        # Capture screenshot
        await page.screenshot(path=str(screenshot_path), full_page=True)

        # Write form dump
        dump_data = {
            "url": page.url,
            "title": await page.title(),
            "fields": filled_fields
        }
        with open(form_dump_path, "w", encoding="utf-8") as f:
            json.dump(dump_data, f, indent=2)

        return screenshot_path, form_dump_path

    @abc.abstractmethod
    async def submit(self, page: Page, job_id: str) -> Dict[str, Any]:
        """Execute submission after verifying human approval token. Returns confirmation metadata."""
        pass
