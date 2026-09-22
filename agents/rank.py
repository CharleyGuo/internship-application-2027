from __future__ import annotations
import re
import json
import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set, Optional
import yaml
from sqlalchemy.orm import Session

from db.session import SessionLocal
from db.models import Job, Score, Application, Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

class FilterAndRankAgent:
    """Evaluates job postings against candidate hard filters and computes multi-factor ranking scores."""

    def __init__(self):
        self.filters_cfg = load_yaml(PROJECT_ROOT / "config" / "filters.yaml")
        facts_p = PROJECT_ROOT / "profile" / "facts.yaml"
        if not facts_p.exists():
            facts_p = PROJECT_ROOT / "profile" / "facts.example.yaml"
        self.facts_cfg = load_yaml(facts_p)

        prof_p = PROJECT_ROOT / "profile" / "profile.yaml"
        if not prof_p.exists():
            prof_p = PROJECT_ROOT / "profile" / "profile.example.yaml"
        self.profile_cfg = load_yaml(prof_p)

        self.companies_cfg = load_yaml(PROJECT_ROOT / "config" / "companies.yaml")

        self.candidate_skills = self._extract_candidate_skills()
        grad_str = str(self.profile_cfg.get("education", {}).get("graduation_expected", "2028"))
        grad_match = re.search(r"202\d", grad_str)
        self.candidate_grad_year = int(grad_match.group(0)) if grad_match else 2028

    def _extract_candidate_skills(self) -> Set[str]:
        """Extract atomic skillset tokens from facts.yaml."""
        skills = set()
        # Languages
        for lang in self.facts_cfg.get("skills", {}).get("languages", []):
            skills.add(lang.get("name", "").lower())
            for word in re.findall(r"\w+", lang.get("context", "").lower()):
                if len(word) > 2:
                    skills.add(word)

        # Tools
        for tool in self.facts_cfg.get("skills", {}).get("systems_and_tools", []):
            skills.add(tool.get("name", "").lower())
            for word in re.findall(r"\w+", tool.get("context", "").lower()):
                if len(word) > 2:
                    skills.add(word)

        # Projects technologies
        for proj in self.facts_cfg.get("projects", []):
            for tech in proj.get("technologies", []):
                skills.add(tech.lower())
            for b in proj.get("bullets", []):
                for kw in b.get("keywords", []):
                    skills.add(kw.lower())

        # Experience keywords
        for exp in self.facts_cfg.get("experience", []):
            for b in exp.get("bullets", []):
                for kw in b.get("keywords", []):
                    skills.add(kw.lower())

        # Core additions
        skills.update({"c++", "python", "systems", "distributed", "concurrency", "linux", "git", "docker", "algorithms", "data structures", "operating systems", "raft", "grpc", "fastapi", "sql"})
        return skills

    def evaluate_hard_filters(self, job: Job) -> Tuple[bool, Optional[str], bool]:
        """Evaluate hard filters.
        Returns: (eligible, eligibility_note, is_adjacent)
        """
        title = job.title or ""
        desc = job.description_md or ""
        full_text = f"{title}\n{desc}".lower()

        # 1. Security Clearance Check (Excluded by default)
        clearance_patterns = [
            r"\b(active\s+security\s+clearance|secret\s+clearance|top\s+secret|ts/sci|polygraph|dod\s+clearance)\b",
            r"\b(must\s+(?:possess|hold|have)\s+an?\s+(?:active\s+)?clearance)\b"
        ]
        for pat in clearance_patterns:
            match = re.search(pat, full_text, re.IGNORECASE)
            if match:
                return False, f"Requires security clearance: '{match.group(0)}'", False

        # 2. Excluded Roles Check (PM, Data Analyst only, IT support, Hardware only)
        excluded_role_patterns = [
            (r"\b(product\s+manager|technical\s+product\s+manager|program\s+manager)\b", "Product / Program Management role"),
            (r"\b(data\s+analyst|business\s+analyst|financial\s+analyst)\b", "Data / Financial Analyst role (not engineering)"),
            (r"\b(it\s+support|helpdesk|desktop\s+support|it\s+specialist|service\s+desk)\b", "IT / Helpdesk support role"),
            (r"\b(hardware\s+engineer|asic\s+design|pcb\s+layout|fpga\s+design\s+engineer|mechanical\s+engineer)\b", "Hardware / ASIC engineering role"),
        ]
        for pat, reason in excluded_role_patterns:
            if re.search(pat, title, re.IGNORECASE) and not re.search(r"\b(software|swe|developer)\b", title, re.IGNORECASE):
                return False, f"Excluded role category: {reason}", False

        # 3. Graduation Year / Term Mismatch Check
        grad_mismatch_patterns = [
            (r"\b(must\s+graduate\s+(?:by|in)\s+(?:december\s+)?202[56])\b", f"Requires graduation in 2025/2026 (Candidate graduates {self.candidate_grad_year})"),
            (r"\b(class\s+of\s+202[567]\s+only|graduating\s+seniors?\s+only)\b", "Restricted to graduating seniors / earlier class years"),
            (r"\b(must\s+be\s+graduating\s+in\s+fall\s+202[567])\b", f"Requires fall graduation before {self.candidate_grad_year}")
        ]
        for pat, reason in grad_mismatch_patterns:
            match = re.search(pat, full_text, re.IGNORECASE)
            if match:
                return False, f"Graduation requirement mismatch: '{match.group(0)}' ({reason})", False

        # 4. Adjacent Roles (Quant Research, Trader, AI Research)
        # Marked as adjacent and retained, not hard-excluded
        adjacent_patterns = [
            r"\b(quant(?:itative)?\s+research(?:er)?|qr\s+intern|trader\s+intern|trading\s+intern)\b",
            r"\b(machine\s+learning\s+research(?:er)?|ai\s+researcher)\b"
        ]
        is_adjacent = False
        for pat in adjacent_patterns:
            if re.search(pat, title, re.IGNORECASE):
                is_adjacent = True
                break

        # 5. Must match SWE / engineering / developer intern keywords
        intern_keyword = bool(re.search(r"\b(intern|internship|co-op|coop|summer\s+analyst)\b", title, re.IGNORECASE))
        eng_keyword = bool(re.search(
            r"\b(software|swe|engineer(?:ing)?|developer|development|dev|systems|quant(?:itative)?|infrastructure|infra|platform|backend|fullstack|distributed|compiler|security|data)\b",
            title,
            re.IGNORECASE
        ))

        if not intern_keyword:
            return False, f"Title does not indicate internship / co-op position: '{title}'", is_adjacent

        if not (eng_keyword or is_adjacent):
            return False, f"Title does not indicate engineering or dev track: '{title}'", is_adjacent

        return True, None, is_adjacent

    def calculate_skills_overlap(self, text: str) -> Tuple[float, List[str]]:
        """Compute Jaccard similarity and extract top matched terms."""
        text_words = set(re.findall(r"[a-zA-Z0-9_\+\#]+", text.lower()))
        matched_terms = []

        for skill in self.candidate_skills:
            if " " in skill:
                if skill in text.lower():
                    matched_terms.append(skill)
            else:
                if skill in text_words:
                    matched_terms.append(skill)

        # Remove duplicates while preserving order
        unique_matches = list(dict.fromkeys(matched_terms))

        # Soft preferences boost: Python, ML-infra, security research
        bonus = 0.0
        if "python" in unique_matches:
            bonus += 0.05
        if any(term in text.lower() for term in ["ml-infra", "inference", "gpu", "distributed training"]):
            bonus += 0.05
        if any(term in text.lower() for term in ["security", "systems", "low latency"]):
            bonus += 0.05

        union_size = len(self.candidate_skills.union(text_words))
        raw_jaccard = len(unique_matches) / max(union_size, 1)
        # Scaled overlap score between 0.1 and 1.0 for practical ranking
        overlap_score = min(1.0, (len(unique_matches) / 12.0) + bonus)

        top_10 = unique_matches[:10]
        return overlap_score, top_10

    def compute_score(self, job: Job) -> Score:
        """Compute multi-factor score and generate rationale."""
        eligible, eligibility_note, is_adjacent = self.evaluate_hard_filters(job)

        # 1. Industry Fit (0.35 weight)
        # Quant: 1.0, Big Tech/AI: 0.95, Fintech/Banks: 0.90, Other: 0.0
        ind = (job.industry or "other").lower()
        if ind == "quant_trading":
            industry_fit = 1.0
        elif ind == "big_tech_ai":
            industry_fit = 0.95
        elif ind == "fintech_banks":
            industry_fit = 0.90
        else:
            industry_fit = 0.0

        # 2. Location Fit (0.25 weight)
        # NYC 1st choice: 1.0, SF Bay Area 2nd choice: 0.85, Chicago/Seattle/Remote: 0.70, Others: 0.0
        loc = (job.location_norm or "Other").lower()
        if "new york" in loc or "nyc" in loc:
            location_fit = 1.0
        elif "sf" in loc or "bay area" in loc:
            location_fit = 0.85
        elif any(k in loc for k in ["chicago", "seattle", "remote"]):
            location_fit = 0.70
        else:
            location_fit = 0.0

        # 3. Skills Overlap (0.20 weight)
        text_to_eval = f"{job.title}\n{job.description_md or ''}\n{job.location_raw or ''}"
        skills_overlap, top_matched_skills = self.calculate_skills_overlap(text_to_eval)

        # 4. Deadline Urgency (0.10 weight)
        if job.closes_at:
            days_left = (job.closes_at - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).days
            if days_left <= 7:
                deadline_urgency = 1.0
            elif days_left <= 14:
                deadline_urgency = 0.8
            elif days_left <= 30:
                deadline_urgency = 0.5
            else:
                deadline_urgency = 0.3
        else:
            deadline_urgency = 0.3

        # 5. Prestige Prior (0.10 weight)
        company_lower = job.company.lower()
        top_tier = ["jane street", "optiver", "hudson river trading", "citadel", "jump trading", "two sigma", "anthropic", "openai", "google", "meta", "stripe", "apple", "nvidia"]
        high_tier = ["databricks", "snowflake", "palantir", "scale ai", "robinhood", "ramp", "brex", "affirm", "bloomberg", "goldman sachs", "morgan stanley", "point72", "imc", "drw", "five rings", "tower research"]

        if any(t in company_lower for t in top_tier):
            prestige_prior = 1.0
        elif any(h in company_lower for h in high_tier):
            prestige_prior = 0.85
        else:
            prestige_prior = 0.70

        # If adjacent role, apply slight calibration factor to prioritize pure SWE/quant-dev
        adj_factor = 0.90 if is_adjacent else 1.0

        # Calculate Total Score
        if not eligible:
            total_score = 0.0
        else:
            raw_total = (
                0.35 * industry_fit +
                0.25 * location_fit +
                0.20 * skills_overlap +
                0.10 * deadline_urgency +
                0.10 * prestige_prior
            ) * adj_factor
            total_score = round(min(1.0, max(0.0, raw_total)), 4)

        # Build Explainable Rationale
        if not eligible:
            rationale = f"Ineligible: {eligibility_note}"
        else:
            skills_str = ", ".join(top_matched_skills) if top_matched_skills else "none directly listed"
            adjacent_str = " (Tagged as adjacent role)" if is_adjacent else ""
            rationale = (
                f"Rank score {total_score:.3f}{adjacent_str}. "
                f"Industry fit: {industry_fit:.2f} ({job.industry}); "
                f"Location fit: {location_fit:.2f} ({job.location_norm}); "
                f"Skills overlap: {skills_overlap:.2f} (Matched: {skills_str}); "
                f"Prestige: {prestige_prior:.2f}; Urgency: {deadline_urgency:.2f}."
            )

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return Score(
            job_id=job.id,
            industry_fit=industry_fit,
            location_fit=location_fit,
            skills_overlap=skills_overlap,
            deadline_urgency=deadline_urgency,
            prestige_prior=prestige_prior,
            total=total_score,
            eligible=eligible,
            eligibility_note=eligibility_note,
            rationale=rationale,
            scored_at=now
        )

    def rank_all_jobs(self, force: bool = False) -> List[Score]:
        """Rank all unranked or updated jobs in database."""
        scored_records = []
        with SessionLocal() as db:
            jobs = db.query(Job).filter(Job.is_open == True).all()
            for job in jobs:
                if not force and job.score:
                    continue

                score = self.compute_score(job)
                existing_score = db.query(Score).filter_by(job_id=job.id).first()
                if existing_score:
                    existing_score.industry_fit = score.industry_fit
                    existing_score.location_fit = score.location_fit
                    existing_score.skills_overlap = score.skills_overlap
                    existing_score.deadline_urgency = score.deadline_urgency
                    existing_score.prestige_prior = score.prestige_prior
                    existing_score.total = score.total
                    existing_score.eligible = score.eligible
                    existing_score.eligibility_note = score.eligibility_note
                    existing_score.rationale = score.rationale
                    existing_score.scored_at = score.scored_at
                    scored_records.append(existing_score)
                else:
                    db.add(score)
                    scored_records.append(score)

                # Transition Application status to 'qualified' if eligible and status is 'discovered'
                if job.application and job.application.status == "discovered":
                    if score.eligible and score.total >= 0.40:
                        job.application.status = "qualified"
                        evt = Event(
                            id=f"evt_{job.id[:12]}_qual",
                            application_id=job.application.id,
                            ts=score.scored_at,
                            actor="agent",
                            type="qualified",
                            payload_json=json.dumps({"total_score": score.total, "rationale": score.rationale})
                        )
                        db.add(evt)

            db.commit()

        return scored_records

    def rank_job(self, job_id: str) -> Dict[str, Any]:
        """Rank a single job by id and update score and application status."""
        with SessionLocal() as db:
            job = db.query(Job).filter_by(id=job_id).first()
            if not job:
                raise ValueError(f"Job '{job_id}' not found.")
            score = self.compute_score(job)
            existing_score = db.query(Score).filter_by(job_id=job.id).first()
            if existing_score:
                existing_score.industry_fit = score.industry_fit
                existing_score.location_fit = score.location_fit
                existing_score.skills_overlap = score.skills_overlap
                existing_score.deadline_urgency = score.deadline_urgency
                existing_score.prestige_prior = score.prestige_prior
                existing_score.total = score.total
                existing_score.eligible = score.eligible
                existing_score.eligibility_note = score.eligibility_note
                existing_score.rationale = score.rationale
                existing_score.scored_at = score.scored_at
            else:
                db.add(score)

            if job.application and job.application.status == "discovered":
                if score.eligible and score.total >= 0.40:
                    job.application.status = "qualified"
                else:
                    job.application.status = "skipped"

            db.commit()
            return {
                "job_id": job.id,
                "eligible": score.eligible,
                "total": score.total,
                "rationale": score.rationale
            }

