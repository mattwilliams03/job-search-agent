"""
jobsearch CLI.

Thin typer app - contains zero business logic. Every command is a
one-line call into a DB-layer or core-service function.
"""

import typer

from src import config
from src.db.migrate import migrate
from src.db.repos import get_schema_status
from src.core.legacy_run import run_legacy_flow

app = typer.Typer(help="jobsearch - job search AI agent CLI")
db_app = typer.Typer(help="Database management commands")
app.add_typer(db_app, name="db")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
