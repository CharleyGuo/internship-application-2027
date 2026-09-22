import pytest
from pathlib import Path
from db.models import Job, Score
from agents.rank import FilterAndRankAgent

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Hand-labeled dataset of 30 postings with ground-truth eligibility
HAND_LABELED_POSTINGS = [
    # 15 Eligible Roles
    {
        "company": "Hudson River Trading",
        "title": "Software Engineer Intern - Systems & Core Tech - Summer 2027",
        "location_norm": "New York City",
        "industry": "quant_trading",
        "description_md": "Low latency C++ development, distributed systems, Linux kernel optimization.",
        "expected_eligible": True
    },
    {
        "company": "Jane Street",
        "title": "Software Engineer Intern - Summer 2027",
        "location_norm": "New York City",
        "industry": "quant_trading",
        "description_md": "OCaml, C++, systems engineering, compilers, high performance compute.",
        "expected_eligible": True
    },
    {
        "company": "Citadel & Citadel Securities",
        "title": "Software Engineer Intern - Summer 2027",
        "location_norm": "Chicago",
        "industry": "quant_trading",
        "description_md": "Modern C++20, high-throughput market data, distributed data infrastructure.",
        "expected_eligible": True
    },
    {
        "company": "Anthropic",
        "title": "Software Engineer Intern - Infrastructure & Platforms - Summer 2027",
        "location_norm": "SF Bay Area",
        "industry": "big_tech_ai",
        "description_md": "Python, distributed training clusters, Kubernetes, GPU orchestration.",
        "expected_eligible": True
    },
    {
        "company": "OpenAI",
        "title": "Software Engineering Intern - Systems & Infra - Summer 2027",
        "location_norm": "SF Bay Area",
        "industry": "big_tech_ai",
        "description_md": "Large-scale inference systems, Python, PyTorch, CUDA, distributed consensus.",
        "expected_eligible": True
    },
    {
        "company": "Google / DeepMind",
        "title": "Software Engineering Intern - Summer 2027",
        "location_norm": "New York City",
        "industry": "big_tech_ai",
        "description_md": "C++, Python, algorithms, distributed systems, machine learning pipelines.",
        "expected_eligible": True
    },
    {
        "company": "Meta",
        "title": "Software Engineer Intern - Undergraduate - Summer 2027",
        "location_norm": "SF Bay Area",
        "industry": "big_tech_ai",
        "description_md": "Full stack and systems engineering, Python, C++, React, Hack, distributed storage.",
        "expected_eligible": True
    },
    {
        "company": "Databricks",
        "title": "Software Engineer Intern - Distributed Systems - Summer 2027",
        "location_norm": "SF Bay Area",
        "industry": "big_tech_ai",
        "description_md": "Apache Spark, Raft consensus, database engine optimization, C++, Python.",
        "expected_eligible": True
    },
    {
        "company": "Stripe",
        "title": "Software Engineering Intern - Summer 2027",
        "location_norm": "New York City",
        "industry": "fintech_banks",
        "description_md": "Payments infrastructure, high availability API services, Ruby, Java, Go.",
        "expected_eligible": True
    },
    {
        "company": "Two Sigma",
        "title": "Software Engineering Intern - Summer 2027",
        "location_norm": "New York City",
        "industry": "quant_trading",
        "description_md": "Distributed computing, algorithmic trading systems, Java, C++, Python.",
        "expected_eligible": True
    },
    {
        "company": "Jump Trading",
        "title": "Software Engineer Intern - Systems Infrastructure - Summer 2027",
        "location_norm": "Chicago",
        "industry": "quant_trading",
        "description_md": "Low latency networking, C++20, kernel bypass, FPGA hardware acceleration interface.",
        "expected_eligible": True
    },
    {
        "company": "Ramp",
        "title": "Software Engineer Intern - Backend Systems - Summer 2027",
        "location_norm": "New York City",
        "industry": "fintech_banks",
        "description_md": "FastAPI, PostgreSQL, AsyncIO, distributed transaction processing, Python.",
        "expected_eligible": True
    },
    {
        "company": "Snowflake",
        "title": "Software Engineering Intern - Core Database Engine - Summer 2027",
        "location_norm": "Seattle",
        "industry": "big_tech_ai",
        "description_md": "Cloud data warehousing, query compilation, C++, distributed storage systems.",
        "expected_eligible": True
    },
    {
        "company": "DRW",
        "title": "Software Engineering Intern - Campus 2027",
        "location_norm": "Chicago",
        "industry": "quant_trading",
        "description_md": "Modern C++, microservices, event-driven trading platforms, Linux.",
        "expected_eligible": True
    },
    {
        "company": "Radix Trading",
        "title": "Quantitative Development Intern - Summer 2027",
        "location_norm": "Chicago",
        "industry": "quant_trading",
        "description_md": "Research and software engineering, Python, C++, numerical algorithms.",
        "expected_eligible": True
    },

    # 15 Ineligible Roles (Testing Hard Filters)
    {
        "company": "Lockheed Martin",
        "title": "Software Engineer Intern - Summer 2027",
        "location_norm": "Orlando, FL",
        "industry": "other",
        "description_md": "Must possess active security clearance (Secret clearance required prior to start).",
        "expected_eligible": False
    },
    {
        "company": "Raytheon",
        "title": "Embedded Software Intern - Summer 2027",
        "location_norm": "Tucson, AZ",
        "industry": "other",
        "description_md": "Active DoD clearance required. Must have Top Secret / SCI polygraph.",
        "expected_eligible": False
    },
    {
        "company": "Google",
        "title": "Associate Product Manager Intern - Summer 2027",
        "location_norm": "SF Bay Area",
        "industry": "big_tech_ai",
        "description_md": "Define product roadmap, user research, feature specs, business analytics.",
        "expected_eligible": False
    },
    {
        "company": "Meta",
        "title": "Technical Program Manager Intern - Summer 2027",
        "location_norm": "Menlo Park",
        "industry": "big_tech_ai",
        "description_md": "Cross-functional schedule tracking, sprint planning, project milestones.",
        "expected_eligible": False
    },
    {
        "company": "Citadel",
        "title": "Data Analyst Intern - Operations - Summer 2027",
        "location_norm": "Chicago",
        "industry": "quant_trading",
        "description_md": "Excel modeling, operational reporting, KPI dashboards, business analysis.",
        "expected_eligible": False
    },
    {
        "company": "Goldman Sachs",
        "title": "Financial Analyst Intern - Global Banking - Summer 2027",
        "location_norm": "New York City",
        "industry": "fintech_banks",
        "description_md": "M&A valuation, DCF modeling, pitch decks, financial statement analysis.",
        "expected_eligible": False
    },
    {
        "company": "Acme Corp",
        "title": "IT Helpdesk & Desktop Support Intern - Summer 2027",
        "location_norm": "Dallas, TX",
        "industry": "other",
        "description_md": "Provision laptops, configure printers, Active Directory user administration.",
        "expected_eligible": False
    },
    {
        "company": "NVIDIA",
        "title": "Hardware Engineer Intern - ASIC Design & Verification",
        "location_norm": "SF Bay Area",
        "industry": "big_tech_ai",
        "description_md": "Verilog/SystemVerilog, UVM testbenches, RTL synthesis, physical timing closure.",
        "expected_eligible": False
    },
    {
        "company": "Apple",
        "title": "PCB Layout & Mechanical Engineer Intern",
        "location_norm": "SF Bay Area",
        "industry": "big_tech_ai",
        "description_md": "Altium Designer, thermal dissipation modeling, CNC prototyping.",
        "expected_eligible": False
    },
    {
        "company": "Bloomberg",
        "title": "Software Engineering Intern - Summer 2027",
        "location_norm": "New York City",
        "industry": "fintech_banks",
        "description_md": "Must graduate by December 2026. This role is strictly for December 2026 graduates.",
        "expected_eligible": False
    },
    {
        "company": "Amazon",
        "title": "Software Development Engineer Intern",
        "location_norm": "Seattle",
        "industry": "big_tech_ai",
        "description_md": "Class of 2026 only. Applications from junior/sophomore students will not be considered.",
        "expected_eligible": False
    },
    {
        "company": "Microsoft",
        "title": "Software Engineer Intern",
        "location_norm": "Redmond",
        "industry": "big_tech_ai",
        "description_md": "Must be graduating in Fall 2026 to qualify for our accelerated full-time conversion program.",
        "expected_eligible": False
    },
    {
        "company": "Walmart",
        "title": "Retail Store Sales Associate - Summer",
        "location_norm": "Nashville, TN",
        "industry": "other",
        "description_md": "Stock shelves, assist customers at cash registers, maintain store appearance.",
        "expected_eligible": False
    },
    {
        "company": "McKinsey",
        "title": "Marketing & Business Strategy Intern - Summer 2027",
        "location_norm": "New York City",
        "industry": "other",
        "description_md": "Market research, customer segmentation surveys, brand consulting.",
        "expected_eligible": False
    },
    {
        "company": "Northrop Grumman",
        "title": "Systems Engineer Intern - Summer 2027",
        "location_norm": "Redondo Beach, CA",
        "industry": "other",
        "description_md": "Requires active security clearance before assignment.",
        "expected_eligible": False
    }
]

