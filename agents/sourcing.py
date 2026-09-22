from __future__ import annotations
import os
import re
import json
import hashlib
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import yaml
import httpx
from bs4 import BeautifulSoup

from db.session import SessionLocal, compute_dedup_key, generate_id
from db.models import Job, Application, Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def normalize_text(text: str) -> str:
    """Normalize text by stripping whitespace, emoji markers, and lowering case."""
    clean = re.sub(r"[🔥🎓🛂🇺🇸🔒]", "", text)
    return " ".join(clean.strip().split())

def normalize_company_name(raw: str) -> str:
    """Map common aliases to standardized company names."""
    name = normalize_text(raw)
    mapping = {
        "Amazon": "Amazon / AWS",
        "AWS": "Amazon / AWS",
        "Google": "Google / DeepMind",
        "DeepMind": "Google / DeepMind",
        "Meta": "Meta",
        "Apple": "Apple",
        "Microsoft": "Microsoft",
        "HRT": "Hudson River Trading",
        "Hudson River Trading": "Hudson River Trading",
        "Citadel": "Citadel & Citadel Securities",
        "Citadel Securities": "Citadel & Citadel Securities",
        "Two Sigma": "Two Sigma",
        "Point72": "Point72 / Cubist",
        "Cubist": "Point72 / Cubist",
        "Goldman Sachs": "Goldman Sachs Engineering",
        "JPMorgan": "JPMorgan SEP",
        "JPMorgan Chase": "JPMorgan SEP",
        "Morgan Stanley": "Morgan Stanley Technology",
        "Capital One": "Capital One TIP",
    }
    return mapping.get(name, name)

def normalize_location(raw: str) -> Tuple[str, bool]:
    """Map raw location string to standardized primary location and remote flag."""
    raw_lower = raw.lower()
    remote_ok = "remote" in raw_lower or "virtual" in raw_lower

    if any(k in raw_lower for k in ["new york", "nyc", "manhattan", "ny"]):
        return "New York City", remote_ok
    elif any(k in raw_lower for k in ["san francisco", "sf", "bay area", "palo alto", "mountain view", "sunnyvale", "menlo park", "san jose", "cupertino", "san mateo"]):
        return "SF Bay Area", remote_ok
    elif "chicago" in raw_lower or "il" in raw_lower:
        return "Chicago", remote_ok
    elif any(k in raw_lower for k in ["seattle", "redmond", "bellevue", "wa"]):
        return "Seattle", remote_ok
    elif remote_ok:
        return "Remote", True
    return raw.split(";")[0].strip() or "Other", remote_ok

def detect_ats_type(url: str) -> str:
    """Infer ATS platform from URL."""
    url_lower = url.lower()
    if "greenhouse.io" in url_lower:
        return "greenhouse"
    elif "lever.co" in url_lower:
        return "lever"
    elif "ashbyhq.com" in url_lower:
        return "ashby"
    elif "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    elif "myworkdayjobs.com" in url_lower:
        return "workday"
    elif "icims.com" in url_lower:
        return "icims"
    return "custom"

def check_network() -> bool:
    """Quickly check if external network access is available."""
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.6)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        return True
    except Exception:
        return False

