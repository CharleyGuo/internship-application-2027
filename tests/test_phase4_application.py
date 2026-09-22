import os
import json
import pytest
import asyncio
import datetime
from pathlib import Path

from db.session import init_db, SessionLocal, generate_id
from db.models import Job, Application, Score, Artifact, Event
from agents.apply import (
    ApplicationAgent,
    AlreadySubmittedError,
    DailySubmissionCapExceeded,
    SubmissionBlockedError
)
from tools.hooks import (
    has_human_approval,
    check_submission_approval_or_block,
    pre_tool_use_approval_hook
)
from adapters.greenhouse import GreenhouseAdapter
from adapters.workday import WorkdayAdapter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_HTML = PROJECT_ROOT / "tests" / "fixtures" / "greenhouse_form.html"

@pytest.fixture(autouse=True)
def setup_db():
    init_db()

@pytest.fixture
def sample_hrt_job():
    """Create a sample HRT job and application for testing."""
    job_id = "job_test_hrt_p4"
    dedup_key = "dedup_test_hrt_p4"
    with SessionLocal() as db:
        # Clean existing
        db.query(Event).filter(Event.id.like(f"%{job_id}%")).delete(synchronize_session=False)
        db.query(Artifact).filter(Artifact.id.like(f"%{job_id}%")).delete(synchronize_session=False)
        existing_app = db.query(Application).filter_by(job_id=job_id).first()
        if existing_app:
            db.query(Event).filter_by(application_id=existing_app.id).delete(synchronize_session=False)
            db.query(Artifact).filter_by(application_id=existing_app.id).delete(synchronize_session=False)
            db.delete(existing_app)
        db.query(Score).filter_by(job_id=job_id).delete(synchronize_session=False)
        existing_job = db.query(Job).filter_by(id=job_id).first()
        if existing_job:
            db.delete(existing_job)
        db.commit()

        job = Job(
            id=job_id,
            dedup_key=dedup_key,
            company="Hudson River Trading",
            title="Software Engineer Intern - Systems & Core Tech (Summer 2027)",
            location_raw="New York, NY",
            location_norm="New York, NY",
            ats_type="greenhouse",
            url=f"file://{FIXTURE_HTML.resolve()}",
            apply_url=f"file://{FIXTURE_HTML.resolve()}",
            industry="quant_trading",
            source="greenhouse_fixture",
            first_seen=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        )
        db.add(job)

        score = Score(
            job_id=job_id,
            eligible=True,
            total=0.92,
            industry_fit=1.0,
            location_fit=0.9,
            skills_overlap=0.85,
            deadline_urgency=0.8,
            prestige_prior=1.0,
            rationale="Exceptional match for high-performance C++ systems engineering at HRT."
        )
        db.add(score)

        app = Application(
            id=f"app_{job_id}",
            job_id=job_id,
            status="qualified",
            last_event_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        )
        db.add(app)
        db.commit()

    return job_id

def test_greenhouse_form_prefill_and_halt(sample_hrt_job):
    """Verify 100% of fields filled, screenshot captured, review packet generated, and halted before submit."""
    agent = ApplicationAgent()
    res = asyncio.run(agent.prefill_application(sample_hrt_job, custom_page_url=f"file://{FIXTURE_HTML.resolve()}"))

    assert res["status"] == "ready_for_review"
    assert res["job_id"] == sample_hrt_job

    fields = res["filled_fields"]
    prof_personal = agent.profile.get("personal", {})
    prof_edu = agent.profile.get("education", {})
    prof_links = agent.profile.get("links", {})

    # Standard personal fields
    assert fields["first_name"] == prof_personal.get("first_name")
    assert fields["last_name"] == prof_personal.get("last_name")
    assert fields["email"] == prof_personal.get("email")
    assert fields["phone"] == prof_personal.get("phone")

    # Links
    assert "linkedin_url" in fields
    assert "github_url" in fields

    # Education & work auth
    assert fields["school"] == prof_edu.get("institution")
    assert fields["degree"] == prof_edu.get("degree")
    assert fields["major"] == prof_edu.get("major")
    assert fields["gpa"] == str(prof_edu.get("gpa"))
    assert fields["work_authorization"] == "yes"

    # Artifacts verified on disk
    dest_dir = PROJECT_ROOT / "artifacts" / sample_hrt_job
    screenshot_path = dest_dir / "screenshot.png"
    form_dump_path = dest_dir / "form_dump.json"
    review_packet_path = dest_dir / "review_packet.md"

    assert screenshot_path.exists()
    assert screenshot_path.stat().st_size > 0

    assert form_dump_path.exists()
    with open(form_dump_path, "r", encoding="utf-8") as f:
        dump_data = json.load(f)
        assert dump_data["fields"]["first_name"] == prof_personal.get("first_name")
        assert dump_data["fields"]["email"] == prof_personal.get("email")

    assert review_packet_path.exists()
    with open(review_packet_path, "r", encoding="utf-8") as f:
        packet_content = f.read()
        assert "Hudson River Trading" in packet_content
        assert "0.92" in packet_content
        assert "ia approve" in packet_content
        assert "ia edit" in packet_content
        assert "ia reject" in packet_content
        assert "Facts Bank Citations" in packet_content

    # Verify DB state
    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=sample_hrt_job).first()
        assert app.status == "ready_for_review"

        artifacts = db.query(Artifact).filter_by(application_id=app.id).all()
        artifact_kinds = {a.kind for a in artifacts}
        assert "screenshot" in artifact_kinds
        assert "form_dump" in artifact_kinds
        assert "review_packet" in artifact_kinds

        event = db.query(Event).filter_by(application_id=app.id, type="ready_for_review").first()
        assert event is not None
        assert event.actor == "agent"

