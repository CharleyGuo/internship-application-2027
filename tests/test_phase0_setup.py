import os
import subprocess
import yaml
from pathlib import Path
import pytest
from typer.testing import CliRunner

from cli import app
from db.session import init_db, SessionLocal, compute_dedup_key
from db.models import Job, Application, Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

def test_companies_yaml_integrity():
    """Verify config/companies.yaml contains all Section 1 companies with valid ATS tokens or custom reasons."""
    companies_file = PROJECT_ROOT / "config" / "companies.yaml"
    assert companies_file.exists(), "config/companies.yaml must exist"

    with open(companies_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    companies = data.get("companies", [])
    assert len(companies) >= 50, f"Expected at least 50 companies, found {len(companies)}"

    # Required companies from Section 1
    expected_quant = [
        "Optiver", "Jane Street", "Hudson River Trading", "Citadel & Citadel Securities",
        "Two Sigma", "IMC", "DRW", "SIG", "Jump Trading", "Akuna Capital", "Five Rings",
        "Tower Research", "Virtu Financial", "Radix Trading", "Old Mission",
        "Belvedere Trading", "Flow Traders", "XTX Markets", "Millennium", "Point72 / Cubist",
        "D.E. Shaw"
    ]
    expected_big_tech = [
        "Google / DeepMind", "Meta", "Apple", "Microsoft", "Amazon / AWS", "NVIDIA",
        "OpenAI", "Anthropic", "Databricks", "Scale AI", "xAI", "Cohere", "Mistral",
        "Perplexity", "Snowflake", "Palantir"
    ]
    expected_fintech = [
        "Stripe", "Robinhood", "Coinbase", "Plaid", "Ramp", "Brex", "Affirm",
        "Bloomberg", "Goldman Sachs Engineering", "JPMorgan SEP", "Morgan Stanley Technology",
        "Capital One TIP", "Squarepoint", "Bridgewater"
    ]

    names_in_config = {c["name"] for c in companies}

    for name in expected_quant + expected_big_tech + expected_fintech:
        assert name in names_in_config, f"Missing Section 1 company in companies.yaml: {name}"

    for c in companies:
        name = c.get("name")
        ats = c.get("ats")
        assert ats in ["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "custom", "icims"], (
            f"Invalid ATS '{ats}' for company {name}"
        )
        if ats == "custom":
            assert c.get("reason"), f"Company {name} with custom ATS must have a documented reason"
        else:
            assert c.get("token") or c.get("tenant"), f"Company {name} with ATS {ats} must have a token or tenant"
        assert c.get("careers_url"), f"Company {name} must have a careers_url"
        assert c.get("industry") in ["quant_trading", "big_tech_ai", "fintech_banks"], (
            f"Company {name} must have a recognized industry category"
        )

def test_database_initialization_and_seeding():
    """Verify SQLite database initializes and seeds Optiver & Jane Street in_process applications."""
    init_db()

    with SessionLocal() as db:
        # Check Optiver
        optiver_app = (
            db.query(Application)
            .join(Job)
            .filter(Job.company == "Optiver")
            .first()
        )
        assert optiver_app is not None, "Optiver application should be seeded"
        assert optiver_app.status in ["in_process", "phone"], f"Expected Optiver status in_process or phone, got {optiver_app.status}"
        assert "phone" in optiver_app.notes.lower()

        # Check Jane Street
        js_app = (
            db.query(Application)
            .join(Job)
            .filter(Job.company == "Jane Street")
            .first()
        )
        assert js_app is not None, "Jane Street application should be seeded"
        assert js_app.status == "in_process", f"Expected Jane Street status in_process, got {js_app.status}"
        assert "prep underway" in js_app.notes.lower()

        # Check audit events
        events = db.query(Event).filter(Event.application_id.in_([optiver_app.id, js_app.id])).all()
        assert len(events) >= 2, "Seeded applications must have audit events"

def test_profile_and_facts_bank():
    """Verify profile and facts bank templates exist and match candidate parameters."""
    profile_file = PROJECT_ROOT / "profile" / "profile.yaml"
    if not profile_file.exists():
        profile_file = PROJECT_ROOT / "profile" / "profile.example.yaml"

    facts_file = PROJECT_ROOT / "profile" / "facts.yaml"
    if not facts_file.exists():
        facts_file = PROJECT_ROOT / "profile" / "facts.example.yaml"

    assert profile_file.exists(), "profile.yaml or profile.example.yaml must exist"
    assert facts_file.exists(), "facts.yaml or facts.example.yaml must exist"

    with open(profile_file, "r") as f:
        prof = yaml.safe_load(f)

    assert "full_name" in prof["personal"] and len(prof["personal"]["full_name"]) > 0
    assert "institution" in prof["education"] and len(prof["education"]["institution"]) > 0
    assert "gpa" in prof["education"]
    assert "class_year" in prof["education"]
    assert prof["work_authorization"]["us_citizen"] is True
    assert prof["work_authorization"]["requires_sponsorship"] is False

    with open(facts_file, "r") as f:
        facts = yaml.safe_load(f)

    assert len(facts.get("coursework", [])) >= 4
    assert len(facts.get("projects", [])) >= 3
    assert len(facts.get("experience", [])) >= 1

    # Check resume variants (or example variants)
    for variant in ["systems", "quant", "general"]:
        variant_path = PROJECT_ROOT / "profile" / "variants" / f"{variant}.md"
        example_path = PROJECT_ROOT / "profile" / "variants" / f"{variant}.example.md"
        assert variant_path.exists() or example_path.exists(), f"Resume variant {variant} must exist"

def test_cli_status_command():
    """Acceptance test: ia status runs successfully."""
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, f"ia status failed with exit code {result.exit_code}: {result.output}"
    assert "Candidate Profile" in result.output
    assert "Target Companies ATS Coverage" in result.output
    assert "100% of target companies have verified ATS tokens" in result.output
    assert "Optiver" in result.output
    assert "Jane Street" in result.output