class SourcingAgent:
    """Discovers, normalizes, and upserts internship postings."""

    def __init__(self):
        self.companies_config = load_yaml(PROJECT_ROOT / "config" / "companies.yaml")
        self.filters_config = load_yaml(PROJECT_ROOT / "config" / "filters.yaml")
        self.headers = {
            "User-Agent": "InternshipAssistantBot/1.0 (Summer Internship Application Assistant; https://github.com)"
        }
        self.online = check_network()

    def parse_github_table(self, html_content: str, source_ref: str = "SimplifyJobs/Summer2027-Internships") -> List[Dict[str, Any]]:
        """Parse HTML table rows from GitHub list."""
        soup = BeautifulSoup(html_content, "html.parser")
        postings = []
        current_company = ""

        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue

            comp_td = tds[0]
            comp_text = normalize_text(comp_td.get_text())
            if comp_text == "↳" or not comp_text:
                company = current_company
            else:
                company = normalize_company_name(comp_text)
                current_company = company

            title = normalize_text(tds[1].get_text())
            location_raw = tds[2].get_text(separator="; ").strip()

            # Find apply link
            link_tag = tds[3].find("a")
            apply_url = link_tag.get("href", "") if link_tag else ""
            if not apply_url:
                continue

            loc_norm, remote_ok = normalize_location(location_raw)
            ats_type = detect_ats_type(apply_url)

            # Assign industry from companies.yaml if recognized
            industry = "other"
            for c in self.companies_config.get("companies", []):
                if c["name"].lower() == company.lower() or company.lower() in c["name"].lower():
                    industry = c.get("industry", "other")
                    break

            postings.append({
                "company": company,
                "title": title,
                "location_raw": location_raw,
                "location_norm": loc_norm,
                "remote_ok": remote_ok,
                "industry": industry,
                "ats_type": ats_type,
                "url": apply_url,
                "apply_url": apply_url,
                "term": "Summer 2027",
                "source": "simplify_github",
                "source_ref": source_ref,
                "description_md": f"Summer 2027 Internship at {company}: {title}",
                "requirements": []
            })

        return postings

    def fetch_github_listings(self) -> List[Dict[str, Any]]:
        """Fetch SimplifyJobs markdown/HTML table or fall back to snapshot fixture."""
        if self.online:
            github_urls = [
                "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
                "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md"
            ]

            for url in github_urls:
                try:
                    with httpx.Client(timeout=3.0, headers=self.headers) as client:
                        resp = client.get(url)
                        if resp.status_code == 200 and "<table>" in resp.text:
                            return self.parse_github_table(resp.text, source_ref=url)
                except Exception:
                    continue

        # Fallback to local snapshot fixture
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "simplify_summer2027_snapshot.html"
        if fixture_path.exists():
            with open(fixture_path, "r", encoding="utf-8") as f:
                return self.parse_github_table(f.read(), source_ref="fixture_snapshot")

        return []

    def fetch_greenhouse_jobs(self, token: str, company_name: str, industry: str) -> List[Dict[str, Any]]:
        """Fetch active jobs from Greenhouse public JSON API."""
        if not self.online:
            return []
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        postings = []
        try:
            with httpx.Client(timeout=2.0, headers=self.headers) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("jobs", []):
                        title = item.get("title", "")
                        # Filter for intern / summer keywords
                        if not re.search(r"(?i)\b(intern|internship|co-op)\b", title):
                            continue
                        loc_raw = item.get("location", {}).get("name", "Remote")
                        loc_norm, remote_ok = normalize_location(loc_raw)
                        apply_url = item.get("absolute_url", "")
                        postings.append({
                            "company": company_name,
                            "title": title,
                            "location_raw": loc_raw,
                            "location_norm": loc_norm,
                            "remote_ok": remote_ok,
                            "industry": industry,
                            "ats_type": "greenhouse",
                            "url": apply_url,
                            "apply_url": apply_url,
                            "req_id": str(item.get("id")),
                            "posted_at": item.get("updated_at"),
                            "term": "Summer 2027" if "2027" in title else "probable",
                            "source": "greenhouse_api",
                            "source_ref": url,
                            "description_md": item.get("content", ""),
                            "requirements": []
                        })
        except Exception:
            pass
        return postings

    def fetch_lever_jobs(self, company_token: str, company_name: str, industry: str) -> List[Dict[str, Any]]:
        """Fetch active jobs from Lever public JSON API."""
        if not self.online:
            return []
        url = f"https://api.lever.co/v0/postings/{company_token}?mode=json"
        postings = []
        try:
            with httpx.Client(timeout=2.0, headers=self.headers) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data:
                        title = item.get("text", "")
                        if not re.search(r"(?i)\b(intern|internship|co-op)\b", title):
                            continue
                        loc_raw = item.get("categories", {}).get("location", "Remote")
                        loc_norm, remote_ok = normalize_location(loc_raw)
                        apply_url = item.get("hostedUrl", "")
                        postings.append({
                            "company": company_name,
                            "title": title,
                            "location_raw": loc_raw,
                            "location_norm": loc_norm,
                            "remote_ok": remote_ok,
                            "industry": industry,
                            "ats_type": "lever",
                            "url": apply_url,
                            "apply_url": apply_url,
                            "req_id": str(item.get("id")),
                            "term": "Summer 2027" if "2027" in title else "probable",
                            "source": "lever_api",
                            "source_ref": url,
                            "description_md": item.get("descriptionPlain", ""),
                            "requirements": []
                        })
        except Exception:
            pass
        return postings

    def fetch_ashby_jobs(self, org_token: str, company_name: str, industry: str) -> List[Dict[str, Any]]:
        """Fetch active jobs from Ashby public posting API."""
        if not self.online:
            return []
        url = f"https://api.ashbyhq.com/posting-api/job-board/{org_token}"
        postings = []
        try:
            with httpx.Client(timeout=2.0, headers=self.headers) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("jobs", []):
                        title = item.get("title", "")
                        if not re.search(r"(?i)\b(intern|internship|co-op)\b", title):
                            continue
                        loc_raw = item.get("location", "Remote")
                        loc_norm, remote_ok = normalize_location(loc_raw)
                        apply_url = item.get("jobUrl", "")
                        postings.append({
                            "company": company_name,
                            "title": title,
                            "location_raw": loc_raw,
                            "location_norm": loc_norm,
                            "remote_ok": remote_ok,
                            "industry": industry,
                            "ats_type": "ashby",
                            "url": apply_url,
                            "apply_url": apply_url,
                            "req_id": str(item.get("id")),
                            "term": "Summer 2027" if "2027" in title else "probable",
                            "source": "ashby_api",
                            "source_ref": url,
                            "description_md": item.get("descriptionHtml", ""),
                            "requirements": []
                        })
        except Exception:
            pass
        return postings
    def import_manual_posting(
        self,
        url: str,
        company: Optional[str] = None,
        title: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict[str, Any]:
        """Manually import a posting URL (from LinkedIn, Handshake, etc.)."""
        ats_type = detect_ats_type(url)
        comp_name = company or "Target Company"
        role_title = title or "Software Engineer Intern - Summer 2027"
        loc_raw = location or "New York, NY"
        loc_norm, remote_ok = normalize_location(loc_raw)

        dedup_key = compute_dedup_key(comp_name, role_title, loc_norm, url)
        job_id = generate_id("job", dedup_key)
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        with SessionLocal() as db:
            existing = db.query(Job).filter_by(dedup_key=dedup_key).first()
            if existing:
                existing.last_seen = now
                existing.is_open = True
                db.commit()
                return {"status": "exists", "job_id": existing.id, "company": existing.company, "title": existing.title}

            new_job = Job(
                id=job_id,
                dedup_key=dedup_key,
                company=comp_name,
                title=role_title,
                location_raw=loc_raw,
                location_norm=loc_norm,
                remote_ok=remote_ok,
                industry="quant_trading" if "trading" in comp_name.lower() else "big_tech_ai",
                ats_type=ats_type,
                url=url,
                apply_url=url,
                term="Summer 2027",
                description_md=f"Manual imported posting from {url}",
                source="manual_import",
                source_ref=url,
                first_seen=now,
                last_seen=now,
                is_open=True,
            )
            db.add(new_job)

            app = Application(
                id=generate_id("app", new_job.id),
                job_id=new_job.id,
                status="discovered",
                last_event_at=now,
                notes="Imported via manual URL import."
            )
            db.add(app)

            evt = Event(
                id=generate_id("evt", f"{app.id}-manual"),
                application_id=app.id,
                ts=now,
                actor="human",
                type="manual_import",
                payload_json=json.dumps({"url": url})
            )
            db.add(evt)
            db.commit()

            return {"status": "created", "job_id": new_job.id, "company": comp_name, "title": role_title}

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """Execute full discovery and upsert pipeline."""
        all_postings: List[Dict[str, Any]] = []

        # 1. GitHub lists
        gh_postings = self.fetch_github_listings()
        all_postings.extend(gh_postings)

        # 2. Public ATS APIs for registered companies
        for c in self.companies_config.get("companies", []):
            ats = c.get("ats")
            token = c.get("token")
            name = c.get("name")
            ind = c.get("industry", "other")
            if not token:
                continue

            if ats == "greenhouse":
                all_postings.extend(self.fetch_greenhouse_jobs(token, name, ind))
            elif ats == "lever":
                all_postings.extend(self.fetch_lever_jobs(token, name, ind))
            elif ats == "ashby":
                all_postings.extend(self.fetch_ashby_jobs(token, name, ind))

        total_discovered = len(all_postings)
        if dry_run:
            return {
                "total_discovered": total_discovered,
                "new_inserted": 0,
                "updated": 0,
                "dry_run": True
            }

        new_inserted = 0
        updated = 0
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        seen_in_batch = set()

        with SessionLocal() as db:
            for p in all_postings:
                comp = p["company"]
                title = p["title"]
                loc_norm = p["location_norm"]
                req_or_url = p.get("req_id") or p.get("apply_url") or p.get("url") or ""
                dedup_key = compute_dedup_key(comp, title, loc_norm, req_or_url)

                if dedup_key in seen_in_batch:
                    continue
                seen_in_batch.add(dedup_key)

                existing = db.query(Job).filter_by(dedup_key=dedup_key).first()
                if existing:
                    existing.last_seen = now
                    existing.consecutive_missing = 0
                    existing.is_open = True
                    updated += 1
                else:
                    posted_raw = p.get("posted_at")
                    posted_dt = now
                    if isinstance(posted_raw, str):
                        try:
                            posted_dt = datetime.datetime.fromisoformat(posted_raw).replace(tzinfo=None)
                        except Exception:
                            posted_dt = now
                    elif isinstance(posted_raw, datetime.datetime):
                        posted_dt = posted_raw.replace(tzinfo=None)

                    job_id = generate_id("job", dedup_key)
                    new_job = Job(
                        id=job_id,
                        dedup_key=dedup_key,
                        company=comp,
                        title=title,
                        location_raw=p.get("location_raw", loc_norm),
                        location_norm=loc_norm,
                        remote_ok=p.get("remote_ok", False),
                        industry=p.get("industry", "other"),
                        ats_type=p.get("ats_type", "custom"),
                        url=p.get("url", ""),
                        apply_url=p.get("apply_url", p.get("url", "")),
                        req_id=p.get("req_id"),
                        posted_at=posted_dt,
                        term=p.get("term", "Summer 2027"),
                        description_md=p.get("description_md", ""),
                        requirements_json=json.dumps(p.get("requirements", [])),
                        source=p.get("source", "sourcing_agent"),
                        source_ref=p.get("source_ref", ""),
                        first_seen=now,
                        last_seen=now,
                        consecutive_missing=0,
                        is_open=True
                    )
                    db.add(new_job)

                    app_id = generate_id("app", new_job.id)
                    app = Application(
                        id=app_id,
                        job_id=new_job.id,
                        status="discovered",
                        last_event_at=now
                    )
                    db.add(app)

                    evt = Event(
                        id=generate_id("evt", f"{app_id}-disc"),
                        application_id=app_id,
                        ts=now,
                        actor="agent",
                        type="discovered",
                        payload_json=json.dumps({"source": p.get("source"), "title": title})
                    )
                    db.add(evt)
                    new_inserted += 1

            db.commit()

        return {
            "total_discovered": total_discovered,
            "new_inserted": new_inserted,
            "updated": updated,
            "dry_run": False
        }
