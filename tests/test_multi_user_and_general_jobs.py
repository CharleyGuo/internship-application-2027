import os
import shutil
import pytest
from pathlib import Path
from typer.testing import CliRunner

from cli import app
from db.session import (
    init_db,
    resolve_database_path,
    get_user_session_factory,
    generate_id,
    SessionLocal
)
from db.models import Job, Application, Score
from agents.rank import FilterAndRankAgent

PROJECT_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

def test_multi_user_db_and_profile_isolation():
    """Verify dedicated SQLite database (data/<user>.db) generation and complete isolation from internships.db."""
    username = "test_bob"
    bob_db_path = resolve_database_path(username)
    assert bob_db_path.name == f"{username}.db"
    assert bob_db_path.parent == PROJECT_ROOT / "data"

    # Initialize bob's database
    init_db(user=username)
    assert bob_db_path.exists()

    bob_factory = get_user_session_factory(username)
    default_factory = get_user_session_factory(None)

    # 1. Verify default DB has Jane Street & Optiver
    with default_factory() as def_db:
        def_jobs = [j.company for j in def_db.query(Job).all()]
        assert "Optiver" in def_jobs
        assert "Jane Street" in def_jobs

    # 2. Verify Bob's DB is a fresh slate without default seed data
    with bob_factory() as bob_db:
        bob_jobs = [j.company for j in bob_db.query(Job).all()]
        assert "Optiver" not in bob_jobs
        assert "Jane Street" not in bob_jobs

        # Insert a job into Bob's DB
        job = Job(
            id=generate_id("job", "bob-databricks-fulltime"),
            dedup_key="dedup_bob_db_1",
            company="Databricks",
            title="Software Engineer, Distributed Storage (Full-Time)",
            location_raw="San Francisco, CA",
            location_norm="SF Bay Area",
            industry="big_tech_ai",
            ats_type="greenhouse",
            url="https://databricks.com/careers",
            apply_url="https://databricks.com/careers/apply",
            source="manual_import",
            is_open=True
        )
        bob_db.add(job)
        bob_db.commit()

    # 3. Verify Bob's job is visible in Bob's DB but NOT in default internships.db
    with default_factory() as def_db:
        check_def = def_db.query(Job).filter_by(dedup_key="dedup_bob_db_1").first()
        assert check_def is None, "Bob's job must not leak into default internships.db"

    with bob_factory() as bob_db:
        check_bob = bob_db.query(Job).filter_by(dedup_key="dedup_bob_db_1").first()
        assert check_bob is not None, "Bob's job must exist in Bob's database"
        assert check_bob.company == "Databricks"

    # Cleanup
    if bob_db_path.exists():
        bob_db_path.unlink()

def test_cli_init_and_status_multi_user_workflow():
    """Verify CLI init and status commands for an individual onboarded user."""
    username = "test_carol"
    user_dir = PROJECT_ROOT / "users" / username
    carol_db_path = resolve_database_path(username)

    try:
        # Run init --user test_carol --non-interactive
        res_init = runner.invoke(app, ["init", "--user", username, "--non-interactive"])
        assert res_init.exit_code == 0, f"Init failed: {res_init.output}"
        assert f"Setup Complete for {username}" in res_init.output

        # Verify user workspace files created
        assert user_dir.exists()
        assert (user_dir / "profile.yaml").exists()
        assert (user_dir / "facts.yaml").exists()
        assert (user_dir / "filters.yaml").exists()
        assert (user_dir / "variants" / "general.md").exists()
        assert carol_db_path.exists()

        # Run status for test_carol
        res_status = runner.invoke(app, ["status", "--user", username])
        assert res_status.exit_code == 0
        assert "Candidate Profile" in res_status.output
        assert "Target Companies ATS Coverage" in res_status.output

    finally:
        # Cleanup
        if user_dir.exists():
            shutil.rmtree(user_dir)
        if carol_db_path.exists():
            carol_db_path.unlink()

