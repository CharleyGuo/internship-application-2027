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

from db.session import (
    init_db,
    SessionLocal,
    get_user_session_factory,
    resolve_database_path,
)
from db.models import Job, Application, Event, Score

app = typer.Typer(
    name="ja",
    help="Semi-automated Software Engineering Job and Internship Application Assistant (Full-Time, New Grad, Internships).",
    no_args_is_help=True,
)
console = Console()

state = {"user": None}

@app.callback()
def main(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context (defaults to standard database and profile)")
):
    """Job Application Assistant (ja / ia) – Semi-automated SWE Job & Internship Application Assistant."""
    if user:
        state["user"] = user

def _resolve_user(user_arg: Optional[str] = None) -> Optional[str]:
    return user_arg or state.get("user")

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

@app.command(name="init")
def init_cmd(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile name for dedicated profile and database (e.g. 'alice')"),
    resume: Optional[str] = typer.Option(None, "--resume", "-r", help="Path to resume PDF or Markdown file to import"),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="Job search mode: 'general' (Full-Time, New Grad, Internship) or 'internship'"),
    force: bool = typer.Option(False, "--force", "-f", help="Force overwrite of existing profile and facts files"),
    non_interactive: bool = typer.Option(False, "--non-interactive", "-y", help="Skip interactive prompts and use example templates")
):
    """Initialize candidate profile, facts bank, resume variants, targeted job market, and dedicated database."""
    resolved_user = _resolve_user(user)
    is_interactive = not non_interactive and sys.stdin.isatty()

    if not resolved_user and is_interactive:
        chosen_user = typer.prompt("Username / Profile name (e.g. 'alice', or press Enter for default)", default="")
        if chosen_user.strip():
            resolved_user = chosen_user.strip()

    is_custom_user = bool(resolved_user and resolved_user.strip().lower() != "default")
    user_clean = "".join(c for c in resolved_user.strip().lower() if c.isalnum() or c in ("_", "-")) if is_custom_user else None

    if is_custom_user:
        user_dir = PROJECT_ROOT / "users" / user_clean
        user_dir.mkdir(parents=True, exist_ok=True)
        profile_path = user_dir / "profile.yaml"
        facts_path = user_dir / "facts.yaml"
        filters_path = user_dir / "filters.yaml"
        variants_dir = user_dir / "variants"
        variants_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (user_dir / "reports").mkdir(parents=True, exist_ok=True)
    else:
        user_dir = None
        profile_path = PROJECT_ROOT / "profile" / "profile.yaml"
        facts_path = PROJECT_ROOT / "profile" / "facts.yaml"
        filters_path = PROJECT_ROOT / "config" / "filters.yaml"
        variants_dir = PROJECT_ROOT / "profile" / "variants"
        variants_dir.mkdir(parents=True, exist_ok=True)

    profile_example = PROJECT_ROOT / "profile" / "profile.example.yaml"
    facts_example = PROJECT_ROOT / "profile" / "facts.example.yaml"
    env_path = PROJECT_ROOT / ".env"
    env_example = PROJECT_ROOT / ".env.example"

    console.print(Panel.fit(
        f"[bold cyan]Job Application Assistant – Onboarding Setup Wizard[/bold cyan]\n"
        f"Configuring workspace for: [bold green]{user_clean or 'Default User'}[/bold green]\n"
        f"Database: [cyan]{resolve_database_path(user_clean)}[/cyan]",
        title="Setup Wizard",
        border_style="cyan"
    ))

    # 1. Profile Setup
    if profile_path.exists() and not force:
        console.print(f"[bold green]✓[/bold green] Candidate profile already exists: [cyan]{profile_path}[/cyan]")
    else:
        if not is_interactive:
            if profile_example.exists():
                shutil.copyfile(profile_example, profile_path)
                console.print(f"[bold green]✓[/bold green] Initialized profile from template: [cyan]{profile_path}[/cyan]")
        else:
            console.print("\n[bold yellow]Step 1: Configure Candidate Profile[/bold yellow]")
            full_name = typer.prompt("Full Name", default="Jane Doe")
            parts = full_name.split()
            first_name = parts[0] if parts else "Jane"
            last_name = parts[-1] if len(parts) > 1 else "Doe"
            email = typer.prompt("Email", default=f"{first_name.lower()}.{last_name.lower()}@example.com")
            phone = typer.prompt("Phone number", default="(555) 123-4567")
            institution = typer.prompt("University / Current Company", default="University / Company")
            major = typer.prompt("Major / Focus Area", default="Computer Science")
            grad_expected = typer.prompt("Graduation Date or Status (e.g. May 2026, May 2028, or Graduated)", default="May 2028")
            gpa = typer.prompt("GPA (or N/A)", default="3.85")
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
                        "street": "123 Main St",
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
                    "minor": "",
                    "gpa": gpa,
                    "start_date": "August 2024",
                    "graduation_expected": grad_expected,
                    "graduation_term": grad_expected,
                    "class_year": f"Class of {grad_expected[-4:]}" if len(grad_expected) >= 4 and grad_expected[-4:].isdigit() else "Alumni"
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

    # 3. Resume Ingestion & Variants Setup
    resume_file_to_import = resume
    if not resume_file_to_import and is_interactive:
        console.print("\n[bold yellow]Step 2: Resume Ingestion[/bold yellow]")
        res_in = typer.prompt("Path to master resume PDF or Markdown file (or press Enter to use templates)", default="")
        if res_in.strip():
            resume_file_to_import = res_in.strip()

    if resume_file_to_import:
        src_resume = Path(resume_file_to_import)
        if src_resume.exists():
            if src_resume.suffix.lower() == ".pdf":
                dest_pdf = (user_dir / "resume.pdf") if user_dir else (PROJECT_ROOT / "profile" / "resume.pdf")
                shutil.copyfile(src_resume, dest_pdf)
                console.print(f"[bold green]✓[/bold green] Imported master resume PDF: [cyan]{dest_pdf}[/cyan]")
            elif src_resume.suffix.lower() in [".md", ".txt"]:
                dest_md = variants_dir / "general.md"
                shutil.copyfile(src_resume, dest_md)
                console.print(f"[bold green]✓[/bold green] Imported master resume markdown: [cyan]{dest_md}[/cyan]")
        else:
            console.print(f"[bold yellow]⚠️ Specified resume path '{resume_file_to_import}' not found. Using templates.[/bold yellow]")

    # Ensure baseline markdown variants exist
    for variant in ["general", "systems", "quant"]:
        var_file = variants_dir / f"{variant}.md"
        var_example = (PROJECT_ROOT / "profile" / "variants" / f"{variant}.example.md")
        if not var_example.exists():
            var_example = PROJECT_ROOT / "profile" / "variants" / f"{variant}.md"
        if not var_file.exists() and var_example.exists():
            shutil.copyfile(var_example, var_file)
            console.print(f"[bold green]✓[/bold green] Created resume variant: [cyan]{var_file.name}[/cyan]")

    # Check for master PDF
    target_pdf_dir = user_dir if user_dir else (PROJECT_ROOT / "profile")
    pdfs = list(target_pdf_dir.glob("*.pdf"))
    if pdfs:
        console.print(f"[bold green]✓[/bold green] Master resume PDF found: [cyan]{pdfs[0].name}[/cyan]")
    else:
        console.print(f"[bold yellow]ℹ Resume PDF notice:[/bold yellow] Place your master resume PDF into [cyan]{target_pdf_dir}/resume.pdf[/cyan]")

    # 4. Target Job Market Preferences
    search_mode = mode or "general"
    if not mode and is_interactive:
        console.print("\n[bold yellow]Step 3: Target Job Market Preferences[/bold yellow]")
        m_choice = typer.prompt("Select target roles: 1) All (Full-Time, New Grad, Internship), 2) Internship only, 3) Full-Time & New Grad only", default="1")
        if m_choice.strip() == "2":
            search_mode = "internship"
            target_types = ["internship"]
        elif m_choice.strip() == "3":
            search_mode = "full_time"
            target_types = ["full_time", "new_grad"]
        else:
            search_mode = "general"
            target_types = ["full_time", "new_grad", "internship"]
    else:
        target_types = ["internship"] if search_mode == "internship" else ["full_time", "new_grad", "internship"]

    if is_custom_user:
        base_filters = load_yaml(PROJECT_ROOT / "config" / "filters.yaml")
        base_filters["job_search_mode"] = search_mode
        base_filters["target_job_types"] = target_types
        with open(filters_path, "w", encoding="utf-8") as f:
            yaml.dump(base_filters, f, sort_keys=False, default_flow_style=False)
        console.print(f"[bold green]✓[/bold green] Configured targeted job market filters: [cyan]{filters_path}[/cyan] (Mode: {search_mode})")

    # 5. Environment File Setup
    if not env_path.exists() and env_example.exists():
        shutil.copyfile(env_example, env_path)
        console.print(f"[bold green]✓[/bold green] Created environment file: [cyan].env[/cyan]")

    # 6. Database Initialization
    init_db(user=user_clean if is_custom_user else None)
    db_resolved = resolve_database_path(user_clean if is_custom_user else None)
    console.print(f"[bold green]✓[/bold green] Dedicated SQLite database initialized: [cyan]{db_resolved}[/cyan]")

    cmd_prefix = f"--user {user_clean} " if is_custom_user else ""
    console.print(Panel.fit(
        f"[bold green]Setup Complete for {user_clean or 'Default Candidate'}![/bold green]\n\n"
        "Your assistant is ready to use with either [bold cyan]ja[/bold cyan] (Job Assistant) or [bold cyan]ia[/bold cyan] (Internship Assistant):\n"
        f" • [bold cyan]ja {cmd_prefix}status[/bold cyan]      : View profile & application pipeline state\n"
        f" • [bold cyan]ja {cmd_prefix}source[/bold cyan]      : Discover new job & internship postings\n"
        f" • [bold cyan]ja {cmd_prefix}rank[/bold cyan]        : Filter & score opportunities for your profile\n"
        f" • [bold cyan]ja {cmd_prefix}tailor all[/bold cyan]  : Generate tailored materials (resumes, answers)\n"
        f" • [bold cyan]ja {cmd_prefix}apply <id>[/bold cyan]  : Pre-fill application form with Playwright\n"
        f" • [bold cyan]ja {cmd_prefix}review[/bold cyan]      : Review pre-filled applications\n"
        f" • [bold cyan]ja {cmd_prefix}approve <id>[/bold cyan]: Grant approval & submit application\n"
        f" • [bold cyan]ja {cmd_prefix}digest[/bold cyan]      : Daily status digest & refresh tracker.xlsx",
        title="Ready",
        border_style="green"
    ))

