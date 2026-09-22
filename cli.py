from __future__ import annotations
import os
import sys
import shutil
import datetime
from pathlib import Path
from typing import Optional, List
import yaml
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.session import init_db, SessionLocal
from db.models import Job, Application, Event, Score

app = typer.Typer(
    name="ia",
    help="Semi-automated Internship Application Assistant for Summer 2027 SWE Intern roles.",
    no_args_is_help=True,
)
console = Console()

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

@app.command(name="init")
def init_cmd(
    force: bool = typer.Option(False, "--force", "-f", help="Force overwrite of existing profile and facts files"),
    non_interactive: bool = typer.Option(False, "--non-interactive", "-y", help="Skip interactive prompts and use example templates")
):
    """Initialize candidate profile, facts bank, resume variants, and database."""
    console.print(Panel.fit(
        "[bold cyan]Internship Application Assistant – Onboarding Setup Wizard[/bold cyan]\n"
        "Configure candidate profile, verifiable facts bank, resume variants, and local database.",
        title="Setup",
        border_style="cyan"
    ))

    profile_path = PROJECT_ROOT / "profile" / "profile.yaml"
    profile_example = PROJECT_ROOT / "profile" / "profile.example.yaml"
    facts_path = PROJECT_ROOT / "profile" / "facts.yaml"
    facts_example = PROJECT_ROOT / "profile" / "facts.example.yaml"
    variants_dir = PROJECT_ROOT / "profile" / "variants"
    env_path = PROJECT_ROOT / ".env"
    env_example = PROJECT_ROOT / ".env.example"

    # 1. Profile Setup
    if profile_path.exists() and not force:
        console.print(f"[bold green]✓[/bold green] Candidate profile already exists: [cyan]{profile_path}[/cyan]")
    else:
        if non_interactive or not sys.stdin.isatty():
            if profile_example.exists():
                shutil.copyfile(profile_example, profile_path)
                console.print(f"[bold green]✓[/bold green] Initialized profile from template: [cyan]{profile_path}[/cyan]")
        else:
            console.print("\n[bold yellow]Step 1: Configure Candidate Profile[/bold yellow]")
            full_name = typer.prompt("Full Name", default="Jane Doe")
            parts = full_name.split()
            first_name = parts[0] if parts else "Jane"
            last_name = parts[-1] if len(parts) > 1 else "Doe"
            email = typer.prompt("Email", default=f"{first_name.lower()}.{last_name.lower()}@university.edu")
            phone = typer.prompt("Phone number", default="(555) 123-4567")
            institution = typer.prompt("University / Institution", default="State University")
            major = typer.prompt("Major", default="Computer Science")
            grad_expected = typer.prompt("Expected Graduation (e.g. May 2028)", default="May 2028")
            gpa = typer.prompt("GPA", default="3.85")
            github = typer.prompt("GitHub Profile URL", default=f"https://github.com/{first_name.lower()}{last_name.lower()}")
            linkedin = typer.prompt("LinkedIn Profile URL", default=f"https://linkedin.com/in/{first_name.lower()}{last_name.lower()}")

            profile_data = {
                "personal": {
                    "first_name": first_name,
                    "last_name": last_name,
                    "full_name": full_name,
                    "email": email,
                    "phone": phone,
                    "address": {
                        "street": "123 University Ave",
                        "city": "San Francisco",
                        "state": "CA",
                        "postal_code": "94107",
                        "country": "United States"
                    }
                },
                "education": {
                    "institution": institution,
                    "degree": "Bachelor of Science",
                    "major": major,
                    "minor": "Mathematics",
                    "gpa": gpa,
                    "start_date": "August 2024",
                    "graduation_expected": grad_expected,
                    "graduation_term": grad_expected,
                    "class_year": f"Class of {grad_expected[-4:]}" if len(grad_expected) >= 4 else "Class of 2028"
                },
                "work_authorization": {
                    "us_citizen": True,
                    "authorized_to_work_in_us": True,
                    "requires_sponsorship": False,
                    "future_sponsorship_needed": False,
                    "visa_type": "U.S. Citizen"
                },
                "links": {
                    "github": github,
                    "linkedin": linkedin,
                    "portfolio": ""
                },
                "demographics_opt_out": {
                    "veteran_status": "I am not a protected veteran",
                    "disability_status": "I do not have a disability",
                    "race_ethnicity": "Decline to self-identify",
                    "gender": "Decline to self-identify"
                }
            }
            with open(profile_path, "w", encoding="utf-8") as f:
                yaml.dump(profile_data, f, sort_keys=False, default_flow_style=False)
            console.print(f"[bold green]✓[/bold green] Created candidate profile: [cyan]{profile_path}[/cyan]")

    # 2. Facts Bank Setup
    if facts_path.exists() and not force:
        console.print(f"[bold green]✓[/bold green] Facts bank already exists: [cyan]{facts_path}[/cyan]")
    else:
        if facts_example.exists():
            shutil.copyfile(facts_example, facts_path)
            console.print(f"[bold green]✓[/bold green] Initialized verifiable facts bank: [cyan]{facts_path}[/cyan]")

    # 3. Resume Markdown Variants Setup
    variants_dir.mkdir(parents=True, exist_ok=True)
    for variant in ["general", "systems", "quant"]:
        var_file = variants_dir / f"{variant}.md"
        var_example = variants_dir / f"{variant}.example.md"
        if not var_file.exists() and var_example.exists():
            shutil.copyfile(var_example, var_file)
            console.print(f"[bold green]✓[/bold green] Created resume variant: [cyan]{var_file.name}[/cyan]")

    # 4. Resume PDF Check
    pdfs = list((PROJECT_ROOT / "profile").glob("*.pdf"))
    if pdfs:
        console.print(f"[bold green]✓[/bold green] Master resume PDF found: [cyan]{pdfs[0].name}[/cyan]")
    else:
        console.print("[bold yellow]ℹ Resume PDF notice:[/bold yellow] Place your master resume PDF into [cyan]profile/[/cyan] (e.g. profile/resume.pdf)")

    # 5. Environment File Setup
    if not env_path.exists() and env_example.exists():
        shutil.copyfile(env_example, env_path)
        console.print(f"[bold green]✓[/bold green] Created environment file: [cyan].env[/cyan]")

    # 6. Database Initialization
    init_db()
    console.print("[bold green]✓[/bold green] Local SQLite database initialized: [cyan]internships.db[/cyan]")

    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        "Your assistant is ready to use. Suggested next steps:\n"
        " • [bold cyan]ia status[/bold cyan]      : View candidate profile & company ATS coverage\n"
        " • [bold cyan]ia source[/bold cyan]      : Discover new Summer 2027 internship postings\n"
        " • [bold cyan]ia rank[/bold cyan]        : Filter & rank postings against your criteria\n"
        " • [bold cyan]ia tailor all[/bold cyan]  : Generate tailored bundles for top opportunities\n"
        " • [bold cyan]ia apply <id>[/bold cyan]  : Pre-fill application form with Playwright",
        title="Ready",
        border_style="green"
    ))

