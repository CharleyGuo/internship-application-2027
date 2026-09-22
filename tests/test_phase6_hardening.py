import os
import json
import pytest
import datetime
from pathlib import Path
from typer.testing import CliRunner

from db.session import init_db, SessionLocal
from db.models import Job, Application, Event
from tools.cost_tracker import CostTracker, DailyBudgetCapExceeded
from tools.resilience import retry_with_backoff, resilient_interact
from tools.company_verifier import CompanyVerifier
from agents.tailor import TailoringAgent
from cli import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

@pytest.fixture(autouse=True)
def setup_db():
    init_db()

def test_cost_tracker_calculation_and_recording():
    """Verify CostTracker correctly computes token costs and records audit events."""
    tracker = CostTracker()
    cost = tracker.calculate_cost(tokens_in=1000, tokens_out=500, model="claude-3-5-sonnet")
    # 1000 / 1M * 3.00 + 500 / 1M * 15.00 = 0.003 + 0.0075 = 0.0105
    assert cost == 0.0105

    # Record usage
    res = tracker.record_usage(
        tokens_in=1000,
        tokens_out=500,
        model="claude-3-5-sonnet",
        agent_name="test_agent",
        task_desc="Hardening unit test"
    )
    assert res["cost_usd"] == 0.0105
    assert res["daily_spend_today"] >= 0.0105

    # Verify event logged in DB
    with SessionLocal() as db:
        evt = (
            db.query(Event)
            .filter_by(type="llm_usage")
            .order_by(Event.ts.desc())
            .first()
        )
        assert evt is not None
        payload = json.loads(evt.payload_json)
        assert payload["cost_usd"] == 0.0105
        assert payload["tokens_in"] == 1000

def test_daily_budget_cap_enforcement():
    """Verify that operations halt with DailyBudgetCapExceeded when daily spend reaches the cap."""
    tracker = CostTracker()
    original_cap = tracker.daily_cap

    try:
        # Set artificial low cap
        tracker.daily_cap = 0.005

        # Check budget or raise should raise DailyBudgetCapExceeded if projected cost exceeds 0.005
        with pytest.raises(DailyBudgetCapExceeded) as exc_info:
            tracker.check_budget_or_raise(additional_cost=0.01)

        assert "BUDGET CAP HALTED" in str(exc_info.value)
        assert "exceeds the configured daily budget cap" in str(exc_info.value)
    finally:
        tracker.daily_cap = original_cap

def test_tailoring_agent_halts_when_budget_exceeded():
    """Verify that TailoringAgent refuses to generate materials when budget cap is breached."""
    agent = TailoringAgent()
    original_cap = agent.cost_tracker.daily_cap

    try:
        # Force cap to $0.0001
        agent.cost_tracker.daily_cap = 0.00001

        # Record high usage to exceed cap
        with pytest.raises(DailyBudgetCapExceeded):
            agent.cost_tracker.record_usage(tokens_in=100_000, tokens_out=50_000)

        # Attempt to tailor a job
        with pytest.raises(DailyBudgetCapExceeded):
            agent.tailor_job("job_sample_0")
    finally:
        agent.cost_tracker.daily_cap = original_cap

def test_retry_with_backoff_transient_recovery():
    """Verify that retry_with_backoff recovers from transient failures and succeeds."""
    attempts = 0

    @retry_with_backoff(max_retries=3, initial_delay=0.01, backoff_factor=1.5, exceptions=(ValueError,))
    def flaky_function():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError(f"Transient error on attempt {attempts}")
        return "success"

    result = flaky_function()
    assert result == "success"
    assert attempts == 3

def test_retry_with_backoff_exhaustion():
    """Verify that retry_with_backoff re-raises after exhausting max retries."""
    attempts = 0

    @retry_with_backoff(max_retries=3, initial_delay=0.01, backoff_factor=1.5, exceptions=(RuntimeError,))
    def always_failing():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Persistent network fault")

    with pytest.raises(RuntimeError) as exc_info:
        always_failing()

    assert "Persistent network fault" in str(exc_info.value)
    assert attempts == 3

def test_async_retry_with_backoff():
    """Verify async support in retry_with_backoff."""
    import asyncio
    call_count = 0

    @retry_with_backoff(max_retries=3, initial_delay=0.01, backoff_factor=1.5, exceptions=(ConnectionResetError,))
    async def flaky_async_call():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionResetError("Connection dropped by peer")
        return {"status": "ok"}

    res = asyncio.run(flaky_async_call())
    assert res == {"status": "ok"}
    assert call_count == 2

def test_company_verifier_healthy():
    """Verify CompanyVerifier validates all target companies from config/companies.yaml."""
    verifier = CompanyVerifier()
    res = verifier.verify_companies()

    assert res["is_healthy"] is True
    assert res["total_companies"] >= 50
    assert len(res["errors"]) == 0
    assert res["valid_companies"] == res["total_companies"]

def test_company_verifier_detects_corrupted_config(tmp_path):
    """Verify CompanyVerifier flags corrupted or incomplete company specifications."""
    corrupted_yaml = tmp_path / "corrupted_companies.yaml"
    corrupted_yaml.write_text("""
companies:
  - name: "Valid Greenhouse"
    ats: "greenhouse"
    token: "valid_token"
  - name: "Invalid Custom Missing Reason"
    ats: "custom"
  - name: "Unknown ATS Firm"
    ats: "unknown_board"
  - name: "Invalid Token Chars"
    ats: "lever"
    token: "token with spaces & illegal # chars"
""")

    verifier = CompanyVerifier(config_path=corrupted_yaml)
    res = verifier.verify_companies()

    assert res["is_healthy"] is False
    assert len(res["errors"]) >= 3
    error_text = " ".join(res["errors"])
    assert "Custom ATS requires documented 'reason'" in error_text
    assert "Unknown ATS type" in error_text
    assert "Invalid token format" in error_text

def test_cli_budget_and_verify_commands():
    """Verify ia budget and ia verify CLI commands."""
    res_budget = runner.invoke(app, ["budget"])
    assert res_budget.exit_code == 0
    assert "Daily LLM Budget Health" in res_budget.output
    assert "Daily Budget Cap: $200.00" in res_budget.output

    res_verify = runner.invoke(app, ["verify"])
    assert res_verify.exit_code == 0
    assert "All 51 target companies verified successfully" in res_verify.output
