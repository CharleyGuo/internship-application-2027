import os
import json
import hashlib
from pathlib import Path
import pytest

from db.session import init_db, SessionLocal
from db.models import Job, Application, Artifact, Event
from agents.tailor import TailoringAgent, FactsBank, compute_sha256

PROJECT_ROOT = Path(__file__).resolve().parent.parent

@pytest.fixture(autouse=True)
def setup_db():
    init_db()

def test_facts_bank_indexing():
    """Verify FactsBank loads and indexes all atomic facts with valid IDs."""
    facts_p = PROJECT_ROOT / "profile" / "facts.yaml"
    if not facts_p.exists():
        facts_p = PROJECT_ROOT / "profile" / "facts.example.yaml"
    facts = FactsBank(facts_p)
    all_ids = facts.all_ids()

    assert any(i.startswith("cand.") for i in all_ids), "Facts bank must have a candidate ID"
    assert any(i.startswith("edu.") for i in all_ids), "Facts bank must have an education ID"
    assert any(i.startswith("skill.") for i in all_ids), "Facts bank must have skill IDs"
    assert any(i.startswith("proj.") for i in all_ids), "Facts bank must have project IDs"
    assert any(i.startswith("course.") for i in all_ids), "Facts bank must have course IDs"

def test_phase3_acceptance_5_sample_jobs_citation_check():
    """Phase 3 Acceptance Test:
    For 5 sample jobs, answers.json passes the facts-bank citation check
    with zero [NEEDS INPUT] for standard questions.
    """
    agent = TailoringAgent()

    sample_companies = [
        ("Hudson River Trading", "quant_trading", "Software Engineer Intern - Systems & Core Tech - Summer 2027"),
        ("Anthropic", "big_tech_ai", "Software Engineer Intern - Infrastructure & Platforms - Summer 2027"),
        ("Databricks", "big_tech_ai", "Software Engineer Intern - Distributed Systems - Summer 2027"),
        ("Stripe", "fintech_banks", "Software Engineering Intern - Summer 2027"),
        ("Ramp", "fintech_banks", "Software Engineer Intern - Backend Systems - Summer 2027")
    ]

    with SessionLocal() as db:
        for idx, (company, industry, title) in enumerate(sample_companies):
            # Create / retrieve sample job
            job = db.query(Job).filter(Job.company == company).first()
            if not job:
                job = Job(
                    id=f"job_sample_{idx}",
                    dedup_key=f"dedup_sample_{idx}",
                    company=company,
                    title=title,
                    location_raw="New York, NY",
                    location_norm="New York City",
                    industry=industry,
                    ats_type="greenhouse" if company != "Ramp" else "ashby",
                    url=f"https://example.com/apply/{company}",
                    apply_url=f"https://example.com/apply/{company}",
                    term="Summer 2027",
                    description_md=f"{title} at {company}",
                    source="sample_test",
                    is_open=True
                )
                db.add(job)
                db.commit()

            bundle = agent.tailor_job(job.id)

            # 1. Verify bundle files exist on disk
            dest_dir = Path(bundle["artifacts_dir"])
            assert (dest_dir / "resume.pdf").exists()
            assert (dest_dir / "resume.md").exists()
            assert (dest_dir / "cover_letter.md").exists()
            assert (dest_dir / "answers.json").exists()
            assert (dest_dir / "flags.md").exists()

            # 2. Verify Cover Letter length (<= 250 words)
            with open(dest_dir / "cover_letter.md", "r", encoding="utf-8") as f:
                cl_content = f.read()
            cl_words = len(cl_content.split())
            assert cl_words <= 250, f"Cover letter for {company} has {cl_words} words, exceeds 250!"
            cand_name = agent.profile.get("personal", {}).get("full_name", "Candidate")
            school_name = agent.profile.get("education", {}).get("institution", "University")
            assert cand_name in cl_content
            assert school_name in cl_content

            # 3. Verify answers.json structure and facts-bank citations
            with open(dest_dir / "answers.json", "r", encoding="utf-8") as f:
                answers_data = json.load(f)

            answers = answers_data["answers"]
            assert len(answers) >= 5, f"Expected at least 5 standard answers for {company}, got {len(answers)}"

            for ans in answers:
                # Acceptance requirement: ZERO [NEEDS INPUT] for standard questions
                assert not ans["needs_input"], (
                    f"Unexpected [NEEDS INPUT] for standard question at {company}: {ans['question']}"
                )
                assert not ans["answer"].startswith("[NEEDS INPUT"), (
                    f"Answer contains [NEEDS INPUT] at {company}: {ans['answer']}"
                )
                assert len(ans["answer"]) <= 1500, "Answer exceeds character limit"

                # Citation check: ALL cited IDs must exist in facts.yaml
                assert len(ans["citations"]) > 0, f"Answer missing citation IDs: {ans['question']}"
                for cid in ans["citations"]:
                    assert agent.facts.has_fact(cid), (
                        f"Fabrication check failed! Cited fact ID '{cid}' does not exist in facts.yaml"
                    )

            # 4. Verify Database Persistence of Artifacts
            artifacts = db.query(Artifact).filter(Artifact.application_id == job.application.id).all()
            assert len(artifacts) >= 4, f"Expected at least 4 registered artifacts for {company}"
            for art in artifacts:
                assert Path(art.path).exists()
                # Verify SHA256 matches actual file on disk
                assert art.sha256 == compute_sha256(Path(art.path))

            # 5. Verify Application status updated to tailored
            app_rec = db.query(Application).filter_by(job_id=job.id).first()
            assert app_rec.status == "tailored"
            assert app_rec.resume_variant in ["quant", "systems", "general"]

def test_zero_fabrication_enforcer():
    """Verify that questions requiring facts outside facts.yaml yield [NEEDS INPUT: ...]."""
    agent = TailoringAgent()
    job = Job(
        id="job_fab_test",
        company="Aviation Tech",
        title="Flight Controls Intern",
        location_raw="Seattle, WA",
        location_norm="Seattle",
        industry="big_tech_ai",
        is_open=True
    )

    unsupported_q = "Describe your 10 years of commercial Boeing 777 piloting experience."
    res = agent.answer_question(unsupported_q, job)

    assert res["needs_input"] is True
    assert "[NEEDS INPUT:" in res["answer"]
    assert len(res["citations"]) == 0