def test_mechanical_approval_guarantee_blocks_unapproved_submit(sample_hrt_job):
    """Verify that an attempt to submit without human approval is mechanically blocked and audited."""
    # Ensure no human approval exists
    assert not has_human_approval(sample_hrt_job)

    # Calling check_submission_approval_or_block must raise SubmissionBlockedError
    with pytest.raises(SubmissionBlockedError) as exc_info:
        check_submission_approval_or_block(sample_hrt_job, "#submit_app (Submit Application)")

    assert "MECHANICAL GUARANTEE BLOCKED" in str(exc_info.value)

    # Verify audit event logged
    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=sample_hrt_job).first()
        blocked_event = (
            db.query(Event)
            .filter_by(application_id=app.id, type="hook_blocked")
            .order_by(Event.ts.desc())
            .first()
        )
        assert blocked_event is not None
        assert blocked_event.actor == "agent"
        payload = json.loads(blocked_event.payload_json)
        assert payload["human_approval_present"] is False
        assert "PreToolUse hook blocked" in payload["reason"]

def test_sdk_pre_tool_use_hook_decision(sample_hrt_job):
    """Verify Claude Agent SDK PreToolUse hook denies click on submit without human approval."""
    # 1. Deny when unapproved
    decision = asyncio.run(pre_tool_use_approval_hook(
        input_data={"tool_input": {"selector": "#submit_app", "text": "Submit Application", "job_id": sample_hrt_job}},
        tool_use_id="call_submit_1",
        context=None
    ))
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    # 2. Allow non-submit click
    nav_decision = asyncio.run(pre_tool_use_approval_hook(
        input_data={"tool_input": {"selector": "#next_page", "text": "Continue", "job_id": sample_hrt_job}},
        tool_use_id="call_nav_1",
        context=None
    ))
    assert nav_decision["hookSpecificOutput"]["permissionDecision"] == "allow"

    # 3. Grant human approval
    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=sample_hrt_job).first()
        approval_event = Event(
            id=f"evt_test_appr_{sample_hrt_job}",
            application_id=app.id,
            ts=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
            actor="human",
            type="approval_granted",
            payload_json=json.dumps({"command": f"ia approve {sample_hrt_job}"})
        )
        db.add(approval_event)
        db.commit()

    # 4. Now hook should allow
    approved_decision = asyncio.run(pre_tool_use_approval_hook(
        input_data={"tool_input": {"selector": "#submit_app", "text": "Submit Application", "job_id": sample_hrt_job}},
        tool_use_id="call_submit_2",
        context=None
    ))
    assert approved_decision["hookSpecificOutput"]["permissionDecision"] == "allow"