def test_general_swe_role_filtering_and_ranking():
    """Verify general job search mode correctly filters and scores Full-Time, New Grad, and Internship roles."""
    ranker = FilterAndRankAgent()
    ranker.job_search_mode = "general"
    ranker.target_job_types = ["full_time", "new_grad", "internship"]

    # 1. Target Full-Time SWE role
    fulltime_job = Job(
        id="job_test_ft",
        company="Stripe",
        title="Software Engineer, Core Infrastructure",
        location_raw="Seattle, WA",
        location_norm="Seattle, WA",
        industry="fintech_banks",
        description_md="Design and operate distributed payments infrastructure using Go and Java."
    )
    score_ft = ranker.compute_score(fulltime_job)
    assert score_ft.eligible is True
    assert score_ft.total >= 0.50

    # 2. Target New Grad SWE role
    newgrad_job = Job(
        id="job_test_ng",
        company="Anthropic",
        title="New Grad Software Engineer (2026/2027)",
        location_raw="San Francisco, CA",
        location_norm="SF Bay Area",
        industry="big_tech_ai",
        description_md="Develop high performance compute infrastructure for large language models using Python and Rust."
    )
    score_ng = ranker.compute_score(newgrad_job)
    assert score_ng.eligible is True
    assert score_ng.total >= 0.50

    # 3. Target Internship SWE role
    intern_job = Job(
        id="job_test_intern",
        company="Citadel",
        title="Software Engineer Intern (Summer 2027)",
        location_raw="New York, NY",
        location_norm="New York City",
        industry="quant_trading",
        description_md="Low latency C++ trading systems development."
    )
    score_intern = ranker.compute_score(intern_job)
    assert score_intern.eligible is True
    assert score_intern.total >= 0.60

    # 4. Excluded Senior Role (Director/Principal)
    director_job = Job(
        id="job_test_dir",
        company="Google",
        title="Director of Engineering, Cloud Infrastructure",
        location_raw="Sunnyvale, CA",
        location_norm="SF Bay Area",
        industry="big_tech_ai",
        description_md="15+ years experience leading engineering organizations."
    )
    score_dir = ranker.compute_score(director_job)
    assert score_dir.eligible is False
    assert "seniority" in score_dir.rationale.lower() or "hard filter" in score_dir.rationale.lower()

    # 5. Excluded Data Engineer role
    data_eng_job = Job(
        id="job_test_de",
        company="Snowflake",
        title="Data Engineer, Enterprise Analytics",
        location_raw="San Mateo, CA",
        location_norm="SF Bay Area",
        industry="big_tech_ai",
        description_md="Manage ETL pipelines and analytics data marts."
    )
    score_de = ranker.compute_score(data_eng_job)
    assert score_de.eligible is False
    assert "data engineer" in score_de.rationale.lower() or "data_engineer" in score_de.rationale.lower()

def test_cli_update_command_and_next_action():
    """Verify 'ja update' updates application status/notes, logs audit event, and refreshes next action."""
    from db.models import Event
    from agents.tracker import TrackerAgent

    # 1. Update Jane Street interview notes via CLI
    res = runner.invoke(app, ["update", "Jane Street", "--notes", "Prep underway. Onsite interview scheduled for 10/16, Friday."])
    assert res.exit_code == 0
    assert "Updated application for Jane Street" in res.output

    # 2. Verify DB state and audit event
    with SessionLocal() as db:
        js_app = db.query(Application).join(Job).filter(Job.company == "Jane Street").first()
        assert js_app is not None
        assert "prep underway" in js_app.notes.lower()
        assert "10/16" in js_app.notes
        
        events = db.query(Event).filter(Event.application_id == js_app.id).order_by(Event.ts.desc()).all()
        assert any(e.type == "status_update" and "10/16" in (e.payload_json or "") for e in events)

    # 3. Verify next action mapping in TrackerAgent
    tracker = TrackerAgent()
    next_act = tracker._determine_next_action(js_app.status, "Jane Street", js_app.notes)
    assert "Prep onsite interview (scheduled for 10/16, Friday)" in next_act

