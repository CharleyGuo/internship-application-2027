from __future__ import annotations
import re
import json
import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set, Optional
import yaml
from sqlalchemy.orm import Session

from db.session import SessionLocal, get_user_session_factory
from db.models import Job, Score, Application, Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

class FilterAndRankAgent:
    """Evaluates job postings against candidate hard filters and computes multi-factor ranking scores.
    Supports general Software Engineering roles (Full-Time, New Grad, and Internships).
    """

    def __init__(self, user: Optional[str] = None):
        self.user = user
        self.session_factory = get_user_session_factory(user)

        # User-specific config resolution
        user_dir = PROJECT_ROOT / "users" / user if user and user != "default" else None
        
        user_filters = user_dir / "filters.yaml" if user_dir else None
        if user_filters and user_filters.exists():
            self.filters_cfg = load_yaml(user_filters)
        else:
            self.filters_cfg = load_yaml(PROJECT_ROOT / "config" / "filters.yaml")

        user_facts = user_dir / "facts.yaml" if user_dir else None
        if user_facts and user_facts.exists():
            self.facts_cfg = load_yaml(user_facts)
        else:
            facts_p = PROJECT_ROOT / "profile" / "facts.yaml"
            if not facts_p.exists():
                facts_p = PROJECT_ROOT / "profile" / "facts.example.yaml"
            self.facts_cfg = load_yaml(facts_p)

        user_prof = user_dir / "profile.yaml" if user_dir else None
        if user_prof and user_prof.exists():
            self.profile_cfg = load_yaml(user_prof)
        else:
            prof_p = PROJECT_ROOT / "profile" / "profile.yaml"
            if not prof_p.exists():
                prof_p = PROJECT_ROOT / "profile" / "profile.example.yaml"
            self.profile_cfg = load_yaml(prof_p)

        self.companies_cfg = load_yaml(PROJECT_ROOT / "config" / "companies.yaml")

        self.candidate_skills = self._extract_candidate_skills()
        grad_str = str(self.profile_cfg.get("education", {}).get("graduation_expected", "2028"))
        grad_match = re.search(r"202\d", grad_str)
        self.candidate_grad_year = int(grad_match.group(0)) if grad_match else 2028

        self.job_search_mode = self.filters_cfg.get("job_search_mode", "general")
        self.target_job_types = self.filters_cfg.get("target_job_types", ["full_time", "new_grad", "internship"])
        self.hard_filters = self.filters_cfg.get("hard_filters", {})

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

        # 2. Excluded Non-Engineering Roles Check (PM, Data Analyst only, IT support, Hardware only)
        excluded_role_patterns = [
            (r"\b(product\s+manager|technical\s+product\s+manager|program\s+manager)\b", "Product / Program Management role"),
            (r"\b(data\s+analyst|business\s+analyst|financial\s+analyst)\b", "Data / Financial Analyst role (not engineering)"),
            (r"\b(it\s+support|helpdesk|desktop\s+support|it\s+specialist|service\s+desk)\b", "IT / Helpdesk support role"),
            (r"\b(hardware\s+engineer|asic\s+design|pcb\s+layout|fpga\s+design\s+engineer|mechanical\s+engineer)\b", "Hardware / ASIC engineering role"),
        ]
        for pat, reason in excluded_role_patterns:
            if re.search(pat, title, re.IGNORECASE) and not re.search(r"\b(software|swe|developer)\b", title, re.IGNORECASE):
                return False, f"Excluded role category: {reason}", False

        # Exclude high seniority roles regardless of SWE keywords
        if re.search(r"\b(director|vice\s+president|vp\b|principal\s+engineer|distinguished\s+engineer|head\s+of\s+engineering)\b", title, re.IGNORECASE):
            return False, "Excluded high seniority role (Director/VP/Principal)", False

        # Check specialized exclusions (Data Engineer, ML/AI Engineer, Test Developer/QA, Analyst, PhD/MS only)
        spec_ex = self.hard_filters.get("role", {}).get("specialized_exclusions", {})
        if spec_ex.get("data_engineer", True) and (
            re.search(r'\bdata\s+engineer(ing)?\b', title, re.IGNORECASE) or 
            re.search(r'\bdata\s+platform\s+engineer\b', title, re.IGNORECASE) or
            re.search(r'\bdata\s+(infrastructure|platform)\s+engineer\b', title, re.IGNORECASE) or
            re.search(r'\bdata\s+science\b', title, re.IGNORECASE) or
            re.search(r'\bdata\s+scientist\b', title, re.IGNORECASE)
        ):
            return False, "Excluded specialized track: Data Engineer", False

        if spec_ex.get("ml_engineer", True) and (
            re.search(r'\b(machine\s+learning|ml)\s+engineer(ing)?\b', title, re.IGNORECASE) or
            re.search(r'\b(machine\s+learning|ml)\s+infrastructure\s+engineer\b', title, re.IGNORECASE)
        ):
            return False, "Excluded specialized track: Machine Learning Engineer", False

        if spec_ex.get("ai_engineer", True) and (
            re.search(r'\b(ai|artificial\s+intelligence)\s+(software\s+|solution\s+|feature\s+)?(engineer(ing)?|developer)\b', title, re.IGNORECASE) or
            re.search(r'\bai\s+engineer\b', title, re.IGNORECASE)
        ):
            return False, "Excluded specialized track: AI Engineer", False

        if spec_ex.get("test_developer", True) and (
            re.search(r'\b(test\s+developer|sdet|qa)\b', title, re.IGNORECASE) or 
            re.search(r'\bsoftware\s+(engineer\s+in\s+)?test\b', title, re.IGNORECASE) or
            re.search(r'\bquality\s+(engineer|assurance)\b', title, re.IGNORECASE)
        ):
            return False, "Excluded specialized track: Test Developer / SDET / QA", False

        if spec_ex.get("phd_ms_only", True) and (
            re.search(r'\b(phd|ph\.d)\b', title, re.IGNORECASE) or 
            re.search(r'\bms\s*(only|\/phd)\b', title, re.IGNORECASE) or
            re.search(r'\(phd\)', title, re.IGNORECASE) or 
            re.search(r'\(ms\)', title, re.IGNORECASE)
        ):
            if not re.search(r'\b(bs|undergrad|bachelor)\b', title, re.IGNORECASE):
                return False, "Excluded requirement: PhD or MS only", False

        # 3. Detect Job Type: Internship vs. Full-Time / New Grad
        is_intern = bool(re.search(r"\b(intern|internship|co-op|coop|summer\s+analyst)\b", title, re.IGNORECASE))
        
        # Check target job types configuration
        if self.target_job_types == ["internship"] and not is_intern:
            return False, f"Configured for internships only; role is not an internship: '{title}'", False
        if "internship" not in self.target_job_types and is_intern:
            return False, f"Configured for full-time only; skipping internship role: '{title}'", False

        # 4. Graduation Year / Term Mismatch Check (Only applicable to internships)
        if is_intern:
            grad_mismatch_patterns = [
                (r"\b(must\s+graduate\s+(?:by|in)\s+(?:december\s+)?202[56])\b", f"Requires graduation in 2025/2026 (Candidate graduates {self.candidate_grad_year})"),
                (r"\b(class\s+of\s+202[567]\s+only|graduating\s+seniors?\s+only)\b", "Restricted to graduating seniors / earlier class years"),
                (r"\b(must\s+be\s+graduating\s+in\s+fall\s+202[567])\b", f"Requires fall graduation before {self.candidate_grad_year}")
            ]
            for pat, reason in grad_mismatch_patterns:
                match = re.search(pat, full_text, re.IGNORECASE)
                if match:
                    return False, f"Graduation requirement mismatch: '{match.group(0)}' ({reason})", False

        # 5. Adjacent Roles (Quant Research, Trader, AI Research)
        adjacent_patterns = [
            r"\b(quant(?:itative)?\s+research(?:er)?|qr\s+intern|trader\s+intern|trading\s+intern)\b",
            r"\b(machine\s+learning\s+research(?:er)?|ai\s+researcher)\b"
        ]
        is_adjacent = False
        for pat in adjacent_patterns:
            if re.search(pat, title, re.IGNORECASE):
                is_adjacent = True
                break

        # 6. Must match SWE / engineering / developer keywords
        eng_keyword = bool(re.search(
            r"\b(software|swe|sde|engineer(?:ing)?|developer|development|dev|systems|platform|backend|frontend|full\s*stack|infrastructure|infra|distributed|compiler|cloud|devops|sre|site\s+reliability|quant(?:itative)?|algo(?:rithmic)?|security|data)\b",
            title,
            re.IGNORECASE
        ))

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
        ind = (job.industry or "other").lower()
        if ind == "quant_trading":
            industry_fit = 1.0
        elif ind == "big_tech_ai":
            industry_fit = 0.95
        elif ind == "fintech_banks":
            industry_fit = 0.90
        else:
            industry_fit = 0.70  # Higher floor for general tech companies

        # 2. Location Fit (0.25 weight)
        loc = (job.location_norm or "Other").lower()
        if "new york" in loc or "nyc" in loc:
            location_fit = 1.0
        elif "sf" in loc or "bay area" in loc:
            location_fit = 0.85
        elif any(k in loc for k in ["chicago", "seattle", "remote"]):
            location_fit = 0.70
        else:
            location_fit = 0.50

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
        with self.session_factory() as db:
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
        with self.session_factory() as db:
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
