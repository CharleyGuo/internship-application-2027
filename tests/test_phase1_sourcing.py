import os
import json
import pytest
from pathlib import Path
from typer.testing import CliRunner

from cli import app
from db.session import init_db, SessionLocal, compute_dedup_key
from db.models import Job, Application, Event, Artifact, Score
from agents.sourcing import SourcingAgent, normalize_company_name, normalize_location, detect_ats_type
from tools.mcp_server import db_upsert_job, db_query, parse_github_table

PROJECT_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

@pytest.fixture(autouse=True)
def setup_database():
    init_db()

def test_normalization_and_detection():
    """Verify company, location, and ATS normalization."""
    assert normalize_company_name("🔥 Amazon") == "Amazon / AWS"
    assert normalize_company_name("DeepMind") == "Google / DeepMind"
    assert normalize_company_name("HRT") == "Hudson River Trading"

    loc_ny, remote1 = normalize_location("New York, NY")
    assert loc_ny == "New York City"
    assert not remote1

    loc_sf, remote2 = normalize_location("Mountain View, CA; San Francisco, CA")
    assert loc_sf == "SF Bay Area"
    assert not remote2

    loc_rem, remote3 = normalize_location("Remote (US)")
    assert loc_rem == "Remote"
    assert remote3

    assert detect_ats_type("https://boards.greenhouse.io/anthropic/jobs/123") == "greenhouse"
    assert detect_ats_type("https://jobs.lever.co/palantir/abc") == "lever"
    assert detect_ats_type("https://jobs.ashbyhq.com/openai/xyz") == "ashby"
    assert detect_ats_type("https://nvidia.wd5.myworkdayjobs.com/site/job") == "workday"
    assert detect_ats_type("https://careers.google.com/jobs") == "custom"

def test_github_table_parsing():
    """Verify HTML table parsing extracts all columns and handles repeated company symbol."""
    sample_html = """
    <table>
    <tr>
      <td>🔥 <strong><a href="https://example.com">Datadog</a></strong></td>
      <td>Software Engineer Intern - Systems - Summer 2027</td>
      <td>New York, NY</td>
      <td><a href="https://boards.greenhouse.io/datadog/jobs/101">Apply</a></td>
      <td>1d</td>
    </tr>
    <tr>
      <td>↳</td>
      <td>Quantitative Developer Intern - Summer 2027</td>
      <td>New York, NY</td>
      <td><a href="https://boards.greenhouse.io/datadog/jobs/102">Apply</a></td>
      <td>1d</td>
    </tr>
    </table>
    """
    agent = SourcingAgent()
    postings = agent.parse_github_table(sample_html)
    assert len(postings) == 2
    assert postings[0]["company"] == "Datadog"
    assert postings[0]["location_norm"] == "New York City"
    assert postings[0]["ats_type"] == "greenhouse"

    assert postings[1]["company"] == "Datadog"  # Resolved from ↳
    assert postings[1]["title"] == "Quantitative Developer Intern - Summer 2027"

