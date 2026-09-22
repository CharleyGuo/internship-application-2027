from __future__ import annotations
import datetime
from typing import Optional, List
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Float,
    DateTime,
    Text,
    ForeignKey,
    Index,
    Integer,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Job(Base):
    """Normalized posting discovered by sourcing agents."""
    __tablename__ = "jobs"

    id = Column(String(64), primary_key=True)
    dedup_key = Column(String(64), unique=True, nullable=False, index=True)
    company = Column(String(128), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    location_raw = Column(String(256), nullable=False)
    location_norm = Column(String(128), nullable=False)
    remote_ok = Column(Boolean, default=False)
    industry = Column(String(64), nullable=False, index=True)
    ats_type = Column(String(64), nullable=False)  # greenhouse, lever, ashby, workday, smartrecruiters, icims, custom
    url = Column(String(1024), nullable=False)
    apply_url = Column(String(1024), nullable=False)
    req_id = Column(String(128), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    closes_at = Column(DateTime, nullable=True)
    term = Column(String(64), default="Summer 2027")
    description_md = Column(Text, default="")
    requirements_json = Column(Text, default="[]")
    source = Column(String(64), nullable=False)  # simplify_github, greenhouse_api, lever_api, etc.
    source_ref = Column(String(256), nullable=True)  # commit SHA, endpoint URL, etc.
    first_seen = Column(DateTime, default=datetime.datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.datetime.utcnow)
    consecutive_missing = Column(Integer, default=0)
    is_open = Column(Boolean, default=True)

    # Relationships
    score = relationship("Score", back_populates="job", uselist=False, cascade="all, delete-orphan")
    application = relationship("Application", back_populates="job", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Job id={self.id} company={self.company} title={self.title} is_open={self.is_open}>"


class Score(Base):
    """Calculated fit and ranking score for a job."""
    __tablename__ = "scores"

    job_id = Column(String(64), ForeignKey("jobs.id"), primary_key=True)
    industry_fit = Column(Float, default=0.0)
    location_fit = Column(Float, default=0.0)
    skills_overlap = Column(Float, default=0.0)
    deadline_urgency = Column(Float, default=0.0)
    prestige_prior = Column(Float, default=0.0)
    total = Column(Float, default=0.0, index=True)
    eligible = Column(Boolean, default=True)
    eligibility_note = Column(Text, nullable=True)
    rationale = Column(Text, default="")
    scored_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    job = relationship("Job", back_populates="score")

    def __repr__(self) -> str:
        return f"<Score job_id={self.job_id} total={self.total:.3f} eligible={self.eligible}>"


class Application(Base):
    """Application state tracking and review status."""
    __tablename__ = "applications"

    id = Column(String(64), primary_key=True)
    job_id = Column(String(64), ForeignKey("jobs.id"), unique=True, nullable=False)
    # Statuses: discovered, qualified, tailored, ready_for_review, submitted, oa, phone, onsite, offer, rejected, withdrawn, in_process, skipped
    status = Column(String(64), default="discovered", index=True)
    resume_variant = Column(String(64), default="general")  # systems, quant, general
    artifacts_dir = Column(String(512), nullable=True)
    confirmation_id = Column(String(256), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    last_event_at = Column(DateTime, default=datetime.datetime.utcnow)
    notes = Column(Text, nullable=True)

    # Relationships
    job = relationship("Job", back_populates="application")
    events = relationship("Event", back_populates="application", cascade="all, delete-orphan", order_by="Event.ts")
    artifacts = relationship("Artifact", back_populates="application", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Application id={self.id} job_id={self.job_id} status={self.status}>"


class Event(Base):
    """Audit log of all agent and human interactions."""
    __tablename__ = "events"

    id = Column(String(64), primary_key=True)
    application_id = Column(String(64), ForeignKey("applications.id"), nullable=False, index=True)
    ts = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    actor = Column(String(32), nullable=False)  # 'agent' or 'human'
    type = Column(String(64), nullable=False)  # status_change, approved, rejected, edited, hook_blocked, prefilled, submitted
    payload_json = Column(Text, default="{}")

    # Relationships
    application = relationship("Application", back_populates="events")

    def __repr__(self) -> str:
        return f"<Event id={self.id} app={self.application_id} type={self.type} actor={self.actor}>"


class Artifact(Base):
    """Generated files, diffs, snapshots, and bundles."""
    __tablename__ = "artifacts"

    id = Column(String(64), primary_key=True)
    application_id = Column(String(64), ForeignKey("applications.id"), nullable=False, index=True)
    kind = Column(String(64), nullable=False)  # resume, cover, answers, screenshot, form_dump, review_packet, flags
    path = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False)

    # Relationships
    application = relationship("Application", back_populates="artifacts")

    def __repr__(self) -> str:
        return f"<Artifact id={self.id} kind={self.kind} path={self.path}>"
