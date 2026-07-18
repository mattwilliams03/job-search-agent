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

# Profile ingestion (Phase 1) and export/sync (Phase 2)
uv run jobsearch profile ingest resume.pdf --type resume   # resume|cover_letter|notes
uv run jobsearch profile seed                                # interactive Q&A
uv run jobsearch profile show [--section skills] [--history]
uv run jobsearch profile export                              # render profile/profile.md
uv run jobsearch profile sync                                 # sync hand-edits back into the DB

# Verify configuration/API keys are set correctly
uv run python -c "from src.config import validate_config, print_config; print_config(); print(validate_config())"

# Run tests
uv run pytest
uv run pytest -v
uv run pytest tests/test_profile_service.py
uv run pytest --cov=src

# Run a single test
uv run pytest tests/test_tools.py::test_search_jobs_success

# Lint / format (configured in pyproject.toml, not wired into CI)
uv run ruff check .
uv run black .
```

Requires a `.env` file (copy from `.env.example`) with `ANTHROPIC_API_KEY`, `ADZUNA_APP_ID`, and `ADZUNA_API_KEY`.

**If `jobsearch`/`uv run python -c "import src"` fails with `ModuleNotFoundError: No module named 'src'`**, the `.venv` editable-install metadata has gotten corrupted (this environment has a file-duplication issue — see `docs/decisions_changes.md`'s Phase 1 section for details). Fix: `rm -rf .venv && uv sync --extra dev`.

## Architecture

This repo is mid-migration per [docs/redesign-plan.md](docs/redesign-plan.md): away from a multi-agent-framework pipeline, toward a stateful SQLite-backed system with a `typer` CLI, driven directly through the Anthropic SDK. **Phases 0, 1, and 2 are implemented** (DB layer, core/CLI split, old multi-agent framework fully removed, document/profile ingestion pipeline, profile export/edit/sync). Phases 3–7 (stateful multi-source search, application drafting, interview tracking, stateful skills analysis) are not yet built — see the plan's §11 roadmap before starting any of them, and read [docs/decisions_changes.md](docs/decisions_changes.md) first: it's a running audit log, per phase, of every place the actual implementation filled a gap in or deviated from the design doc's literal text, with rationale future phases need before touching that code.

**Layered structure** (per the plan's §1.2):

```
src/cli.py       -> typer CLI: `jobsearch db migrate|status`, `jobsearch run`,
                    `jobsearch profile ingest|seed|show|export|sync`
src/core/        -> service functions with no business logic in the CLI layer
  legacy_run.py  -> the mechanically-ported 4-step job-search flow (see below);
                    gets dismantled piecewise as later phases replace each step
  profile_service.py -> document/profile-facts ingestion pipeline (see below)
src/llm.py       -> thin Anthropic SDK wrapper; complete(task=..., system=, user=)
                    resolves a per-task model via config.MODEL_FOR_TASK
src/prompts/     -> one module per pipeline step (SYSTEM + build_user_prompt);
                    legacy_* files transcribed verbatim from the deleted
                    src/agents.py + src/tasks.py; extraction.py/merge.py are
                    Phase 1's Haiku/Sonnet profile-ingestion prompts;
                    history_summary.py (Phase 2) is the one step whose
                    response is bare text, not JSON