@app.command(name="status")
def status_cmd(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Display system status, database statistics, company coverage, and pipeline state."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    companies_data = load_yaml(PROJECT_ROOT / "config" / "companies.yaml")
    companies = companies_data.get("companies", [])

    user_dir = PROJECT_ROOT / "users" / resolved_user if resolved_user and resolved_user != "default" else None
    prof_p = (user_dir / "profile.yaml") if user_dir and (user_dir / "profile.yaml").exists() else (PROJECT_ROOT / "profile" / "profile.yaml")
    if not prof_p.exists():
        prof_p = PROJECT_ROOT / "profile" / "profile.example.yaml"
    profile_data = load_yaml(prof_p)

    user_filters = (user_dir / "filters.yaml") if user_dir and (user_dir / "filters.yaml").exists() else (PROJECT_ROOT / "config" / "filters.yaml")
    filters_cfg = load_yaml(user_filters)
    target_types = filters_cfg.get("target_job_types", ["full_time", "new_grad", "internship"])

    console.print(Panel.fit(
        f"[bold green]Job & Internship Application Assistant ({', '.join(t.replace('_', ' ').title() for t in target_types)})[/bold green]\n"
        f"Candidate: [cyan]{profile_data.get('personal', {}).get('full_name', 'Not configured (run ja init)')}[/cyan] | "
        f"School: [cyan]{profile_data.get('education', {}).get('institution', 'Not configured')}[/cyan] | "
        f"GPA: [cyan]{profile_data.get('education', {}).get('gpa', 'N/A')}[/cyan] | "
        f"Class: [cyan]{profile_data.get('education', {}).get('class_year', 'N/A')}[/cyan]\n"
        f"Database: [dim]{resolve_database_path(resolved_user)}[/dim]",
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
    factory = get_user_session_factory(resolved_user)
    with factory() as db:
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
def source_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate run without writing to DB"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Run Sourcing Agent to discover new postings across GitHub lists and ATS APIs."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    console.print(f"[bold blue]Running Sourcing Agent (dry_run={dry_run}, user={resolved_user or 'default'})...[/bold blue]")
    from agents.sourcing import SourcingAgent
    agent = SourcingAgent(user=resolved_user)
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
    limit: int = typer.Option(15, "--limit", "-n", help="Number of ranked jobs to display"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Run Filter & Rank Agent to score pending jobs."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    console.print(f"[bold blue]Running Filter & Rank Agent (force={force}, user={resolved_user or 'default'})...[/bold blue]")
    from agents.rank import FilterAndRankAgent
    ranker = FilterAndRankAgent(user=resolved_user)
    scores = ranker.rank_all_jobs(force=force)

    factory = get_user_session_factory(resolved_user)
    with factory() as db:
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
    limit: int = typer.Option(5, "--limit", "-n", help="Limit when tailoring multiple jobs"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Generate tailored materials bundle (resume variant, cover letter, answers.json, flags.md)."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    from agents.tailor import TailoringAgent
    agent = TailoringAgent(user=resolved_user)

    if job_id == "all":
        factory = get_user_session_factory(resolved_user)
        with factory() as db:
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
    url: Optional[str] = typer.Option(None, "--url", help="Optional override URL for test or custom page"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Pre-fill application form with Playwright and halt before submit."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    console.print(f"[bold blue]Pre-filling application for job {job_id}...[/bold blue]")
    from agents.apply import ApplicationAgent, AlreadySubmittedError
    agent = ApplicationAgent(user=resolved_user)
    try:
        res = asyncio.run(agent.prefill_application(job_id, custom_page_url=url))
        console.print(f"[bold green]✓ Pre-fill complete! Status: {res['status']}[/bold green]")
        console.print(f" • Filled fields count: [cyan]{len(res['filled_fields'])}[/cyan]")
        console.print(f" • Screenshot: [yellow]{res['screenshot']}[/yellow]")
        console.print(f" • Form dump: [yellow]{res['form_dump']}[/yellow]")
        console.print(f" • Review packet: [bold magenta]{res['review_packet']}[/bold magenta]")
        cmd_p = f"--user {resolved_user} " if resolved_user else ""
        console.print(f"\n[bold yellow]HALTED BEFORE SUBMIT.[/bold yellow] Run [bold green]ja {cmd_p}review[/bold green] or [bold green]ja {cmd_p}approve {job_id}[/bold green] to proceed.")
    except AlreadySubmittedError as e:
        console.print(f"[bold red]Already submitted:[/bold red] {e}")
    except Exception as e:
        console.print(f"[bold red]Error pre-filling application:[/bold red] {e}")
        raise

@app.command(name="review")
def review_cmd(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Interactively review applications with status=ready_for_review."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    factory = get_user_session_factory(resolved_user)
    with factory() as db:
        pending = (
            db.query(Job)
            .join(Application)
            .filter(Application.status == "ready_for_review")
            .all()
        )
        if not pending:
            console.print("[yellow]No applications currently in 'ready_for_review' state.[/yellow]")
            cmd_p = f"--user {resolved_user} " if resolved_user else ""
            console.print(f"Run [bold cyan]ja {cmd_p}tailor all[/bold cyan] and [bold cyan]ja {cmd_p}apply <job_id>[/bold cyan] first.")
            return

        console.print(f"[bold green]Found {len(pending)} application(s) ready for human review:[/bold green]\n")
        table = Table(title="Review Queue (ready_for_review)")
        table.add_column("Job ID", style="cyan")
        table.add_column("Company", style="bold green")
        table.add_column("Role", style="white")
        table.add_column("ATS", style="dim")
        table.add_column("Score", justify="right", style="magenta")
        table.add_column("Review Packet Path", style="yellow")

        user_dir = PROJECT_ROOT / "users" / resolved_user if resolved_user and resolved_user != "default" else None
        artifacts_base = (user_dir / "artifacts") if user_dir else (PROJECT_ROOT / "artifacts")

        for job in pending:
            packet_path = artifacts_base / job.id / "review_packet.md"
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
        cmd_p = f"--user {resolved_user} " if resolved_user else ""
        console.print("\n[bold]Next steps:[/bold]")
        console.print(f" • [bold green]ja {cmd_p}approve <job_id>[/bold green] : Approve and submit application")
        console.print(f" • [bold yellow]ja {cmd_p}edit <job_id> <field> <value>[/bold yellow] : Edit a pre-filled field value")
        console.print(f" • [bold red]ja {cmd_p}reject <job_id> --reason <reason>[/bold red] : Mark application as skipped")

@app.command(name="approve")
def approve_cmd(
    job_id: str = typer.Argument(..., help="Job ID to explicitly approve for submission"),
    url: Optional[str] = typer.Option(None, "--url", help="Optional override URL for test or custom page"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Approve submission for a reviewed job application."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    console.print(f"[bold green]Approval granted for {job_id}. Executing submission...[/bold green]")
    from agents.apply import ApplicationAgent, SubmissionBlockedError, DailySubmissionCapExceeded, AlreadySubmittedError
    agent = ApplicationAgent(user=resolved_user)
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
    value: str = typer.Argument(..., help="New value"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Edit a pre-filled field value before approval."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    from agents.apply import ApplicationAgent
    agent = ApplicationAgent(user=resolved_user)
    res = agent.edit_field(job_id, field, value)
    console.print(f"[bold green]✓ Field updated successfully:[/bold green] [cyan]{res['field']}[/cyan] = [yellow]{res['new_value']}[/yellow] (Job: {job_id})")

@app.command(name="reject")
def reject_cmd(
    job_id: str = typer.Argument(..., help="Job ID"),
    reason: str = typer.Option("User rejected", "--reason", "-r", help="Rejection rationale"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Mark application as skipped."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    from agents.apply import ApplicationAgent
    agent = ApplicationAgent(user=resolved_user)
    res = agent.reject_application(job_id, reason=reason)
    console.print(f"[bold red]✓ Marked job {job_id} as skipped.[/bold red] Reason: {res['reason']}")

@app.command(name="digest")
def digest_cmd(
    date: Optional[str] = typer.Option(None, "--date", "-d", help="Target date for digest (YYYY-MM-DD)"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Generate daily status digest report (reports/YYYY-MM-DD.md) and update tracker.xlsx."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    from agents.tracker import TrackerAgent
    agent = TrackerAgent(user=resolved_user)
    target_date = None
    if date:
        try:
            target_date = datetime.date.fromisoformat(date)
        except Exception:
            console.print(f"[bold red]Invalid date format '{date}'. Please use YYYY-MM-DD.[/bold red]")
            raise typer.Exit(code=1)

    console.print(f"[bold blue]Generating daily digest report (target date: {target_date or datetime.date.today()}, user={resolved_user or 'default'})...[/bold blue]")
    result = agent.run(report_date=target_date)
    console.print(f"[bold green]✓ Daily digest report generated![/bold green] -> [cyan]{result['digest_path']}[/cyan]")
    console.print(f"[bold green]✓ Excel spreadsheet updated![/bold green] -> [yellow]{result['excel_path']}[/yellow]")

@app.command(name="export")
def export_cmd(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output spreadsheet path (defaults to tracker.xlsx)"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Export pipeline and job database to Excel tracker spreadsheet."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    from agents.tracker import TrackerAgent
    agent = TrackerAgent(user=resolved_user)
    out_path = Path(output) if output else None
    excel_path = agent.export_excel(output_path=out_path)
    console.print(f"[bold green]✓ Pipeline exported successfully![/bold green] -> [cyan]{excel_path}[/cyan]")

@app.command(name="import")
def import_cmd(
    url: str = typer.Argument(..., help="Posting URL to manually import"),
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Company name"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Role title"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Location"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Import a manual posting URL (e.g. from LinkedIn, Handshake, or referral)."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
    console.print(f"[bold blue]Importing manual posting from {url}...[/bold blue]")
    from agents.sourcing import SourcingAgent
    agent = SourcingAgent(user=resolved_user)
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
    folder_id: Optional[str] = typer.Option(None, "--folder", "-f", help="Override Google Drive folder ID"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User profile context")
):
    """Synchronize application database and tracker spreadsheet with Google Drive / Google Sheets."""
    resolved_user = _resolve_user(user)
    init_db(user=resolved_user)
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
            " 3. Run: [bold cyan]ja sync --setup[/bold cyan] again to open the browser authorization consent screen.\n\n"
            "Once credentials are placed, verify with [bold green]ja sync --check[/bold green].",
            title="Google Drive Setup",
            border_style="cyan"
        ))

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
            console.print("Run [bold cyan]ja sync --setup[/bold cyan] for instructions on configuring credentials.")
        return

    console.print(f"[bold blue]Exporting latest database state and Excel tracker (user={resolved_user or 'default'})...[/bold blue]")
    agent = TrackerAgent(user=resolved_user)
    excel_path = agent.export_excel()
    console.print(f"[bold green]✓ Local tracker updated:[/bold green] [cyan]{excel_path}[/cyan]")

    if drive:
        console.print(f"\n[bold blue]Synchronizing to Google Drive folder ({syncer.folder_id})...[/bold blue]")
        try:
            sheet_res = syncer.sync_spreadsheet(local_excel_path=Path(excel_path))
            console.print(f"[bold green]✓ Google Spreadsheet synced![/bold green] ({sheet_res['action']})")
            console.print(f" • Title: [bold]{sheet_res['title']}[/bold]")
            console.print(f" • Link: [bold cyan]{sheet_res['web_url']}[/bold cyan]")
            console.print(f" • Timestamp: {sheet_res['synced_at']}")

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
        console.print(f"\n[dim]To sync to Google Drive, pass --drive flag: [bold cyan]ja {'--user ' + resolved_user + ' ' if resolved_user else ''}sync --drive[/bold cyan][/dim]")

if __name__ == "__main__":
    app()
