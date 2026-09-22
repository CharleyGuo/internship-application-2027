from __future__ import annotations
import os
import hashlib
import json
import datetime
from pathlib import Path
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from db.models import Base, Job, Score, Application, Event

DB_FILE = Path(__file__).resolve().parent.parent / "internships.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_FILE}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Generator[Session, None, None]:
    """Yield a transactional database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_id(prefix: str, content: str) -> str:
    """Generate a stable deterministic ID from content."""
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"

def compute_dedup_key(company: str, title: str, location: str, req_id_or_url: str) -> str:
    """Compute sha1(normalize(company) + normalize(title) + normalize(location) + req_id_or_url_path)."""
    norm = lambda s: "".join(ch.lower() for ch in (s or "") if ch.isalnum())
    key_str = f"{norm(company)}:{norm(title)}:{norm(location)}:{req_id_or_url.strip().lower()}"
    return hashlib.sha1(key_str.encode("utf-8")).hexdigest()

def seed_initial_pipeline(db: Session) -> None:
    """Import initial in-process applications (Optiver, Jane Street)."""
    # 1. Optiver SWE Intern (Summer 2027, Chicago)
    optiver_dedup = compute_dedup_key(
        company="Optiver",
        title="Software Engineer Intern",
        location="Chicago, IL",
        req_id_or_url="optiver-swe-intern-summer-2027-chicago"
    )
    existing_optiver = db.query(Job).filter_by(dedup_key=optiver_dedup).first()
    if not existing_optiver:
        optiver_job = Job(
            id=generate_id("job", "optiver-summer-2027-swe-chicago"),
            dedup_key=optiver_dedup,
            company="Optiver",
            title="Software Engineer Intern",
            location_raw="Chicago, IL",
            location_norm="Chicago",
            remote_ok=False,
            industry="quant_trading",
            ats_type="smartrecruiters",
            url="https://optiver.com/working-at-optiver/career-opportunities/",
            apply_url="https://optiver.com/working-at-optiver/career-opportunities/",
            req_id="OPT-2027-CHI-SWE",
            posted_at=datetime.datetime(2026, 8, 1),
            term="Summer 2027",
            description_md="Optiver Software Engineering Internship - Summer 2027 (Chicago)",
            source="manual_import",
            source_ref="pipeline_init",
            is_open=True,
        )
        db.add(optiver_job)
        db.flush()

        optiver_app = Application(
            id=generate_id("app", optiver_job.id),
            job_id=optiver_job.id,
            status="in_process",
            resume_variant="quant",
            notes="Phone interview 9/18/2026 and waiting for next step.",
            last_event_at=datetime.datetime(2026, 9, 18, 14, 0)
        )
        db.add(optiver_app)
        db.flush()

        event1 = Event(
            id=generate_id("evt", f"{optiver_app.id}-import"),
            application_id=optiver_app.id,
            ts=datetime.datetime(2026, 9, 18, 14, 0),
            actor="human",
            type="imported_in_process",
            payload_json=json.dumps({"stage": "phone_interview", "date": "2026-09-18", "note": "Phone interview conducted; awaiting next step."})
        )
        db.add(event1)

    # 2. Jane Street SWE Intern (Summer 2027, NYC)
    js_dedup = compute_dedup_key(
        company="Jane Street",
        title="Software Engineer Intern",
        location="New York, NY",
        req_id_or_url="janestreet-swe-intern-summer-2027-nyc"
    )
    existing_js = db.query(Job).filter_by(dedup_key=js_dedup).first()
    if not existing_js:
        js_job = Job(
            id=generate_id("job", "janestreet-summer-2027-swe-nyc"),
            dedup_key=js_dedup,
            company="Jane Street",
            title="Software Engineer Intern",
            location_raw="New York, NY",
            location_norm="New York City",
            remote_ok=False,
            industry="quant_trading",
            ats_type="custom",
            url="https://www.janestreet.com/join-jane-street/open-roles/",
            apply_url="https://www.janestreet.com/join-jane-street/open-roles/",
            req_id="JS-2027-NYC-SWE",
            posted_at=datetime.datetime(2026, 8, 1),
            term="Summer 2027",
            description_md="Jane Street Software Engineering Internship - Summer 2027 (NYC)",
            source="manual_import",
            source_ref="pipeline_init",
            is_open=True,
        )
        db.add(js_job)
        db.flush()

        js_app = Application(
            id=generate_id("app", js_job.id),
            job_id=js_job.id,
            status="in_process",
            resume_variant="systems",
            notes="Prep underway. Phone interview arrange 9/21/2026.",
            last_event_at=datetime.datetime(2026, 9, 20, 10, 0)
        )
        db.add(js_app)
        db.flush()

        event2 = Event(
            id=generate_id("evt", f"{js_app.id}-import"),
            application_id=js_app.id,
            ts=datetime.datetime(2026, 9, 20, 10, 0),
            actor="human",
            type="imported_in_process",
            payload_json=json.dumps({"stage": "prep_underway", "interview_date": "2026-09-21", "note": "Phone interview arranged for 9/21/2026."})
        )
        db.add(event2)

    db.commit()

def init_db() -> None:
    """Initialize database tables and seed day-one applications."""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_initial_pipeline(db)
