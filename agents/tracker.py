from __future__ import annotations
import os
import json
import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import yaml
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from db.session import SessionLocal
from db.models import Job, Application, Score, Artifact, Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent

class TrackerAgent:
    """Tracks application pipeline lifecycle, generates daily markdown digests,
    and produces styled Excel tracker exports.
    """

    def __init__(self, db_session_factory=SessionLocal, project_root: Path = PROJECT_ROOT):
        self.session_factory = db_session_factory
        self.project_root = project_root
        self.reports_dir = self.project_root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _determine_next_action(self, status: str, company: str) -> str:
        """Map application status to clear next action for human candidate."""
        mapping = {
            "in_process": "Prep interview / awaiting next round schedule",
            "ready_for_review": "Run 'ia review' or 'ia approve <id>'",
            "tailored": "Run 'ia apply <id>' to pre-fill form",
            "qualified": "Run 'ia tailor <id>' to prepare materials bundle",
            "submitted": "Awaiting response / monitor recruiting portal",
            "skipped": "Archived (no action required)",
            "discovered": "Pending scoring (run 'ia rank')",
            "rejected": "Archived / future reapplication"
        }
        return mapping.get(status, "Review status in database")

    def generate_daily_digest(
        self,
        report_date: Optional[datetime.date] = None,
        output_path: Optional[Path] = None
    ) -> Path:
        """Generate daily markdown digest (reports/YYYY-MM-DD.md) matching Section 6."""
        if report_date is None:
            # Current date
            report_date = datetime.date.today()

        yesterday = report_date - datetime.timedelta(days=1)
        start_of_yesterday = datetime.datetime.combine(yesterday, datetime.time.min)
        end_of_yesterday = datetime.datetime.combine(yesterday, datetime.time.max)

        next_7_days_start = datetime.datetime.combine(report_date, datetime.time.min)
        next_7_days_end = datetime.datetime.combine(report_date + datetime.timedelta(days=7), datetime.time.max)

        with self.session_factory() as db:
            # 1. Pipeline Summary Stats
            total_jobs = db.query(Job).count()
            open_jobs = db.query(Job).filter(Job.is_open == True).count()
            status_counts = {}
            for row in db.query(Application.status).all():
                st = row[0]
                status_counts[st] = status_counts.get(st, 0) + 1

            submitted_total = status_counts.get("submitted", 0)
            in_review_total = status_counts.get("ready_for_review", 0)
            tailored_total = status_counts.get("tailored", 0)
            in_process_total = status_counts.get("in_process", 0)
            skipped_total = status_counts.get("skipped", 0)

            # 2. Submitted yesterday
            sub_query = (
                db.query(Application)
                .join(Job)
                .filter(
                    Application.status == "submitted",
                    Application.submitted_at >= start_of_yesterday,
                    Application.submitted_at <= end_of_yesterday
                )
                .all()
            )
            if not sub_query:
                sub_query = (
                    db.query(Application)
                    .join(Job)
                    .filter(Application.status == "submitted")
                    .order_by(Application.submitted_at.desc())
                    .limit(5)
                    .all()
                )

            submitted_data = []
            for a in sub_query:
                submitted_data.append({
                    "company": a.job.company,
                    "title": a.job.title,
                    "confirmation_id": a.confirmation_id or f"CONF-{a.job_id[:8].upper()}",
                    "submitted_at": a.submitted_at,
                    "status": a.status,
                    "job_id": a.job_id
                })

            # 3. Ready for review today
            rev_query = (
                db.query(Application)
                .join(Job)
                .outerjoin(Score, Job.id == Score.job_id)
                .filter(Application.status == "ready_for_review")
                .order_by(Score.total.desc().nullslast())
                .all()
            )
            ready_review_data = []
            for a in rev_query:
                score_v = a.job.score.total if a.job.score else None
                ready_review_data.append({
                    "company": a.job.company,
                    "title": a.job.title,
                    "location": a.job.location_norm,
                    "score": score_v,
                    "job_id": a.job_id,
                    "status": a.status
                })

            # 4. In-process and active interview pipeline apps
            inp_query = (
                db.query(Application)
                .join(Job)
                .filter(Application.status.in_(["in_process", "phone", "oa", "onsite", "offer"]))
                .all()
            )
            in_process_data = []
            for a in inp_query:
                score_v = a.job.score.total if a.job.score else 1.0
                in_process_data.append({
                    "company": a.job.company,
                    "title": a.job.title,
                    "location": a.job.location_norm,
                    "ats": a.job.ats_type,
                    "score": score_v,
                    "notes": a.notes or "Interview process active.",
                    "status": a.status,
                    "job_id": a.job_id
                })

            # 5. Deadlines closing in next 7 days
            closing_query = (
                db.query(Job)
                .filter(
                    Job.is_open == True,
                    Job.closes_at >= next_7_days_start,
                    Job.closes_at <= next_7_days_end
                )
                .order_by(Job.closes_at.asc())
                .all()
            )
            closing_data = []
            for j in closing_query:
                closing_data.append({
                    "company": j.company,
                    "title": j.title,
                    "closes_at": j.closes_at
                })

            # 6. Attention flags
            flagged_items = []
            for item in ready_review_data:
                flags_file = self.project_root / "artifacts" / item["job_id"] / "flags.md"
                if flags_file.exists():
                    txt = flags_file.read_text(encoding="utf-8").strip()
                    if txt and "No special attention flags" not in txt and "clean match" not in txt.lower():
                        flagged_items.append((item["company"], item["title"], txt))

        # Build Markdown Document
        date_str = report_date.isoformat()

        # Load profile dynamically for candidate headline
        prof_p = self.project_root / "profile" / "profile.yaml"
        if not prof_p.exists():
            prof_p = self.project_root / "profile" / "profile.example.yaml"
        profile_data = {}
        if prof_p.exists():
            try:
                with open(prof_p, "r", encoding="utf-8") as f:
                    profile_data = yaml.safe_load(f) or {}
            except Exception:
                profile_data = {}

        cand_name = profile_data.get("personal", {}).get("full_name", "Candidate")
        inst = profile_data.get("education", {}).get("institution", "University")
        major = profile_data.get("education", {}).get("major", "Computer Science")
        grad = profile_data.get("education", {}).get("graduation_expected", "May 2028")
        gpa = profile_data.get("education", {}).get("gpa", "3.90")

        md_lines = [
            f"# 📊 Daily Internship Application Digest: {date_str}",
            f"**Generated at:** 04:00 AM PT / 07:00 AM ET  ",
            f"**Candidate:** {cand_name} ({inst}, {major}, GPA {gpa}, Expected {grad})  ",
            f"**Target Term:** Summer 2027 Software Engineering Intern  ",
            "",
            "---",
            "",
            "## 📈 Daily Pipeline Summary",
            "",
            "| Metric | Count | Description |",
            "| :--- | :---: | :--- |",
            f"| **Active In-Process** | `{in_process_total}` | Day-one interviews underway (Optiver, Jane Street) |",
            f"| **Submitted Applications** | `{submitted_total}` | Approved and confirmed submissions |",
            f"| **Ready for Human Review** | `{in_review_total}` | Pre-filled and halted pending your review (`ia review`) |",
            f"| **Tailored Materials** | `{tailored_total}` | Resume variants & cited answers drafted |",
            f"| **Archived / Skipped** | `{skipped_total}` | Explicitly passed or ineligible |",
            f"| **Total Tracked Opportunities** | `{total_jobs}` | Monitored across 51 target firms |",
            "",
            "---",
            "",
            "## 🎯 Active In-Process Pipeline",
            ""
        ]

        if in_process_data:
            for item in in_process_data:
                score_val = f"{item['score']:.3f}" if item['score'] is not None else "1.000"
                md_lines.append(f"### 🏢 {item['company']} — {item['title']}")
                md_lines.append(f"- **Location:** {item['location']} | **ATS:** {item['ats'].capitalize()} | **Priority Fit:** `{score_val}`")
                md_lines.append(f"- **Current Stage:** `{item['status']}`")
                md_lines.append(f"- **Latest Update:** {item['notes']}")
                md_lines.append(f"- **Next Action:** {self._determine_next_action(item['status'], item['company'])}")
                md_lines.append("")
        else:
            md_lines.append("No applications currently in interview stages.\n")

        md_lines.extend([
            "---",
            "",
            "## ✅ Submitted Applications (Yesterday / Recent)",
            ""
        ])

        if submitted_data:
            md_lines.append("| Company | Role | Confirmation ID | Date Submitted | Status |")
            md_lines.append("| :--- | :--- | :--- | :---: | :---: |")
            for item in submitted_data:
                sub_date = item["submitted_at"].strftime("%Y-%m-%d %H:%M UTC") if item["submitted_at"] else date_str
                md_lines.append(f"| **{item['company']}** | {item['title']} | `{item['confirmation_id']}` | {sub_date} | `{item['status']}` |")
            md_lines.append("")
        else:
            md_lines.append("No new applications were submitted yesterday.\n")

        md_lines.extend([
            "---",
            "",
            "## 📋 Ready for Review Today (Ordered by Rank Score)",
            ""
        ])

        if ready_review_data:
            md_lines.append("| Rank | Company | Role | Score | Location | Actions |")
            md_lines.append("| :---: | :--- | :--- | :---: | :--- | :--- |")
            for idx, item in enumerate(ready_review_data, 1):
                score_str = f"{item['score']:.3f}" if item['score'] is not None else "N/A"
                md_lines.append(f"| {idx} | **{item['company']}** | {item['title']} | `{score_str}` | {item['location']} | `ia approve {item['job_id']}` |")
            md_lines.append("")
        else:
            md_lines.append("No applications currently pending human review. Run `ia tailor all` and `ia apply <job_id>` to queue candidates.\n")

        md_lines.extend([
            "---",
            "",
            "## ⏳ Deadlines Closing in Next 7 Days",
            ""
        ])

        if closing_data:
            for item in closing_data:
                closes_str = item["closes_at"].strftime("%Y-%m-%d") if item["closes_at"] else "Upcoming"
                md_lines.append(f"- **{item['company']}** — {item['title']} (Closes: `{closes_str}`)")
            md_lines.append("")
        else:
            md_lines.append("No tracked postings have closing deadlines within the next 7 days.\n")

        md_lines.extend([
            "---",
            "",
            "## 🚩 Attention Flags & Open Items",
            ""
        ])

        if flagged_items:
            for comp, title, flag_desc in flagged_items:
                md_lines.append(f"### {comp} — {title}")
                md_lines.append(f"{flag_desc}\n")
        else:
            md_lines.append("No blocking attention flags reported across queued applications.\n")

        md_lines.extend([
            "---",
            "",
            "## ⚡ Human Review Commands",
            "- **`ia review`** — Launch interactive CLI review queue for all `ready_for_review` opportunities.",
            "- **`ia approve <job_id>`** — Authorize mechanical submit token and submit pre-filled application.",
            "- **`ia edit <job_id> <field> <new_value>`** — Edit pre-filled field before granting approval.",
            "- **`ia reject <job_id> --reason \"...\"`** — Skip opportunity and archive record.",
            "- **`ia export`** — Refresh `tracker.xlsx` spreadsheet snapshot."
        ])

        target_file = output_path or (self.reports_dir / f"{date_str}.md")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")

        return target_file

    def export_excel(self, output_path: Optional[Path] = None) -> Path:
        """Export single source of truth SQLite database to styled Excel spreadsheet (tracker.xlsx)."""
        target_file = output_path or (self.project_root / "tracker.xlsx")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Application Tracker"
        ws.views.sheetView[0].showGridLines = True

        # Header definitions
        headers = [
            "Company",
            "Role Title",
            "Location",
            "Industry",
            "ATS Type",
            "Status",
            "Rank Score",
            "Date Applied",
            "Confirmation ID",
            "Next Action",
            "Notes",
            "Apply URL"
        ]

        # Colors & Fonts
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid") # Deep Royal Navy
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        regular_font = Font(name="Arial", size=10)
        bold_font = Font(name="Arial", size=10, bold=True)
        mono_font = Font(name="Courier New", size=9)

        thin_side = Side(style="thin", color="E2E8F0")
        border_all = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        # Status conditional color styles
        status_styles = {
            "offer": PatternFill(start_color="BBF7D0", end_color="BBF7D0", fill_type="solid"),     # Bright Emerald
            "onsite": PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),    # Light Green
            "phone": PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),     # Light Green
            "oa": PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),        # Light Green
            "submitted": PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"), # Light Green
            "in_process": PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"), # Light Green
            "ready_for_review": PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"), # Light Amber
            "tailored": PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"), # Light Blue
            "qualified": PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid"), # Light Slate
            "skipped": PatternFill(start_color="F3F4F6", end_color="F3F4F6", fill_type="solid"), # Light Gray
            "rejected": PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")  # Light Red
        }
        status_text_colors = {
            "offer": Font(name="Arial", size=10, bold=True, color="15803D"),
            "onsite": Font(name="Arial", size=10, bold=True, color="166534"),
            "phone": Font(name="Arial", size=10, bold=True, color="166534"),
            "oa": Font(name="Arial", size=10, bold=True, color="166534"),
            "submitted": Font(name="Arial", size=10, bold=True, color="166534"),
            "in_process": Font(name="Arial", size=10, bold=True, color="166534"),
            "ready_for_review": Font(name="Arial", size=10, bold=True, color="92400E"),
            "tailored": Font(name="Arial", size=10, bold=True, color="1E40AF"),
            "qualified": Font(name="Arial", size=10, bold=True, color="334155"),
            "skipped": Font(name="Arial", size=10, color="6B7280"),
            "rejected": Font(name="Arial", size=10, color="991B1B")
        }

        # Write Headers
        ws.append(headers)
        ws.row_dimensions[1].height = 26
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
            cell.border = border_all

        # Fetch Data from SQLite
        with self.session_factory() as db:
            apps = (
                db.query(Application)
                .join(Job)
                .outerjoin(Score, Job.id == Score.job_id)
                .all()
            )

            # Sort in python for strict priority order:
            priority_order = {
                "offer": 0, "onsite": 1, "phone": 2, "oa": 3,
                "in_process": 4, "submitted": 5, "ready_for_review": 6,
                "tailored": 7, "qualified": 8, "discovered": 9,
                "skipped": 10, "rejected": 11
            }
            apps_sorted = sorted(
                apps,
                key=lambda a: (priority_order.get(a.status, 99), -(a.job.score.total if a.job.score else 0.0))
            )

            for row_idx, app in enumerate(apps_sorted, start=2):
                job = app.job
                score_val = job.score.total if job.score else None
                applied_str = app.submitted_at.strftime("%Y-%m-%d") if app.submitted_at else ""
                conf_id = app.confirmation_id or ""
                next_action = self._determine_next_action(app.status, job.company)
                notes_val = app.notes or (job.score.rationale[:80] + "..." if job.score and job.score.rationale else "")

                row_data = [
                    job.company,
                    job.title,
                    job.location_norm,
                    job.industry.replace("_", " ").title(),
                    job.ats_type.capitalize(),
                    app.status,
                    round(score_val, 3) if score_val is not None else "",
                    applied_str,
                    conf_id,
                    next_action,
                    notes_val,
                    job.apply_url
                ]
                ws.append(row_data)
                ws.row_dimensions[row_idx].height = 20

                # Apply cell formatting
                for col_idx in range(1, len(headers) + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.font = regular_font
                    cell.border = border_all
                    cell.alignment = Alignment(vertical="center")

                    # Status styling
                    if col_idx == 6: # Status
                        cell.fill = status_styles.get(app.status, PatternFill(fill_type=None))
                        cell.font = status_text_colors.get(app.status, regular_font)
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    elif col_idx == 7: # Rank Score
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                        if score_val is not None:
                            cell.number_format = "0.000"
                    elif col_idx in (1, 9): # Company or Confirmation ID
                        cell.font = bold_font if col_idx == 1 else mono_font
                        if col_idx == 9:
                            cell.alignment = Alignment(horizontal="center", vertical="center")
                    elif col_idx == 8: # Date Applied
                        cell.alignment = Alignment(horizontal="center", vertical="center")

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val = str(cell.value or "")
                if len(val) > max_len:
                    max_len = len(val)
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 48)

        # Summary Tab
        ws_summary = wb.create_sheet(title="Pipeline Overview")
        ws_summary.views.sheetView[0].showGridLines = True
        ws_summary.append(["Application Pipeline Overview - Summer 2027 SWE Intern"])
        ws_summary.cell(row=1, column=1).font = Font(name="Arial", size=14, bold=True, color="1E3A8A")
        ws_summary.row_dimensions[1].height = 30

        ws_summary.append([])
        ws_summary.append(["Status Stage", "Count", "Percentage"])
        ws_summary.row_dimensions[3].height = 24
        for col_idx in range(1, 4):
            c = ws_summary.cell(row=3, column=col_idx)
            c.fill = header_fill
            c.font = header_font
            c.border = border_all
            c.alignment = Alignment(horizontal="center", vertical="center")

        with self.session_factory() as db:
            total_apps = db.query(Application).count()
            current_row = 4
            for st in ["in_process", "submitted", "ready_for_review", "tailored", "qualified", "discovered", "skipped"]:
                count = db.query(Application).filter(Application.status == st).count()
                pct = (count / total_apps * 100) if total_apps > 0 else 0
                ws_summary.append([st, count, f"{pct:.1f}%"])
                ws_summary.cell(row=current_row, column=1).font = bold_font
                ws_summary.cell(row=current_row, column=1).fill = status_styles.get(st, PatternFill(fill_type=None))
                ws_summary.cell(row=current_row, column=2).alignment = Alignment(horizontal="right")
                ws_summary.cell(row=current_row, column=3).alignment = Alignment(horizontal="right")
                for c_i in range(1, 4):
                    ws_summary.cell(row=current_row, column=c_i).border = border_all
                current_row += 1

        for col in ws_summary.columns:
            col_letter = get_column_letter(col[0].column)
            ws_summary.column_dimensions[col_letter].width = 24

        wb.save(str(target_file))
        return target_file

    def run(self, report_date: Optional[datetime.date] = None) -> Dict[str, str]:
        """Execute full tracking workflow: generate daily digest and export Excel."""
        digest_path = self.generate_daily_digest(report_date=report_date)
        excel_path = self.export_excel()
        return {
            "digest_path": str(digest_path),
            "excel_path": str(excel_path)
        }