src/db/          -> connection.py (sqlite3, WAL, FK-enforced), migrations/*.sql
                    (full schema applied in one migration, 001_initial.sql -
                    still true through Phase 2, no new migration needed),
                    migrate.py (idempotent runner), repos.py (schema
                    introspection + documents/profile_facts/fact_sources/
                    sync_state/history_summaries CRUD)
src/tools.py     -> search_jobs: the only external-API tool (Adzuna), a plain
                    function called directly (not a tool-use loop)
src/config.py    -> loads .env, defines CLAUDE_MODEL / MODEL_EXTRACT / MODEL_MERGE /
                    MODEL_HISTORY_SUMMARY / MODEL_FOR_TASK / PROFILE_SECTIONS /
                    PROFILE_MD_PATH / all tunables
main.py          -> thin wrapper delegating to src.core.legacy_run.run_legacy_flow;
                    kept only until Phase 4 deletes it per the plan
```

`data/jobsearch.db` is the SQLite system of record (gitignored); the full §2 schema is applied via `src/db/migrations/001_initial.sql`. Per `docs/decisions_changes.md`, new per-table CRUD belongs in `src/db/repos.py` — add functions there as new services need them; don't restructure the existing ones.

### Legacy job search flow

**`run_legacy_flow`** (in [src/core/legacy_run.py](src/core/legacy_run.py), called by both `main.py` and `jobsearch run`) is a faithful, mechanical port of the original multi-agent pipeline — same prompts, same output-file layout, not a redesign:

1. **Job Search** — calls `search_jobs()` in [src/tools.py](src/tools.py) directly (a deterministic Python call, not an Anthropic tool-use loop — the original task description already spelled out the exact call params), then hands the result to Claude as context for a narrative report.
2. **Skills Analysis**, 3. **Interview Prep**, 4. **Career Advisory** — each takes *only* the Job Search step's output as context (matching the original framework's `context=[job_search_task]` wiring — none of these three chain off each other).

Prompts are written in Anthropic's XML-tag style (`<instructions>`, `<focus_areas>`, `<recommendation>`, etc.) — see [docs/BEST_PRACTICES.md](docs/BEST_PRACTICES.md) for the rationale if editing them.

**Output:** each run creates `outputs/{role}_{timestamp}/`. Every step's raw output is saved to its own markdown file as soon as it completes, and a combined `job_search_report_{timestamp}.md` is written last. That combined report's "Full Report" section is **only the Career Advisory step's output**, not a concatenation of all four — this replicates a real behavior of the original framework's `Crew.kickoff()` (`CrewOutput.raw` is the last task's raw output only). Don't "fix" this without a deliberate decision; it's called out in `src/core/legacy_run.py`.

**Search parameters** (`JOB_ROLE`, `LOCATION`, `NUM_RESULTS`) are hardcoded at the top of [main.py](main.py) for that entry point; `jobsearch run` exposes the same parameters as `--role`/`--location`/`--num-results` flags instead. Defaults live in [src/config.py](src/config.py).

### Profile ingestion (Phase 1)

**`profile_service.py`** turns documents (and an interactive Q&A) into structured, provenance-tracked `profile_facts` rows, via `docs/redesign-plan.md` §4's pipeline: dirty-file guard (Phase 2, see below) → convert (markitdown) → dedupe (sha256 of the converted text) → extract (Haiku, one-bullet-one-fact, every fact grounded by a verbatim `quote`) → verify each quote actually appears in the source text (fabricated ones are mechanically dropped) → merge (Sonnet, batched — decides `new`/`duplicate_of`/`updates`/`conflicts_with` per candidate against current active facts *in the same sections*) → apply (insert/supersede + `fact_sources` provenance rows) → auto-export (Phase 2).

`ingest_document()` and `seed_profile()` share one internal pipeline (`_ingest_markdown`); `seed_profile` synthesizes a single `documents` row (`doc_type='notes'`) from the interactive answers rather than a converted file, tagged `origin='seed'` vs `ingest_document`'s `origin='ingestion'`.

PDF/DOCX ingestion requires `markitdown[pdf,docx]` — the base `markitdown` package has no PDF support at all.

**Known limitation** (observed live, documented in `docs/decisions_changes.md`): merge only compares candidates against existing facts in matching sections, so if extraction misclassifies a candidate's section, a real update can be missed (both old and new facts end up active instead of one superseding the other). Not fixed — see the decisions log before touching `merge_facts` or `extraction.py`'s prompt.

**Bugfix worth knowing about**: `apply_merge_decisions`'s `duplicate_of`/`updates`/`conflicts_with` branches now check the target fact is still `status='active'` before superseding it (fixed in Phase 2 — it's load-bearing for the history-chain walk below). If you ever see `existing is not None:` without an accompanying status check anywhere near `repos.supersede_fact`, that's the bug pattern to watch for.

### Profile export / edit / sync (Phase 2)

Closes the loop `ingest_document`/`seed_profile` left open in Phase 1: `export_profile()` renders all active facts to `profile/profile.md` (§3.1's format — one bullet per fact, each with a `<!-- f_xxxx -->` anchor comment) and snapshots the exported text + hash into `sync_state`. Every successful (non-deduped) ingestion **auto-exports afterward, unconditionally**, in the same transaction.

`sync_profile()` three-way-merges a hand-edited `profile.md` back into the DB: edited bullets become new `origin='manual'` facts that supersede the old one; deleted bullets get retired (`superseded_by=NULL`); unanchored new lines become new facts. **Conflict detection has no stored history beyond the single most recent snapshot** — `sync_state` only ever holds the latest export — so a conflict (the DB moved on via ingestion after a file was exported, and the user separately edited that same bullet before syncing) is only detectable via a **live, per-bullet, sequential lookup** of each anchor's current fact status (never a batched pre-fetch): if it resolves to `superseded` instead of `active`, and the file's text differs from that specific superseded row's own stored content (not the snapshot, which never had that uid), it's a genuine conflict — manual wins, the old chain is preserved, and the conflict is surfaced in the CLI output.

`_ingest_markdown` also gains a **dirty-file guard** (`_check_not_dirty`, first statement, applies equally to both `ingest_document` and `seed_profile` — contrast with `MIN_INGEST_TEXT_LENGTH`, which deliberately stays `ingest_document`-only): if `profile.md` exists and its hash doesn't match the last export, ingestion refuses with a clear `DirtyProfileError` until you run `profile sync`. A no-op if nothing has ever been exported.

`profile show --history` renders each active fact's superseded-ancestor lineage as one Haiku-summarized line (`src/prompts/history_summary.py`), cached in `history_summaries`. The cache needs no explicit staleness column: it's keyed on the *active* fact's own `id`, and a chain can only grow by superseding that specific active fact into a new one — which changes which `id` you'd query, producing a natural cache-miss. This only holds because of the `apply_merge_decisions` bugfix above; don't reintroduce that bug.

## Planned redesign (partially implemented)

[docs/redesign-plan.md](docs/redesign-plan.md) is the source of truth for where this repo is headed; [docs/decisions_changes.md](docs/decisions_changes.md) is the audit trail of how the actual implementation of each completed phase compares to it. Phases 0–2 are done; Phases 3–7 are not. If asked to implement a later phase, work phase-by-phase per the plan's §11 roadmap rather than jumping ahead — each phase is scoped to leave the system working and tested, and each phase's implementation should get the same kind of independent audit-against-the-plan treatment reflected in the decisions log before being considered done.