@app.command(name="status")
def status_cmd():
    """Display system status, database statistics, company coverage, and pipeline state."""
    init_db()
    companies_data = load_yaml(PROJECT_ROOT / "config" / "companies.yaml")
    companies = companies_data.get("companies", [])
    profile_data = load_yaml(PROJECT_ROOT / "profile" / "profile.yaml")

    console.print(Panel.fit(
        f"[bold green]Internship Application Assistant (Summer 2027)[/bold green]\n"
        f"Candidate: [cyan]{profile_data.get('personal', {}).get('full_name', 'Not configured (run ia init)')}[/cyan] | "
        f"School: [cyan]{profile_data.get('education', {}).get('institution', 'Not configured')}[/cyan] | "
        f"GPA: [cyan]{profile_data.get('education', {}).get('gpa', 'N/A')}[/cyan] | "
        f"Class: [cyan]{profile_data.get('education', {}).get('class_year', 'N/A')}[/cyan]",
        title="Candidate Profile",
        border_style="green"
    ))

    # Check company ATS coverage
    ats_counts = {}
    missing_reason_or_token = []

    for c in companies:
        ats = c.get("ats", "unknown")
        ats_counts[ats] = ats_counts.get(ats, 0) + 1
        if ats == "custom":
            if not c.get("reason"):
                missing_reason_or_token.append(f"{c.get('name')} (custom missing reason)")
        else:
            if not (c.get("token") or c.get("tenant")):
                missing_reason_or_token.append(f"{c.get('name')} ({ats} missing token)")

    comp_table = Table(title=f"Target Companies ATS Coverage ({len(companies)} total)")
    comp_table.add_column("ATS Type", style="cyan")
    comp_table.add_column("Count", justify="right", style="magenta")
    comp_table.add_column("Status", style="green")

    for ats, count in sorted(ats_counts.items(), key=lambda x: -x[1]):
        comp_table.add_row(ats.capitalize(), str(count), "Verified")

    console.print(comp_table)

    if missing_reason_or_token:
        console.print(f"[bold red]WARNING: Companies missing token or reason: {missing_reason_or_token}[/bold red]")
    else:
        console.print("[bold green]✓ 100% of target companies have verified ATS tokens or documented custom reasons.[/bold green]\n")

    # Check Database Status
    with SessionLocal() as db:
        total_jobs = db.query(Job).count()
        apps = db.query(Application).all()
        status_counts = {}
        for a in apps:
            status_counts[a.status] = status_counts.get(a.status, 0) + 1

        db_table = Table(title="Application Pipeline Status")
        db_table.add_column("Status Stage", style="cyan")
        db_table.add_column("Count", justify="right", style="magenta")

        for stage in [
            "discovered", "qualified", "tailored", "ready_for_review",
            "submitted", "in_process", "oa", "phone", "onsite", "offer", "rejected", "skipped"
        ]:
            count = status_counts.get(stage, 0)
            if count > 0 or stage in ["in_process", "ready_for_review", "submitted"]:
                db_table.add_row(stage, str(count))

        console.print(db_table)

        # Print active in-process and interview pipeline details
        active_apps = [a for a in apps if a.status in ["in_process", "phone", "oa", "onsite", "offer"]]
        if active_apps:
            console.print("\n[bold yellow]Active In-Process & Interview Pipeline:[/bold yellow]")
            for a in active_apps:
                job = a.job
                stage_badge = f"[bold green][{a.status.upper()}][/bold green]"
                console.print(f" • [bold]{job.company}[/bold] – {job.title} ({job.location_norm}) {stage_badge}: {a.notes}")

