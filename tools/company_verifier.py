from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

VALID_ATS_TYPES = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "custom"}

class CompanyVerifier:
    """Verifies target companies configuration (config/companies.yaml) before batch operations."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (PROJECT_ROOT / "config" / "companies.yaml")

    def verify_companies(self) -> Dict[str, Any]:
        """Validate ATS tokens, URLs, and custom documentation for all 51 target companies."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Companies configuration file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        companies = data.get("companies", [])
        total = len(companies)
        valid_count = 0
        warnings = []
        errors = []

        categories = {}

        for c in companies:
            name = c.get("name")
            if not name:
                errors.append("Found company entry without 'name'.")
                continue

            cat = c.get("category", "other")
            categories[cat] = categories.get(cat, 0) + 1

            ats = str(c.get("ats", "")).lower().strip()
            if not ats:
                errors.append(f"{name}: Missing 'ats' specification.")
                continue

            if ats not in VALID_ATS_TYPES:
                errors.append(f"{name}: Unknown ATS type '{ats}'. Must be one of {VALID_ATS_TYPES}.")
                continue

            # If custom ATS, require documented reason
            if ats == "custom":
                reason = c.get("reason", "").strip()
                if not reason:
                    errors.append(f"{name}: Custom ATS requires documented 'reason' explaining portal/login behavior.")
                    continue
                valid_count += 1
            else:
                # Standard ATS: verify token or career URL
                token = c.get("token")
                career_url = c.get("career_url") or c.get("url")

                if not token and not career_url:
                    errors.append(f"{name} ({ats}): Requires at least a valid 'token' or 'career_url'.")
                    continue

                if token and not re.match(r"^[a-zA-Z0-9_\-\.\:]+$", str(token)):
                    errors.append(f"{name}: Invalid token format '{token}'.")
                    continue

                valid_count += 1

        is_healthy = (len(errors) == 0) and (total >= 50)
        return {
            "is_healthy": is_healthy,
            "total_companies": total,
            "valid_companies": valid_count,
            "categories": categories,
            "warnings": warnings,
            "errors": errors
        }
