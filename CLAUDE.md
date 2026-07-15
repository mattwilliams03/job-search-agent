# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This project uses `uv` for dependency management.

```bash
# Install dependencies
uv sync

# Apply database migrations (creates data/jobsearch.db)
uv run jobsearch db migrate
uv run jobsearch db status

# Run the legacy job search flow (searches jobs, generates full report)
uv run main.py
uv run main.py --verbose            # show full config and per-step save paths
uv run jobsearch run --role "..." --location "..." --num-results 5 --verbose

# Verify configuration/API keys are set correctly
uv run python -c "from src.config import validate_config, print_config; print_config(); print(validate_config())"

# Run tests
uv run pytest
uv run pytest -v
uv run pytest tests/test_tools.py
uv run pytest --cov=src

# Run a single test
uv run pytest tests/test_tools.py::test_search_jobs_success

# Lint / format (configured in pyproject.toml, not wired into CI)
uv run ruff check .
uv run black .
```

Requires a `.env` file (copy from `.env.example`) with `ANTHROPIC_API_KEY`, `ADZUNA_APP_ID`, and `ADZUNA_API_KEY`.

## Architecture

This repo is mid-migration per [docs/redesign-plan.md](docs/redesign-plan.md): away from a multi-agent-framework pipeline, toward a stateful SQLite-backed system with a `typer` CLI, driven directly through the Anthropic SDK. **Phase 0 is implemented** (DB layer, core/CLI split, the old multi-agent framework fully removed). Phases 1–7 (profile ingestion, stateful search, application drafting, interview tracking, stateful skills analysis) are not yet built — see the plan's §11 roadmap before starting any of them.

**Layered structure** (per the plan's §1.2):

```
src/cli.py       -> typer CLI: `jobsearch db migrate|status`, `jobsearch run`
src/core/        -> service functions with no business logic in the CLI layer
  legacy_run.py  -> the mechanically-ported 4-step flow (see below); gets
                    dismantled piecewise as later phases replace each step
src/llm.py       -> thin Anthropic SDK wrapper; complete(task=..., system=, user=)
                    resolves a per-task model via config.MODEL_FOR_TASK
src/prompts/     -> one module per legacy step (SYSTEM/GOAL/build_user_prompt),
                    transcribed verbatim from the deleted src/agents.py + src/tasks.py
src/db/          -> connection.py (sqlite3, WAL, FK-enforced), migrations/*.sql,
                    migrate.py (idempotent runner), repos.py (schema introspection)
src/tools.py     -> search_jobs: the only external-API tool (Adzuna), a plain
                    function called directly (not a tool-use loop)
src/config.py    -> loads .env, defines CLAUDE_MODEL / MODEL_FOR_TASK / all tunables
main.py          -> thin wrapper delegating to src.core.legacy_run.run_legacy_flow;
                    kept only until Phase 4 deletes it per the plan
```

`data/jobsearch.db` is the SQLite system of record (gitignored); the full §2 schema is applied via `src/db/migrations/001_initial.sql`, but **no service reads/writes it yet** — Phase 0 only wires up migrations and `jobsearch db status`. Nothing under `src/db/` should be extended with real per-table CRUD until a Phase 1+ service actually needs it.

**The legacy flow** (`run_legacy_flow` in [src/core/legacy_run.py](src/core/legacy_run.py), called by both `main.py` and `jobsearch run`) is a faithful, mechanical port of the original multi-agent pipeline — same prompts, same output-file layout, not a redesign:

1. **Job Search** — calls `search_jobs()` in [src/tools.py](src/tools.py) directly (a deterministic Python call, not an Anthropic tool-use loop — the original task description already spelled out the exact call params), then hands the result to Claude as context for a narrative report.
2. **Skills Analysis**, 3. **Interview Prep**, 4. **Career Advisory** — each takes *only* the Job Search step's output as context (matching the original framework's `context=[job_search_task]` wiring — none of these three chain off each other).

Prompts are written in Anthropic's XML-tag style (`<instructions>`, `<focus_areas>`, `<recommendation>`, etc.) — see [docs/BEST_PRACTICES.md](docs/BEST_PRACTICES.md) for the rationale if editing them.

**Output:** each run creates `outputs/{role}_{timestamp}/`. Every step's raw output is saved to its own markdown file as soon as it completes, and a combined `job_search_report_{timestamp}.md` is written last. That combined report's "Full Report" section is **only the Career Advisory step's output**, not a concatenation of all four — this replicates a real behavior of the original framework's `Crew.kickoff()` (`CrewOutput.raw` is the last task's raw output only), confirmed by reading the installed package's source before it was removed. Don't "fix" this without a deliberate decision; it's called out in `src/core/legacy_run.py`.

**Search parameters** (`JOB_ROLE`, `LOCATION`, `NUM_RESULTS`) are hardcoded at the top of [main.py](main.py) for that entry point (editing them is the normal way to change what's searched for there); `jobsearch run` exposes the same parameters as `--role`/`--location`/`--num-results` flags instead. Defaults live in [src/config.py](src/config.py).

## Planned redesign (partially implemented)

[docs/redesign-plan.md](docs/redesign-plan.md) is the source of truth for where this repo is headed. Phase 0 (this state) is done; Phases 1–7 are not. If asked to implement a later phase, work phase-by-phase per its §11 roadmap rather than jumping ahead — each phase is scoped to leave the system working and tested.