def test_approved_submission_succeeds(sample_hrt_job):
    """Verify that after human approval is granted, submission succeeds, confirmation ID is extracted, and DB is updated."""
    agent = ApplicationAgent()

    # Pre-fill first
    asyncio.run(agent.prefill_application(sample_hrt_job, custom_page_url=f"file://{FIXTURE_HTML.resolve()}"))

    # Now execute approve_and_submit
    res = asyncio.run(agent.approve_and_submit(
        sample_hrt_job,
        actor="human",
        custom_page_url=f"file://{FIXTURE_HTML.resolve()}"
    ))

    assert res["status"] == "submitted"
    assert res["confirmation_id"] == "HRT-2027-SWE-98412"

    # Verify confirmation screenshot exists
    conf_screenshot = PROJECT_ROOT / "artifacts" / sample_hrt_job / "confirmation_screenshot.png"
    assert conf_screenshot.exists()
    assert conf_screenshot.stat().st_size > 0

    # Verify database state
    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=sample_hrt_job).first()
        assert app.status == "submitted"
        assert app.confirmation_id == "HRT-2027-SWE-98412"
        assert app.submitted_at is not None

        # Check events
        events = db.query(Event).filter_by(application_id=app.id).all()
        event_types = {e.type: e.actor for e in events}
        assert event_types.get("approval_granted") == "human"
        assert event_types.get("submitted") == "agent"

def test_anti_duplicate_submission_protection(sample_hrt_job):
    """Verify that attempting to re-submit for an already submitted job raises AlreadySubmittedError."""
    agent = ApplicationAgent()
    # Execute first submission
    asyncio.run(agent.approve_and_submit(
        sample_hrt_job,
        actor="human",
        custom_page_url=f"file://{FIXTURE_HTML.resolve()}"
    ))

    # Attempt second submission
    with pytest.raises(AlreadySubmittedError):
        asyncio.run(agent.approve_and_submit(
            sample_hrt_job,
            actor="human",
            custom_page_url=f"file://{FIXTURE_HTML.resolve()}"
        ))

def test_daily_submission_cap_enforcement(sample_hrt_job):
    """Verify that if daily cap is reached, further submissions are prevented."""
    agent = ApplicationAgent()
    agent.daily_cap = 0  # Force limit to 0

    with pytest.raises(DailySubmissionCapExceeded):
        asyncio.run(agent.approve_and_submit(sample_hrt_job, actor="human"))

def test_human_review_edit_and_reject(sample_hrt_job):
    """Verify human review commands: ia edit and ia reject."""
    agent = ApplicationAgent()

    # Pre-fill
    asyncio.run(agent.prefill_application(sample_hrt_job, custom_page_url=f"file://{FIXTURE_HTML.resolve()}"))

    # 1. Edit field
    edit_res = agent.edit_field(sample_hrt_job, "first_name", "Jonathan")
    assert edit_res["field"] == "first_name"
    assert edit_res["new_value"] == "Jonathan"

    dest_dir = PROJECT_ROOT / "artifacts" / sample_hrt_job
    with open(dest_dir / "form_dump.json", "r", encoding="utf-8") as f:
        dump = json.load(f)
        assert dump["fields"]["first_name"] == "Jonathan"

    # Verify edit event logged
    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=sample_hrt_job).first()
        edit_evt = db.query(Event).filter_by(application_id=app.id, type="edited").first()
        assert edit_evt is not None
        assert edit_evt.actor == "human"

    # Test reject / skip
    reject_res = agent.reject_application(sample_hrt_job, reason="Location preference")
    assert reject_res["status"] == "skipped"

    with SessionLocal() as db:
        app = db.query(Application).filter_by(job_id=sample_hrt_job).first()
        assert app.status == "skipped"
        assert app.notes == "Location preference"
        skip_evt = db.query(Event).filter_by(application_id=app.id, type="skipped").first()
        assert skip_evt is not None
        assert skip_evt.actor == "human"

def test_workday_sidecar_generation():
    """Verify Workday stub adapter creates sidecar JSON with candidate info and free-text answers."""
    wd_adapter = WorkdayAdapter()
    profile = {"personal": {"first_name": "TestCandidate", "last_name": "User", "email": "test@example.com"}}
    answers = {"answers": [{"question": "Why Workday?", "answer": "Solid cloud architecture."}]}
    resume = PROJECT_ROOT / "artifacts" / "job_test_hrt_p4" / "resume.pdf"
    resume.parent.mkdir(parents=True, exist_ok=True)
    resume.write_text("Dummy resume")

    # Mock dummy page
    from unittest.mock import MagicMock
    dummy_page = MagicMock()

    res = asyncio.run(wd_adapter.fill_form(dummy_page, profile, answers, resume))
    assert res["workday_mode"] == "manual_sidecar_paste"
    sidecar_file = Path(res["sidecar_json"])
    assert sidecar_file.exists()
    with open(sidecar_file, "r", encoding="utf-8") as f:
        sidecar_data = json.load(f)
        assert sidecar_data["personal_info"]["first_name"] == "TestCandidate"
        assert "workday_notice" in sidecar_data