@app.command(name="source")
def source_cmd(dry_run: bool = typer.Option(False, "--dry-run", help="Simulate run without writing to DB")):
    """Run Sourcing Agent to discover new postings across GitHub lists and ATS APIs."""
    init_db()
    console.print(f"[bold blue]Running Sourcing Agent (dry_run={dry_run})...[/bold blue]")
    from agents.sourcing import SourcingAgent
    agent = SourcingAgent()
    stats = agent.run(dry_run=dry_run)

    console.print(f"[bold green]Sourcing complete![/bold green]")
    console.print(f" • Total discovered: [cyan]{stats['total_discovered']}[/cyan]")
    console.print(f" • New jobs inserted: [green]{stats['new_inserted']}[/green]")
    console.print(f" • Existing jobs refreshed: [yellow]{stats['updated']}[/yellow]")
    if dry_run:
        console.print(" • [italic yellow](Dry run: no changes written to DB)[/italic yellow]")

@app.command(name="rank")
def rank_cmd(
    force: bool = typer.Option(False, "--force", "-f", help="Re-score already scored jobs"),
    limit: int = typer.Option(15, "--limit", "-n", help="Number of ranked jobs to display")
):
    """Run Filter & Rank Agent to score pending jobs."""
    init_db()
    console.print(f"[bold blue]Running Filter & Rank Agent (force={force})...[/bold blue]")
    from agents.rank import FilterAndRankAgent
    ranker = FilterAndRankAgent()
    scores = ranker.rank_all_jobs(force=force)

    with SessionLocal() as db:
        top_jobs = (
            db.query(Job)
            .join(Score)
            .filter(Score.eligible == True)
            .order_by(Score.total.desc())
            .limit(limit)
            .all()
        )

        ineligible_count = db.query(Score).filter(Score.eligible == False).count()
        eligible_count = db.query(Score).filter(Score.eligible == True).count()

        console.print(f"[bold green]Ranking complete![/bold green] Scored: {len(scores)} jobs ({eligible_count} eligible, {ineligible_count} filtered out).\n")

        table = Table(title=f"Top Ranked Qualified Opportunities (Top {min(limit, len(top_jobs))})")
        table.add_column("Rank", justify="right", style="cyan")
        table.add_column("Company", style="bold green")
        table.add_column("Role", style="white")
        table.add_column("Location", style="yellow")
        table.add_column("Score", justify="right", style="magenta")
        table.add_column("ATS", style="dim")

        for idx, job in enumerate(top_jobs, 1):
            table.add_row(
                str(idx),
                job.company,
                job.title[:38] + ("…" if len(job.title) > 38 else ""),
                job.location_norm,
                f"{job.score.total:.3f}",
                job.ats_type.capitalize()
            )

        console.print(table)

