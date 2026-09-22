from __future__ import annotations
import json
import hashlib
import datetime
from typing import Dict, Any, List, Optional
import httpx
from bs4 import BeautifulSoup

from claude_agent_sdk import tool, create_sdk_mcp_server
from db.session import SessionLocal, compute_dedup_key, generate_id
from db.models import Job, Score, Application, Event

@tool("db_upsert_job", "Insert or update a discovered job posting in SQLite", {"job_data": dict})
async def db_upsert_job(args: Dict[str, Any]) -> Dict[str, Any]:
    """Upserts a job into the SQLite database with dedup hashing."""
    job_dict = args.get("job_data", {})
    company = job_dict.get("company", "").strip()
    title = job_dict.get("title", "").strip()
    location_raw = job_dict.get("location_raw", "").strip() or "Remote"
    location_norm = job_dict.get("location_norm", "").strip() or location_raw
    req_id_or_url = job_dict.get("req_id") or job_dict.get("apply_url") or job_dict.get("url") or ""

    dedup_key = compute_dedup_key(company, title, location_norm, req_id_or_url)
    job_id = job_dict.get("id") or generate_id("job", dedup_key)

    with SessionLocal() as db:
        existing = db.query(Job).filter_by(dedup_key=dedup_key).first()
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if existing:
            existing.last_seen = now
            existing.consecutive_missing = 0
            existing.is_open = True
            if job_dict.get("description_md") and not existing.description_md:
                existing.description_md = job_dict["description_md"]
            db.commit()
            return {"content": [{"type": "text", "text": json.dumps({"action": "updated", "id": existing.id, "dedup_key": dedup_key})}]}

        new_job = Job(
            id=job_id,
            dedup_key=dedup_key,
            company=company,
            title=title,
            location_raw=location_raw,
            location_norm=location_norm,
            remote_ok=job_dict.get("remote_ok", False),
            industry=job_dict.get("industry", "other"),
            ats_type=job_dict.get("ats_type", "custom"),
            url=job_dict.get("url", ""),
            apply_url=job_dict.get("apply_url", job_dict.get("url", "")),
            req_id=job_dict.get("req_id"),
            posted_at=job_dict.get("posted_at") or now,
            term=job_dict.get("term", "Summer 2027"),
            description_md=job_dict.get("description_md", ""),
            requirements_json=json.dumps(job_dict.get("requirements", [])),
            source=job_dict.get("source", "manual"),
            source_ref=job_dict.get("source_ref", ""),
            first_seen=now,
            last_seen=now,
            consecutive_missing=0,
            is_open=True,
        )
        db.add(new_job)

        # Create linked Application in discovered status
        app_id = generate_id("app", new_job.id)
        app = Application(
            id=app_id,
            job_id=new_job.id,
            status="discovered",
            last_event_at=now
        )
        db.add(app)

        event = Event(
            id=generate_id("evt", f"{app_id}-disc"),
            application_id=app_id,
            ts=now,
            actor="agent",
            type="discovered",
            payload_json=json.dumps({"source": job_dict.get("source"), "company": company, "title": title})
        )
        db.add(event)
        db.commit()

        return {"content": [{"type": "text", "text": json.dumps({"action": "inserted", "id": new_job.id, "dedup_key": dedup_key})}]}

@tool("db_query", "Query jobs and applications from SQLite", {"table": str, "filter_by": dict})
async def db_query(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute filtered query on database."""
    table = args.get("table", "jobs")
    filters = args.get("filter_by", {})
    results = []

    with SessionLocal() as db:
        if table == "jobs":
            query = db.query(Job)
            for k, v in filters.items():
                if hasattr(Job, k):
                    query = query.filter(getattr(Job, k) == v)
            jobs = query.limit(50).all()
            results = [
                {
                    "id": j.id,
                    "company": j.company,
                    "title": j.title,
                    "location": j.location_norm,
                    "term": j.term,
                    "is_open": j.is_open,
                    "source": j.source
                }
                for j in jobs
            ]
        elif table == "applications":
            query = db.query(Application)
            for k, v in filters.items():
                if hasattr(Application, k):
                    query = query.filter(getattr(Application, k) == v)
            apps = query.limit(50).all()
            results = [
                {
                    "id": a.id,
                    "job_id": a.job_id,
                    "company": a.job.company if a.job else "",
                    "title": a.job.title if a.job else "",
                    "status": a.status,
                    "notes": a.notes
                }
                for a in apps
            ]

    return {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]}

@tool("fetch_json", "Safely fetch JSON from a public URL with honest bot user-agent", {"url": str})
async def fetch_json(args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch JSON from a URL with timeout and custom User-Agent."""
    url = args.get("url", "")
    headers = {"User-Agent": "InternshipAssistantBot/1.0 (Summer Internship Application Assistant; https://github.com)"}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return {"content": [{"type": "text", "text": json.dumps(data)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}]}

@tool("parse_github_table", "Parse SimplifyJobs internship markdown/HTML table", {"table_html": str, "source_ref": str})
async def parse_github_table(args: Dict[str, Any]) -> Dict[str, Any]:
    """Parse HTML table rows from GitHub list into structured posting dicts."""
    html_content = args.get("table_html", "")
    source_ref = args.get("source_ref", "SimplifyJobs/Summer2027-Internships")
    soup = BeautifulSoup(html_content, "html.parser")

    jobs = []
    current_company = ""

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        # Column 0: Company
        comp_td = tds[0]
        comp_text = comp_td.get_text().strip().replace("🔥", "").strip()
        if comp_text == "↳" or not comp_text:
            company_name = current_company
        else:
            company_name = comp_text
            current_company = comp_text

        # Column 1: Role title
        title_td = tds[1]
        title = title_td.get_text().strip().replace("🎓", "").strip()

        # Column 2: Location
        loc_td = tds[2]
        location = loc_td.get_text(separator="; ").strip()

        # Column 3: Application Link
        apply_td = tds[3]
        link_tag = apply_td.find("a")
        apply_url = link_tag.get("href", "") if link_tag else ""

        if company_name and title and apply_url:
            jobs.append({
                "company": company_name,
                "title": title,
                "location_raw": location,
                "apply_url": apply_url,
                "url": apply_url,
                "source": "simplify_github",
                "source_ref": source_ref,
                "term": "Summer 2027"
            })

    return {"content": [{"type": "text", "text": json.dumps({"count": len(jobs), "jobs": jobs[:100]})}]}

# Server instance
sourcing_mcp_server = create_sdk_mcp_server(
    name="internship-sourcing-tools",
    version="1.0.0",
    tools=[db_upsert_job, db_query, fetch_json, parse_github_table]
)