def test_hard_filters_precision_acceptance():
    """Phase 2 Acceptance Test:
    Verify precision >= 0.90 on hand-labeled set of 30 postings.
    Verify rank rationale is present for all 30 postings.
    """
    agent = FilterAndRankAgent()

    true_positives = 0
    false_positives = 0
    true_negatives = 0
    false_negatives = 0

    assert len(HAND_LABELED_POSTINGS) == 30, f"Expected exactly 30 postings, got {len(HAND_LABELED_POSTINGS)}"

    for idx, item in enumerate(HAND_LABELED_POSTINGS):
        job = Job(
            id=f"test_job_{idx}",
            company=item["company"],
            title=item["title"],
            location_raw=item["location_norm"],
            location_norm=item["location_norm"],
            industry=item["industry"],
            description_md=item["description_md"],
            term="Summer 2027",
            is_open=True
        )

        score = agent.compute_score(job)

        # Check rationale requirement: MUST be present for ALL postings
        assert score.rationale is not None and len(score.rationale) > 10, (
            f"Rationale missing or too short for job {job.company} - {job.title}"
        )

        predicted_eligible = score.eligible
        expected_eligible = item["expected_eligible"]

        if expected_eligible and predicted_eligible:
            true_positives += 1
        elif not expected_eligible and predicted_eligible:
            false_positives += 1
            print(f"FALSE POSITIVE: {job.company} - {job.title} marked eligible! Note: {score.eligibility_note}")
        elif not expected_eligible and not predicted_eligible:
            true_negatives += 1
            # Ineligible jobs must have a clear explanatory eligibility note quoting the reason
            assert score.eligibility_note is not None and len(score.eligibility_note) > 0, (
                f"Ineligible job {job.company} missing eligibility_note"
            )
        elif expected_eligible and not predicted_eligible:
            false_negatives += 1
            print(f"FALSE NEGATIVE: {job.company} - {job.title} marked ineligible: {score.eligibility_note}")

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0.0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0.0
    accuracy = (true_positives + true_negatives) / len(HAND_LABELED_POSTINGS)

    print(f"\n--- Hard Filter Metrics on 30 Hand-Labeled Postings ---")
    print(f"True Positives: {true_positives}/15 | True Negatives: {true_negatives}/15")
    print(f"False Positives: {false_positives} | False Negatives: {false_negatives}")
    print(f"Precision: {precision:.3f} (Required: >= 0.90)")
    print(f"Recall: {recall:.3f}")
    print(f"Accuracy: {accuracy:.3f}")

    assert precision >= 0.90, f"Hard filter precision {precision:.3f} failed acceptance threshold of 0.90"
    assert accuracy >= 0.90, f"Hard filter accuracy {accuracy:.3f} failed threshold of 0.90"

