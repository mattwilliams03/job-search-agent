"""
jobsearch CLI.

Thin typer app - contains zero business logic. Every command is a
one-line call into a DB-layer or core-service function.
"""

from pathlib import Path
from typing import Optional

import typer

from src import config
from src.db.migrate import migrate
from src.db.repos import get_schema_status
from src.core.legacy_run import run_legacy_flow
from src.core import profile_service

app = typer.Typer(help="jobsearch - job search AI agent CLI")
db_app = typer.Typer(help="Database management commands")
app.add_typer(db_app, name="db")
profile_app = typer.Typer(help="Profile management commands")
app.add_typer(profile_app, name="profile")

PROFILE_INGEST_TYPES = ["resume", "cover_letter", "notes"]

PROFILE_SEED_QUESTIONS = {
    "summary": "Professional summary (who you are, what you do):",
    "experience": "Work experience (one item per line):",
    "skills": "Key skills (one per line):",
    "achievements": "Notable achievements (one per line):",
    "education": "Education background:",
    "preferences": "Job preferences (role type, location, remote, etc.):",
    "style": "Notes on your preferred resume/cover-letter voice, if any:",
}


@db_app.command("migrate")
def db_migrate_cmd() -> None:
    """Apply any pending database migrations."""
    applied = migrate()
    if applied:
        typer.echo(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        typer.echo("Database already up to date.")


@db_app.command("status")
def db_status_cmd() -> None:
    """Show applied/pending migrations and table row counts."""
    status = get_schema_status()
    typer.echo(f"DB: {status['db_path']}")
    typer.echo(f"Applied: {', '.join(status['applied_migrations']) or '(none)'}")
    typer.echo(f"Pending: {', '.join(status['pending_migrations']) or '(none)'}")
    for name, count in status["tables"].items():
        typer.echo(f"  {name}: {count} rows")


@app.command("run")
def run_cmd(
    role: str = typer.Option(config.DEFAULT_JOB_ROLE, "--role"),
    location: str = typer.Option(config.DEFAULT_LOCATION, "--location"),
    num_results: int = typer.Option(config.DEFAULT_NUM_RESULTS, "--num-results"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Run the job search flow: search, skills analysis, interview prep, career advisory."""
    is_valid, errors = config.validate_config()
    if not is_valid:
        for error in errors:
            typer.echo(f"❌ {error}")
        raise typer.Exit(code=1)

    result = run_legacy_flow(role=role, location=location, num_results=num_results, verbose=verbose)
    typer.echo(f"✅ Done — report saved to: {result.report_path}")


def _print_ingest_summary(label: str, result) -> None:
    if result.skipped:
        typer.echo(f"Already ingested (document id {result.document_id}) — no-op.")
        return

    a = result.apply
    typer.echo(
        f"{label}: {len(a.new)} new, {len(a.updated)} updated, "
        f"{len(a.duplicates)} duplicate, {len(a.conflicts)} conflict(s)."
    )
    for new_uid, existing_uid, resolution in a.conflicts:
        typer.echo(f"  ⚠️  {new_uid} conflicts with {existing_uid}: {resolution}")


@profile_app.command("ingest")
def profile_ingest_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False),
    doc_type: str = typer.Option(..., "--type", help="resume|cover_letter|notes"),
) -> None:
    """Ingest a document (resume, cover letter, or notes) into the profile."""
    if doc_type not in PROFILE_INGEST_TYPES:
        typer.echo(f"❌ --type must be one of: {', '.join(PROFILE_INGEST_TYPES)}")
        raise typer.Exit(code=1)

    try:
        result = profile_service.ingest_document(str(file), doc_type=doc_type)
    except profile_service.IngestionError as e:
        typer.echo(f"❌ {e}")
        raise typer.Exit(code=1)

    _print_ingest_summary(f"Ingested {file.name}", result)


@profile_app.command("seed")
def profile_seed_cmd() -> None:
    """Interactive Q&A that seeds initial profile facts."""
    answers = {}
    for section in config.PROFILE_SECTIONS:
        question = PROFILE_SEED_QUESTIONS.get(section, f"{section}:")
        answer = typer.prompt(question, default="", show_default=False)
        if answer.strip():
            answers[section] = answer.strip()

    if not answers:
        typer.echo("No answers given — nothing to seed.")
        return

    try:
        result = profile_service.seed_profile(answers)
    except profile_service.IngestionError as e:
        typer.echo(f"❌ {e}")
        raise typer.Exit(code=1)

    _print_ingest_summary("Seeded profile", result)


@profile_app.command("show")
def profile_show_cmd(
    section: Optional[str] = typer.Option(None, "--section"),
    history: bool = typer.Option(False, "--history"),
) -> None:
    """Show active profile facts, grouped by section."""
    sections = [section] if section else None

    if history:
        entries = profile_service.get_profile_facts_with_history(sections=sections)
        if not entries:
            typer.echo("No profile facts yet.")
            return

        grouped: dict = {}
        for entry in entries:
            grouped.setdefault(entry["fact"]["section"], []).append(entry)

        for sec in config.PROFILE_SECTIONS:
            if sec not in grouped:
                continue
            typer.echo(f"\n## {sec}")
            for entry in grouped[sec]:
                f = entry["fact"]
                typer.echo(f"  [{f['uid']}] {f['content']}")
                if entry["history_summary"]:
                    typer.echo(f"      ↳ {entry['history_summary']}")
        return

    facts = profile_service.get_profile_facts(sections=sections)

    if not facts:
        typer.echo("No profile facts yet.")
        return

    grouped: dict = {}
    for f in facts:
        grouped.setdefault(f["section"], []).append(f)

    for sec in config.PROFILE_SECTIONS:
        if sec not in grouped:
            continue
        typer.echo(f"\n## {sec}")
        for f in grouped[sec]:
            typer.echo(f"  [{f['uid']}] {f['content']}")


@profile_app.command("export")
def profile_export_cmd() -> None:
    """Render the active profile to profile.md."""
    result = profile_service.export_profile()
    typer.echo(f"Exported {result.fact_count} fact(s) to {result.path}")


@profile_app.command("sync")
def profile_sync_cmd() -> None:
    """Sync hand-edits in profile.md back into the profile."""
    try:
        result = profile_service.sync_profile()
    except profile_service.ProfileNotExportedError as e:
        typer.echo(f"❌ {e}")
        raise typer.Exit(code=1)

    if result.no_op:
        typer.echo("profile.md matches the last export — nothing to sync.")
        return

    typer.echo(
        f"Synced: {len(result.new)} new, {len(result.updated)} updated, "
        f"{len(result.deleted)} deleted, {len(result.conflicts)} conflict(s)."
    )
    for c in result.conflicts:
        typer.echo(f"  ⚠️  {c.new_uid} (was {c.original_uid}): {c.note}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