@app.command(name="tailor")
def tailor_cmd(
    job_id: str = typer.Argument(..., help="ID of job to tailor materials for, or 'all' to tailor top qualified jobs"),
    limit: int = typer.Option(5, "--limit", "-n", help="Limit when tailoring multiple jobs")
):
    """Generate tailored materials bundle (resume variant, cover letter, answers.json, flags.md)."""
    init_db()
    from agents.tailor import TailoringAgent
    agent = TailoringAgent()

    if job_id == "all":
        with SessionLocal() as db:
            qualified_jobs = (
                db.query(Job)
                .join(Score)
                .join(Application)
                .filter(Score.eligible == True, Application.status.in_(["qualified", "discovered"]))
                .order_by(Score.total.desc())
                .limit(limit)
                .all()
            )
            job_ids = [j.id for j in qualified_jobs]

        console.print(f"[bold blue]Tailoring bundles for top {len(job_ids)} qualified jobs...[/bold blue]")
        for jid in job_ids:
            bundle = agent.tailor_job(jid)
            console.print(f"[bold green]✓ Tailored {bundle['company']}[/bold green] -> [cyan]{bundle['artifacts_dir']}[/cyan] (Variant: {bundle['variant']})")
    else:
        console.print(f"[bold blue]Tailoring application materials for job {job_id}...[/bold blue]")
        bundle = agent.tailor_job(job_id)
        console.print(f"[bold green]✓ Tailored materials bundle generated![/bold green]")
        console.print(f" • Company: [bold]{bundle['company']}[/bold]")
        console.print(f" • Variant: [cyan]{bundle['variant']}[/cyan]")
        console.print(f" • Artifacts directory: [yellow]{bundle['artifacts_dir']}[/yellow]")
        console.print(f" • Resume: {bundle['resume']}")
        console.print(f" • Cover letter: {bundle['cover_letter']}")
        console.print(f" • Answers: {bundle['answers']}")
        console.print(f" • Flags: {bundle['flags']}")

import asyncio