def test_scoring_formula_verification():
    """Verify exact formula weights:
    score = 0.35 * industry + 0.25 * location + 0.20 * skills + 0.10 * deadline + 0.10 * prestige
    """
    agent = FilterAndRankAgent()
    job = Job(
        id="formula_test_job",
        company="Hudson River Trading",
        title="Software Engineer Intern - Systems & Core Tech - Summer 2027",
        location_raw="New York, NY",
        location_norm="New York City",
        industry="quant_trading",
        description_md="Modern C++, distributed systems, Linux, Raft, low latency, Python.",
        term="Summer 2027",
        is_open=True
    )

    score = agent.compute_score(job)
    assert score.eligible is True

    expected_total = (
        0.35 * score.industry_fit +
        0.25 * score.location_fit +
        0.20 * score.skills_overlap +
        0.10 * score.deadline_urgency +
        0.10 * score.prestige_prior
    )
    assert abs(score.total - expected_total) < 1e-3, (
        f"Computed score {score.total} differs from expected {expected_total}"
    )

def test_adjacent_role_handling():
    """Verify that pure Quant Research or Trader Intern roles are tagged adjacent and retained."""
    agent = FilterAndRankAgent()
    qr_job = Job(
        id="qr_test_job",
        company="Citadel",
        title="Quantitative Research Intern - Summer 2027",
        location_raw="New York, NY",
        location_norm="New York City",
        industry="quant_trading",
        description_md="Statistical modeling, probability, machine learning, Python.",
        term="Summer 2027",
        is_open=True
    )

    score = agent.compute_score(qr_job)
    assert score.eligible is True, "Adjacent QR role should remain eligible"
    assert "adjacent" in score.rationale.lower(), "Rationale must record adjacent role tag"