def test_phase1_acceptance_sourcing_and_deduplication():
    """Phase 1 Acceptance Test:
    >= 50 unique open Summer 2027 SWE-intern postings discovered.
    A second run produces zero duplicate records.
    """
    # Clean non-imported jobs to test fresh discovery and insertion
    with SessionLocal() as db:
        in_proc_job_ids = [a.job_id for a in db.query(Application).filter(Application.status == "in_process").all()]
        in_proc_app_ids = [a.id for a in db.query(Application).filter(Application.status == "in_process").all()]
        db.query(Artifact).filter(~Artifact.application_id.in_(in_proc_app_ids)).delete(synchronize_session=False)
        db.query(Score).filter(~Score.job_id.in_(in_proc_job_ids)).delete(synchronize_session=False)
        db.query(Event).filter(~Event.application_id.in_(in_proc_app_ids)).delete(synchronize_session=False)
        db.query(Application).filter(~Application.job_id.in_(in_proc_job_ids)).delete(synchronize_session=False)
        db.query(Job).filter(~Job.id.in_(in_proc_job_ids)).delete(synchronize_session=False)
        db.commit()

    agent = SourcingAgent()
    agent.online = False

    # First run
    stats1 = agent.run(dry_run=False)
    assert stats1["total_discovered"] >= 50, f"Expected >= 50 postings, got {stats1['total_discovered']}"
    assert stats1["new_inserted"] >= 50, f"Expected >= 50 new insertions, got {stats1['new_inserted']}"

    # Verify database count
    with SessionLocal() as db:
        open_swe_jobs = db.query(Job).filter(Job.is_open == True, Job.term == "Summer 2027").all()
        assert len(open_swe_jobs) >= 50, f"Expected >= 50 open summer 2027 jobs, found {len(open_swe_jobs)}"

        # Check unique dedup keys
        dedup_keys = [j.dedup_key for j in open_swe_jobs]
        assert len(dedup_keys) == len(set(dedup_keys)), "All dedup keys must be unique"

    # Second run (Idempotency test)
    stats2 = agent.run(dry_run=False)
    assert stats2["new_inserted"] == 0, f"Expected 0 new insertions on second run, got {stats2['new_inserted']}"
    assert stats2["updated"] == stats2["total_discovered"], "All discovered postings in second run should be refreshed as updated"

    # Database count should remain unchanged
    with SessionLocal() as db:
        open_swe_jobs_after = db.query(Job).filter(Job.is_open == True, Job.term == "Summer 2027").all()
        assert len(open_swe_jobs_after) == len(open_swe_jobs), "Database row count must not increase on duplicate run"

def test_in_process_pipeline_preservation():
    """Verify that existing in_process pipeline applications are never overwritten or reset."""
    with SessionLocal() as db:
        optiver_app = db.query(Application).join(Job).filter(Job.company == "Optiver").first()
        js_app = db.query(Application).join(Job).filter(Job.company == "Jane Street").first()

        assert optiver_app.status in ["in_process", "phone"]
        assert js_app.status in ["in_process", "phone"]

    # Run sourcing again
    agent = SourcingAgent()
    agent.online = False
    agent.run(dry_run=False)

    with SessionLocal() as db:
        optiver_app_after = db.query(Application).join(Job).filter(Job.company == "Optiver").first()
        js_app_after = db.query(Application).join(Job).filter(Job.company == "Jane Street").first()

        assert optiver_app_after.status == "in_process"
        assert js_app_after.status == "in_process"

def test_manual_import_flow():
    """Verify import_manual_posting dedup and creation."""
    agent = SourcingAgent()
    url = "https://www.linkedin.com/jobs/view/test-unique-posting-999"

    res1 = agent.import_manual_posting(
        url=url,
        company="Stripe",
        title="Software Engineering Intern - Payments - Summer 2027",
        location="Seattle, WA"
    )
    assert res1["status"] in ["created", "exists"]

    # Second import of same URL
    res2 = agent.import_manual_posting(
        url=url,
        company="Stripe",
        title="Software Engineering Intern - Payments - Summer 2027",
        location="Seattle, WA"
    )
    assert res2["status"] == "exists"
    assert res2["job_id"] == res1["job_id"]

@pytest.mark.anyio
async def test_mcp_tools():
    """Verify in-process MCP tools db_upsert_job and db_query."""
    job_payload = {
        "company": "Figma",
        "title": "Software Engineer Intern - Core Systems - Summer 2027",
        "location_raw": "San Francisco, CA",
        "location_norm": "SF Bay Area",
        "url": "https://boards.greenhouse.io/figma/jobs/999111",
        "apply_url": "https://boards.greenhouse.io/figma/jobs/999111",
        "ats_type": "greenhouse",
        "industry": "big_tech_ai",
        "source": "test_mcp",
        "term": "Summer 2027"
    }

    # Upsert via handler
    result = await db_upsert_job.handler({"job_data": job_payload})
    content = json.loads(result["content"][0]["text"])
    assert content["action"] in ["inserted", "updated"]

    # Query via handler
    q_result = await db_query.handler({"table": "jobs", "filter_by": {"company": "Figma"}})
    q_content = json.loads(q_result["content"][0]["text"])
    assert len(q_content) >= 1
    assert q_content[0]["company"] == "Figma"