@app.command(name="apply")
def apply_cmd(
    job_id: str = typer.Argument(..., help="ID of job to pre-fill"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Optional override URL for test or custom page")
):
    """Pre-fill application form with Playwright and halt before submit."""
    init_db()
    console.print(f"[bold blue]Pre-filling application for job {job_id}...[/bold blue]")
    from agents.apply import ApplicationAgent, AlreadySubmittedError
    agent = ApplicationAgent()
    try:
        res = asyncio.run(agent.prefill_application(job_id, custom_page_url=url))
        console.print(f"[bold green]✓ Pre-fill complete! Status: {res['status']}[/bold green]")
        console.print(f" • Filled fields count: [cyan]{len(res['filled_fields'])}[/cyan]")
        console.print(f" • Screenshot: [yellow]{res['screenshot']}[/yellow]")
        console.print(f" • Form dump: [yellow]{res['form_dump']}[/yellow]")
        console.print(f" • Review packet: [bold magenta]{res['review_packet']}[/bold magenta]")
        console.print("\n[bold yellow]HALTED BEFORE SUBMIT.[/bold yellow] Run [bold green]ia review[/bold green] or [bold green]ia approve {job_id}[/bold green] to proceed.")
    except AlreadySubmittedError as e:
        console.print(f"[bold red]Already submitted:[/bold red] {e}")
    except Exception as e:
        console.print(f"[bold red]Error pre-filling application:[/bold red] {e}")
        raise

@app.command(name="review")
def review_cmd():
    """Interactively review applications with status=ready_for_review."""
    init_db()
    with SessionLocal() as db:
        pending = (
            db.query(Job)
            .join(Application)
            .filter(Application.status == "ready_for_review")
            .all()
        )
        if not pending:
            console.print("[yellow]No applications currently in 'ready_for_review' state.[/yellow]")
            console.print("Run [bold cyan]ia tailor all[/bold cyan] and [bold cyan]ia apply <job_id>[/bold cyan] first.")
            return

        console.print(f"[bold green]Found {len(pending)} application(s) ready for human review:[/bold green]\n")
        table = Table(title="Review Queue (ready_for_review)")
        table.add_column("Job ID", style="cyan")
        table.add_column("Company", style="bold green")
        table.add_column("Role", style="white")
        table.add_column("ATS", style="dim")
        table.add_column("Score", justify="right", style="magenta")
        table.add_column("Review Packet Path", style="yellow")

        for job in pending:
            packet_path = Path("artifacts") / job.id / "review_packet.md"
            score_str = f"{job.score.total:.3f}" if job.score else "N/A"
            table.add_row(
                job.id,
                job.company,
                job.title[:30] + ("…" if len(job.title) > 30 else ""),
                job.ats_type.capitalize(),
                score_str,
                str(packet_path)
            )

        console.print(table)
        console.print("\n[bold]Next steps:[/bold]")
        console.print(" • [bold green]ia approve <job_id>[/bold green] : Approve and submit application")
        console.print(" • [bold yellow]ia edit <job_id> <field> <value>[/bold yellow] : Edit a pre-filled field value")
        console.print(" • [bold red]ia reject <job_id> --reason <reason>[/bold red] : Mark application as skipped")

@app.command(name="approve")
def approve_cmd(
    job_id: str = typer.Argument(..., help="Job ID to explicitly approve for submission"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Optional override URL for test or custom page")
):
    """Approve submission for a reviewed job application."""
    init_db()
    console.print(f"[bold green]Approval granted for {job_id}. Executing submission...[/bold green]")
    from agents.apply import ApplicationAgent, SubmissionBlockedError, DailySubmissionCapExceeded, AlreadySubmittedError
    agent = ApplicationAgent()
    try:
        res = asyncio.run(agent.approve_and_submit(job_id, actor="human", custom_page_url=url))
        console.print(f"[bold green]✓ Application submitted successfully![/bold green]")
        console.print(f" • Confirmation ID: [bold cyan]{res['confirmation_id']}[/bold cyan]")
        console.print(f" • Status: [bold green]{res['status']}[/bold green]")
        console.print(f" • Submitted at: {res['submitted_at']}")
    except SubmissionBlockedError as e:
        console.print(f"[bold red]SUBMISSION BLOCKED:[/bold red] {e}")
    except (DailySubmissionCapExceeded, AlreadySubmittedError) as e:
        console.print(f"[bold yellow]Submission prevented:[/bold yellow] {e}")
    except Exception as e:
        console.print(f"[bold red]Submission failed:[/bold red] {e}")
        raise

@app.command(name="edit")
def edit_cmd(
    job_id: str = typer.Argument(..., help="Job ID"),
    field: str = typer.Argument(..., help="Field name"),
    value: str = typer.Argument(..., help="New value")
):
    """Edit a pre-filled field value before approval."""
    init_db()
    from agents.apply import ApplicationAgent
    agent = ApplicationAgent()
    res = agent.edit_field(job_id, field, value)
    console.print(f"[bold green]✓ Field updated successfully:[/bold green] [cyan]{res['field']}[/cyan] = [yellow]{res['new_value']}[/yellow] (Job: {job_id})")

@app.command(name="reject")
def reject_cmd(
    job_id: str = typer.Argument(..., help="Job ID"),
    reason: str = typer.Option("User rejected", "--reason", "-r", help="Rejection rationale")
):
    """Mark application as skipped."""
    init_db()
    from agents.apply import ApplicationAgent
    agent = ApplicationAgent()
    res = agent.reject_application(job_id, reason=reason)
    console.print(f"[bold red]✓ Marked job {job_id} as skipped.[/bold red] Reason: {res['reason']}")

@app.command(name="digest")
def digest_cmd(
    date: Optional[str] = typer.Option(None, "--date", "-d", help="Target date for digest (YYYY-MM-DD)")
):
    """Generate daily status digest report (reports/YYYY-MM-DD.md) and update tracker.xlsx."""
    init_db()
    from agents.tracker import TrackerAgent
    agent = TrackerAgent()
    target_date = None
    if date:
        try:
            target_date = datetime.date.fromisoformat(date)
        except Exception:
            console.print(f"[bold red]Invalid date format '{date}'. Please use YYYY-MM-DD.[/bold red]")
            raise typer.Exit(code=1)

    console.print(f"[bold blue]Generating daily digest report (target date: {target_date or datetime.date.today()})...[/bold blue]")
    result = agent.run(report_date=target_date)
    console.print(f"[bold green]✓ Daily digest report generated![/bold green] -> [cyan]{result['digest_path']}[/cyan]")
    console.print(f"[bold green]✓ Excel spreadsheet updated![/bold green] -> [yellow]{result['excel_path']}[/yellow]")

@app.command(name="export")
def export_cmd(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output spreadsheet path (defaults to tracker.xlsx)")
):
    """Export pipeline and job database to Excel tracker spreadsheet."""
    init_db()
    from agents.tracker import TrackerAgent
    agent = TrackerAgent()
    out_path = Path(output) if output else None
    excel_path = agent.export_excel(output_path=out_path)
    console.print(f"[bold green]✓ Pipeline exported successfully![/bold green] -> [cyan]{excel_path}[/cyan]")

@app.command(name="import")
def import_cmd(
    url: str = typer.Argument(..., help="Posting URL to manually import"),
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Company name"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Role title"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Location")
):
    """Import a manual posting URL (e.g. from LinkedIn, Handshake, or referral)."""
    init_db()
    console.print(f"[bold blue]Importing manual posting from {url}...[/bold blue]")
    from agents.sourcing import SourcingAgent
    agent = SourcingAgent()
    result = agent.import_manual_posting(url=url, company=company, title=title, location=location)
    console.print(f"[bold green]Result: {result['status']} | Job ID: {result['job_id']} | {result['company']} - {result['title']}[/bold green]")

@app.command(name="budget")
def budget_cmd():
    """Display current daily LLM spend, remaining budget, and cap status."""
    init_db()
    from tools.cost_tracker import CostTracker
    tracker = CostTracker()
    spent = tracker.get_daily_spend()
    cap = tracker.daily_cap
    remaining = max(0.0, cap - spent)
    pct = (spent / cap * 100) if cap > 0 else 0

    console.print(Panel.fit(
        f"[bold]Daily LLM Budget Health[/bold]\n"
        f" • Daily Budget Cap: [bold green]${cap:.2f}[/bold green]\n"
        f" • Spent Today: [{'yellow' if spent < cap * 0.8 else 'red'}]${spent:.4f}[/{'yellow' if spent < cap * 0.8 else 'red'}] ({pct:.1f}%)\n"
        f" • Remaining Today: [cyan]${remaining:.4f}[/cyan]\n"
        f" • Status: [{'bold green' if spent < cap else 'bold red'}]{'Active / In Budget' if spent < cap else 'HALTED / Budget Exceeded'}[/{'bold green' if spent < cap else 'bold red'}]",
        title="LLM Spend & Cap",
        border_style="cyan"
    ))

@app.command(name="verify")
def verify_cmd():
    """Re-verify ATS tokens, URLs, and custom documentation for all target companies."""
    console.print("[bold blue]Verifying target companies ATS coverage...[/bold blue]")
    from tools.company_verifier import CompanyVerifier
    verifier = CompanyVerifier()
    res = verifier.verify_companies()

    if res["is_healthy"]:
        console.print(f"[bold green]✓ All {res['total_companies']} target companies verified successfully![/bold green]")
        for cat, count in res["categories"].items():
            console.print(f" • {cat.replace('_', ' ').title()}: [cyan]{count}[/cyan] companies")
    else:
        console.print(f"[bold red]Found {len(res['errors'])} validation errors:[/bold red]")
        for err in res["errors"]:
            console.print(f" [red]• {err}[/red]")
        raise typer.Exit(code=1)

@app.command(name="sync")
def sync_cmd(
    drive: bool = typer.Option(False, "--drive", "-d", help="Sync tracker spreadsheet and database snapshot to Google Drive"),
    check: bool = typer.Option(False, "--check", "-c", help="Check Google Drive connection and list files in folder"),
    setup: bool = typer.Option(False, "--setup", "-s", help="Guide credentials setup or initiate OAuth flow"),
    folder_id: Optional[str] = typer.Option(None, "--folder", "-f", help="Override Google Drive folder ID")
):
    """Synchronize application database and tracker spreadsheet with Google Drive / Google Sheets."""
    init_db()
    from tools.gdrive_sync import GDriveSync, CredentialsNotFoundError, GoogleDriveSyncError
    from agents.tracker import TrackerAgent

    syncer = GDriveSync(folder_id=folder_id) if folder_id else GDriveSync()

    if setup:
        console.print(Panel.fit(
            "[bold cyan]Google Drive Sync Setup Instructions[/bold cyan]\n\n"
            f"Target Google Drive Folder ID: [bold yellow]{syncer.folder_id}[/bold yellow]\n"
            f"Folder URL: [link=https://drive.google.com/drive/u/0/folders/{syncer.folder_id}]https://drive.google.com/drive/u/0/folders/{syncer.folder_id}[/link]\n\n"
            "[bold green]Option 1: Service Account (Recommended for automated 8:00 AM runs)[/bold green]\n"
            " 1. In Google Cloud Console, create a Service Account with Google Drive API enabled.\n"
            " 2. Download JSON key and save as: [yellow]config/service_account.json[/yellow]\n"
            f" 3. Share the folder ([cyan]{syncer.folder_id}[/cyan]) with your service account email as [bold]Editor[/bold].\n\n"
            "[bold green]Option 2: OAuth 2.0 Client (Interactive User Login)[/bold green]\n"
            " 1. Create OAuth Client ID (Desktop Application) in Google Cloud Console.\n"
            " 2. Download JSON and save as: [yellow]config/credentials.json[/yellow]\n"
            " 3. Run: [bold cyan]ia sync --setup[/bold cyan] again to open the browser authorization consent screen.\n\n"
            "Once credentials are placed, verify with [bold green]ia sync --check[/bold green].",
            title="Google Drive Setup",
            border_style="cyan"
        ))

        # Check if credentials.json is present to run interactive flow
        client_secrets_file = syncer.config_dir / "credentials.json"
        if client_secrets_file.exists() and not (syncer.config_dir / "token.json").exists():
            console.print("\n[bold yellow]Found config/credentials.json! Launching browser for OAuth authentication...[/bold yellow]")
            try:
                syncer.run_oauth_flow()
                console.print("[bold green]✓ Successfully authorized! Token cached at config/token.json[/bold green]")
            except Exception as e:
                console.print(f"[bold red]OAuth authorization failed:[/bold red] {e}")
        return

    if check:
        console.print(f"[bold blue]Checking Google Drive connection for folder {syncer.folder_id}...[/bold blue]")
        info = syncer.check_connection()
        if info["connected"]:
            console.print(f"[bold green]✓ Successfully connected to Google Drive![/bold green]")
            console.print(f" • Folder Name: [bold]{info.get('folder_name')}[/bold]")
            console.print(f" • Folder ID: [yellow]{info.get('folder_id')}[/yellow]")
            console.print(f" • Write Permission: [{'green' if info.get('can_edit') else 'red'}]{'Yes (Editor)' if info.get('can_edit') else 'Read-Only'}[/{'green' if info.get('can_edit') else 'red'}]")
            console.print(f" • Auth Mode: [cyan]{info.get('auth_mode')}[/cyan]")

            files = syncer.list_folder_files()
            if files:
                table = Table(title=f"Files in Target Folder ({len(files)} items)")
                table.add_column("File Name", style="bold green")
                table.add_column("Type", style="yellow")
                table.add_column("File ID", style="cyan")
                table.add_column("Modified", style="dim")
                for f in files:
                    table.add_row(f.get("name"), f.get("mimeType").split(".")[-1], f.get("id"), str(f.get("modifiedTime", ""))[:19])
                console.print(table)
            else:
                console.print("[dim]Folder is currently empty.[/dim]")
        else:
            console.print(f"[bold red]Connection failed:[/bold red] {info.get('error')}")
            console.print("Run [bold cyan]ia sync --setup[/bold cyan] for instructions on configuring credentials.")
        return

    # Default action or --drive: Export local tracker first, then upload to Drive if credentials present
    console.print("[bold blue]Exporting latest local database state and Excel tracker...[/bold blue]")
    agent = TrackerAgent()
    excel_path = agent.export_excel()
    console.print(f"[bold green]✓ Local tracker updated:[/bold green] [cyan]{excel_path}[/cyan]")

    if drive:
        console.print(f"\n[bold blue]Synchronizing to Google Drive folder ({syncer.folder_id})...[/bold blue]")
        try:
            # 1. Sync spreadsheet
            sheet_res = syncer.sync_spreadsheet(local_excel_path=Path(excel_path))
            console.print(f"[bold green]✓ Google Spreadsheet synced![/bold green] ({sheet_res['action']})")
            console.print(f" • Title: [bold]{sheet_res['title']}[/bold]")
            console.print(f" • Link: [bold cyan]{sheet_res['web_url']}[/bold cyan]")
            console.print(f" • Timestamp: {sheet_res['synced_at']}")

            # 2. Backup database
            db_res = syncer.backup_database()
            console.print(f"[bold green]✓ Database backup uploaded![/bold green] ({db_res['action']})")
            console.print(f" • Backup file: [yellow]{db_res['filename']}[/yellow] ({db_res['size_bytes']} bytes)")
            console.print(f" • Local snapshot: [dim]{db_res['local_snapshot']}[/dim]")
        except CredentialsNotFoundError as e:
            console.print(f"\n[bold yellow]Google Drive credentials required to sync:[/bold yellow]")
            console.print(str(e))
        except GoogleDriveSyncError as e:
            console.print(f"[bold red]Sync error:[/bold red] {e}")
            raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"[bold red]Unexpected error during sync:[/bold red] {e}")
            raise typer.Exit(code=1)
    else:
        console.print("\n[dim]To sync to Google Drive, pass --drive flag: [bold cyan]ia sync --drive[/bold cyan][/dim]")

if __name__ == "__main__":
    app()

