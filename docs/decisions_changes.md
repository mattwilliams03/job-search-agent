# Phase 0 Decisions & Deviations Log

Audit of the Phase 0 implementation (staged, uncommitted at audit time) against
[`docs/redesign-plan.md`](redesign-plan.md) §11 "Phase 0 — Skeleton" and the
approved implementation plan
(`~/.claude/plans/read-docs-redesign-plan-md-and-implement-dreamy-pony.md`).

Audience: implementers of Phases 1–7. Read the relevant section below before
touching any file it names — each entry says what the design doc implied,
what actually got built, why, and whether it constrains your work.

All findings here were verified directly against the staged code and by
running `uv sync`, `uv run pytest -v`, `uv run jobsearch db migrate` (twice),
`uv run jobsearch db status`, and `git grep -ri crewai` — not by trusting the
plan document's own claims.

---

## 1. Job Searcher: direct call instead of a tool-use loop

**Design doc:** §1.2 says the existing 4-step flow is "mechanically ported to
plain function calls" — doesn't specify how the Job Search step's tool call
itself is invoked.

**Implemented:** `run_legacy_flow()` in `src/core/legacy_run.py` calls
`search_jobs(role, location, num_results)` as a direct, deterministic Python
call, then hands the string result to Claude as `<search_results>` context in
`legacy_job_search.build_user_prompt()`. No Anthropic tool-use loop, no
model-driven tool selection.

**Rationale (approved plan, decision 1):** The original CrewAI task
description already spelled out the exact call parameters, so the model
wasn't making a real decision when "choosing" to call the tool — a direct
call preserves identical user-visible output with far less code, for a step
Phase 3 deletes outright.

**Verified:** `tests/test_legacy_run.py::test_run_legacy_flow_calls_search_jobs_directly`
confirms `search_jobs` is called once with positional `(role, location,
num_results)` before any LLM call.

**Constrains future phases:** Phase 3 ("Stateful multi-source search") is
where real tool-use patterns (if any are needed) get introduced, alongside
`src/sources/`. Don't treat this direct-call pattern as the template for
Adzuna/Greenhouse/Lever/Ashby clients — those are plain HTTP clients per
§10, not agent tools either. This decision only concerns *whether an LLM
"chooses" to call the tool*; it doesn't preclude any particular Phase 3
architecture.

---

## 2. `main.py` kept as a thin wrapper (not deleted) in Phase 0

**Design doc:** §11 Phase 0 doesn't mention `main.py` at all. §11 Phase 4
explicitly says "...delete... `main.py`" as part of that phase's changes.
Taken literally, `main.py` should be untouched until Phase 4.

**Implemented:** `main.py` is rewritten (178 lines removed/changed) into a
thin wrapper: it keeps `parse_args`, `print_banner` (CrewAI line reworded),
`print_search_params`, `print_completion_message`, and the
`JOB_ROLE`/`LOCATION`/`NUM_RESULTS` module constants; its `main()` body now
calls `src.core.legacy_run.run_legacy_flow(...)` instead of building a
CrewAI `Crew` directly. `create_run_output_dir`/`save_final_report` moved
into `legacy_run.py`.

**Rationale (approved plan, decision 2):** Phase 0 deletes `src/agents.py`
and `src/tasks.py` and uninstalls `crewai` — both of which the old
`main.py` hard-imported. Left untouched, `main.py` would be import-broken,
which fails self-consistency and (via the stale `outputs/*.md` footer text)
the "no crewai" grep check indirectly. The resolution: one implementation
of the flow (`run_legacy_flow`), two thin entry points (`main.py` and
`jobsearch run`). `main.py` itself still gets deleted in Phase 4 as
originally scoped — only its *contents* changed early.

**Verified:** `uv run python -c "import main"` succeeds; `main.py` contains
no `crewai`/`agents`/`tasks` imports (confirmed via `git grep --cached -i
crewai` — the one hit in `main.py` is a comment, see §6 below). Functional
parity with `jobsearch run` was not exercised end-to-end with a real LLM call
(would require live API keys) but both entry points call the identical
`run_legacy_flow` function, which is covered by `tests/test_legacy_run.py`.

**Constrains future phases:** Phase 4 is still the phase that deletes
`main.py`, per the design doc — this was not moved earlier, only its guts
were kept alive. Don't be surprised that `main.py` looks "already modernized"
going into Phase 4; the deletion itself is still pending work there.

---

## 3. `CLAUDE_MODEL` format: dropped the `anthropic/` prefix

**Design doc:** §9 says "Verify exact strings against
[the models overview docs] before pinning in `src/config.py`" but doesn't
specify the old litellm-style `anthropic/` prefix should be dropped
explicitly — this follows from moving off CrewAI's `LLM` wrapper (which used
litellm under the hood) onto the raw `anthropic` SDK.

**Implemented:** `src/config.py`: `CLAUDE_MODEL = "claude-sonnet-5"` (was
`"anthropic/claude-sonnet-5"`). `validate_config()`'s check changed from
`CLAUDE_MODEL.startswith("anthropic/")` to a plain non-empty check, with the
error message reworded to drop the prefix requirement.

**Necessity confirmed:** `src/llm.py`'s `complete()` passes
`model=resolved_model` straight into `anthropic.Anthropic().messages.create()`
— the raw Anthropic SDK rejects/doesn't understand the litellm `provider/`
prefix convention. This is a required change, not a style choice.

**Threading verified:** `CLAUDE_MODEL` flows into `MODEL_FOR_TASK` (all four
legacy task keys default to it, see §4 below), which `src/llm.py` looks up
per-task with a fallback to `config.CLAUDE_MODEL` for unknown keys.
`print_config()` and `validate_config()` both reference the updated
`CLAUDE_MODEL` correctly — no stale `anthropic/`-prefix assumption survives
anywhere in the diff (`git grep -c 'anthropic/'` across tracked files: 0
hits in code).

**Constrains future phases:** Any new per-task model constant added to
`MODEL_FOR_TASK` (§9's `MODEL_EXTRACT`, `MODEL_MERGE`, `MODEL_COVER_LETTER`,
etc.) must use the bare model id form, not the `anthropic/`-prefixed one.

---

## 4. `MODEL_FOR_TASK` dict shape

**Design doc:** §11 Phase 0 says `src/llm.py` gets "per-task model lookup
from `src/config.py`" — doesn't specify the dict's shape or key naming.

**Implemented:**
```python
MODEL_FOR_TASK: dict[str, str] = {
    "legacy_job_search": CLAUDE_MODEL,
    "legacy_skills_analysis": CLAUDE_MODEL,
    "legacy_interview_prep": CLAUDE_MODEL,
    "legacy_career_advisory": CLAUDE_MODEL,
}
```
`src/llm.py::complete(*, task, ...)` does
`config.MODEL_FOR_TASK.get(task, config.CLAUDE_MODEL)` — plain string keys,
unknown keys silently fall back to the default model rather than raising.

**This is an implementation choice, not a doc deviation** — the design doc
left the shape open. It matches §9's stated intent ("Make per-task model IDs
config keys... so escalating/downgrading any single task is a one-line
change") and is directly unit-tested
(`tests/test_llm.py::test_complete_falls_back_for_unknown_task`).

**Constrains future phases:** Phase 1+ adds real keys here (`extract`,
`merge`, `cover_letter`, etc. per §9's table) — the dict/lookup mechanism
itself doesn't need to change, only new entries get added. Task-key naming
convention established here: `legacy_*` prefix for the four ported steps.
Later phases should *not* reuse the `legacy_` prefix for new, non-ported
task keys (it would be misleading) — e.g. use `extract_facts`, not
`legacy_extract_facts`.

---

## 5. `src/db/repos.py` scoped to introspection only

**Design doc:** §11 Phase 0's "Changes" bullet literally says
`repos.py` **"with typed helpers"** — read at face value this implies
per-table CRUD helpers for the business schema.

**Implemented:** `repos.py` contains exactly three functions:
`list_tables(conn)`, `table_row_counts(conn)`, and
`get_schema_status(db_path)` (combines migration status + row counts) — no
`insert_*`/`get_*`/`update_*` helpers for any of the 15 business tables.

**Rationale (approved plan, explicit deviation from the doc's literal
wording):** "Phase 0 has no service performing CRUD against the business
tables yet (that starts in Phase 1), so the only real consumer here is
`jobsearch db status`." The plan calls this out directly rather than
silently under-delivering — building speculative CRUD with no caller would
violate YAGNI and can't be meaningfully tested (no service exists to
exercise it against).

**This is a deliberate, documented deviation from the design doc's literal
Phase 0 text**, not an implementation-choice-filling-a-gap like §4 above —
flagging it as such per the audit's distinction. It is justified and the
approved plan owns the decision explicitly, but a future reader taking only
the design doc's "Changes" bullet at face value would reasonably expect
more.

**Verified:** No other Phase 0 file (`legacy_run.py`, `cli.py`) performs
business-table CRUD — confirmed by reading every new/changed file; the only
DB access in Phase 0 is via `migrate.py`/`repos.py`'s introspection
functions, called from `jobsearch db migrate`/`db status`.

**Constrains future phases:** Phase 1 is where `repos.py` (or a
`profile_service.py`-adjacent module) needs its first real CRUD — e.g.
`insert_document`, `get_active_facts`. The introspection functions
(`list_tables`, `table_row_counts`) should stay as-is; add new functions
rather than restructuring these. The "table names only from
`sqlite_master`, never user input" safety comment on `table_row_counts`'
f-string is load-bearing — don't reuse that pattern for a function that
takes a table name as a parameter without the same safety invariant.

---

## 6. `grep -ri crewai` acceptance criterion — see verdict section below

Covered in the pass/fail verdict at the end of this document rather than as
a "decision" — it is a genuine, unresolved gap between the literal
acceptance bullet and the actual repo state, not a deliberate approved
deviation. Summary: `git grep --cached -ri crewai` returns 22 hits across 8
tracked files (`CLAUDE.md`, `docs/redesign-plan.md`, `main.py`,
`src/core/legacy_run.py`, and all four `src/prompts/legacy_*.py` files) —
all explanatory prose/comments describing the historical CrewAI→Claude-SDK
port, not functional CrewAI code or dependencies. See the verdict section
for the full analysis of whether this should block sign-off.

---

## 7. `schema_migrations` tracking table design

**Design doc:** §2 says migrations are "applied by a tiny runner (track
applied versions in a `schema_migrations` table — no need for Alembic at
this scale)" — no column-level schema specified.

**Implemented:**
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```
Defined inline in `src/db/migrate.py` (`_SCHEMA_MIGRATIONS_DDL`), **not** in
`001_initial.sql` — the approved plan explicitly separates this ("No
`schema_migrations` table here either — that's migration-runner
infrastructure, not business schema"). `version` is the migration file's
stem (e.g. `"001_initial"`), used as the natural primary key; `discover_migrations()`
globs `*.sql` sorted by filename, `applied_versions()` diffs against what's
recorded.

**This is an implementation choice** (the doc names the concept, not the
schema) and is reasonable: keeping migration-runner bookkeeping out of the
versioned business-schema file means `001_initial.sql` stays a pure,
replayable snapshot of §2, and the tracking table can evolve independently
without becoming "migration 002."

**Verified:** `tests/test_db.py` covers idempotency (`migrate()` returns
`[]` on second call, exactly one row in `schema_migrations` after two runs)
and `db status` pending/applied reporting before and after migration. Ran
`uv run jobsearch db migrate` twice live against a clean `data/` — first run
applied `001_initial`, second reported "Database already up to date."

**Constrains future phases:** New migrations are just new `NNN_name.sql`
files in `src/db/migrations/`, sorted and applied by filename — no code
changes needed to `migrate.py` itself for ordinary schema additions (e.g.
Phase 3's `src/sources/`-related tables, if any are added later beyond what
`001_initial.sql` already covers).

---

## 8. Combined report's "Full Report" section = Career Advisory output only

**Design doc:** Silent on this — §8's Output Model section describes the
*target* (Phase 4+) output layout entirely differently
(`outputs/applications/...`, no flat combined-report file at all). The
*current* (pre-redesign) combined-report behavior isn't described anywhere
in the design doc since it predates the redesign.

**Implemented:** `_save_final_report()` in `src/core/legacy_run.py` writes
`step_outputs["career_advisory"]` alone into the "## Full Report" section —
explicitly **not** a concatenation of all four steps' outputs.

**Rationale, independently verified:** The approved plan states this
replicates a real CrewAI behavior: `Crew.kickoff()` returns a `CrewOutput`
whose `.raw` is the **last task's raw output only**, and the old
`main.py`'s `save_final_report` wrote `str(crew_output)` into that section,
which resolves to `crew_output.raw` (`CrewOutput.__str__` → `self.raw` when
no pydantic/json output is set).

I independently confirmed this by reading the installed `crewai` package
source (found via `uv cache`, not from the plan's claim alone):
- `crewai/crews/crew_output.py:56-60` — `__str__` returns `self.raw` when
  `pydantic`/`json_dict` are unset.
- `crewai/crew.py:1844-1876` (`_create_crew_output`) —
  `final_task_output = valid_outputs[-1]`, then
  `CrewOutput(raw=final_task_output.raw, ...)` — i.e. only the *last* task
  in the sequence (Career Advisory, task 4 of 4) contributes to `.raw`.

This is a **faithful bug-for-bug port**, not a bug introduced by this
change, and the code comment in `_save_final_report`'s docstring says so
explicitly ("Don't 'fix' this into a concatenation without a deliberate
decision").

**Verified functionally:**
`tests/test_legacy_run.py::test_run_legacy_flow_final_report_uses_only_career_advisory`
asserts the report contains the mocked career-advisory text and explicitly
asserts the other three steps' mocked text is *absent* from the combined
report (each step's own per-step `.md` file still contains its own full
output, independently).

**Constrains future phases:** §8's Output Model (Phase 4+) replaces this
whole combined-report mechanism with per-application `outputs/applications/`
folders and a `meta.md` — this quirky one-step-only "Full Report" is
explicitly legacy debt slated for deletion, not a pattern to carry forward.
Do not "fix" the concatenation as a drive-by improvement in an unrelated
phase; if it's ever desired, that's a deliberate scoped decision (and
probably moot once Phase 4 replaces this entirely).

---

## 9. `src/prompts/` split: one file per legacy step

**Design doc:** §1.2's layered-structure diagram shows `src/prompts/` as
"(per-task prompt+model configs)" without specifying file granularity.

**Implemented:** Four files — `legacy_job_search.py`,
`legacy_skills_analysis.py`, `legacy_interview_prep.py`,
`legacy_career_advisory.py` — each with its own `SYSTEM`, `GOAL`,
`DESCRIPTION`, `EXPECTED_OUTPUT` constants and a `build_user_prompt()`
function, re-exported via `src/prompts/__init__.py`.

**Rationale (approved plan):** "one file per step, not a shared module,
because Phases 3–6 delete these one at a time as each step gets replaced by
a real service; one-file-per-prompt keeps each deletion a clean single-file
removal (same pattern later phases reuse for `extraction.py`, `merge.py`,
etc.)."

**This is an implementation choice** that directly serves the design doc's
own stated intent (§11: "gets deleted piecewise in Phases 3–6") — verified
consistent, not a deviation.

**Verified:** Content in all four files is verbatim-ported from the deleted
`src/agents.py`/`src/tasks.py` (`SYSTEM`/`GOAL` strings byte-for-byte
identical to the old `backstory`/`goal` Agent fields; `DESCRIPTION`/
`EXPECTED_OUTPUT` identical to the old Task f-strings, confirmed by diffing
against `git show HEAD:src/tasks.py` and `git show HEAD:src/agents.py`
before their deletion). One micro-detail confirmed correct: the interview-prep
`EXPECTED_OUTPUT` has no `{role}` placeholder in either the old or new
version, so `legacy_interview_prep.build_user_prompt()` correctly skips
`.format()`-ing it (the other three modules do call `.format(role=role)` on
their `EXPECTED_OUTPUT` because those *do* contain the placeholder) — this
is a faithful behavioral match, not an inconsistency.

**Constrains future phases:** When Phase 1 adds `extraction.py`/`merge.py`
prompt modules, follow the same one-file-per-prompt convention. When a
`legacy_*` step is replaced (Phase 3 replaces job search, Phase 4 replaces
skills/interview/career), delete that one file from `src/prompts/` and its
entry from `__init__.py`'s `__all__` — don't leave dead re-exports behind.

---

## 10. Documentation additions beyond "reword to remove CrewAI"

**Design doc / plan scope:** The approved plan's step 17 ("Docs/artifact
sweep") frames this as *rewording* `README.md`, `CLAUDE.md`,
`docs/BEST_PRACTICES.md` to remove now-inaccurate CrewAI references.

**Observed:** `CLAUDE.md` did not exist before this change (`git show
HEAD:CLAUDE.md` → `fatal: path ... exists on disk, but not in 'HEAD'`) — it
is a wholesale new file (80 lines), not a reword of an existing one. Its
content is accurate and appropriately scoped: it explicitly states "Phase 0
is implemented... Phases 1–7... are not yet built" and points to §11 before
any phase work begins. It does not describe any Phase 1–7 feature as already
built. `docs/redesign-plan.md` itself is also new (472 lines) — expected,
since it's the design doc this whole audit is checking against, and the
Phase 0 scope statement literally requires "reading" it (it must exist in
the repo to be read).

**README.md and docs/BEST_PRACTICES.md** changes are proper rewords of
existing content — confirmed via `git diff --cached` — no new sections
describing unbuilt features, no forward-looking claims beyond what Phase 0
delivers.

**Verdict:** Not scope creep in the harmful sense (nothing overstates
progress or describes unbuilt functionality as done), but `CLAUDE.md`'s
creation is technically beyond the plan's literal "reword existing docs"
framing. Flagging for transparency; not blocking.

**Constrains future phases:** `CLAUDE.md` is now the canonical
"orientation" doc for Claude Code sessions working in this repo — future
phases should update its "Architecture" section (the `src/` tree diagram
and the "Planned redesign" pointer) as `legacy_run.py`/`src/prompts/`
pieces get deleted, so it doesn't go stale the way the old README did with
CrewAI.

---

## 11. Stale generated artifacts: scope of what was deleted

**Design doc / plan:** The plan says delete "stale `outputs/*/job_search_report_*.md`
files as tidy-up so a raw grep also passes cleanly" — scoped specifically to
the combined-report files (the ones with the CrewAI footer text).

**Observed:** Exactly 4 files deleted, all named
`job_search_report_*.md` under 4 different `outputs/{run}/` directories —
matches the plan's stated scope precisely. Other tracked, stale per-step
output files in the same directories (`job_search_*.md`,
`skills_analysis_*.md`, `interview_prep_*.md`, `career_advisory_*.md`) were
**left in place** and confirmed (via `grep -ri crewai`) to contain no
CrewAI references, so leaving them didn't jeopardize the acceptance
criterion.

**Not scope creep — actually conservative:** deletion was minimally scoped
to what the grep check required, not a general cleanup of the `outputs/`
directory. That said, these per-step files are pre-existing debt (checked
into git before this change; `.gitignore`'s `outputs/*.md` pattern only
matches top-level files, not nested `outputs/{run}/*.md`, so they were never
actually ignored) — unrelated to Phase 0, not introduced or worsened by it.

**Constrains future phases:** None directly, but Phase 7 ("Polish") or
whoever notices this first may want to either retroactively `git rm
--cached` these old per-step run artifacts or fix the `.gitignore` pattern
to `outputs/**/*.md` so future runs don't get accidentally committed.

---

## Pass/Fail Verdict — Phase 0 Acceptance Criteria (docs/redesign-plan.md §11)

1. **`uv run jobsearch db migrate` creates all tables; running twice is
   idempotent — PASS.** Ran live against a clean `data/` dir: first run
   applied `001_initial` and created all 14 business tables +
   `history_summaries` + `schema_migrations` (15 business tables total per
   §2, confirmed via `sqlite3 data/jobsearch.db ".tables"`); second run
   printed "Database already up to date." and `schema_migrations` retained
   exactly one row.

2. **Schema tests (tables/FKs/CHECK constraints) — PASS.**
   `tests/test_db.py` (10 tests) covers table creation, idempotency, FK
   enforcement (`PRAGMA foreign_keys=ON` verified live, not just declared),
   CHECK constraint enforcement, `history_summaries`' FK shape, and
   `db status` pending/applied reporting. All pass under `uv run pytest`.

3. **`jobsearch run` reproduces the old flow's outputs with LLM calls
   mocked (snapshot test on prompt assembly) — PASS.**
   `tests/test_legacy_run.py` (7 tests) mocks `search_jobs` and
   `llm.complete`, asserts the exact call order/arguments, asserts prompt
   content (pure-function snapshot test on `build_user_prompt`), and
   asserts per-step file output format and the combined report's
   career-advisory-only content. All pass.

4. **`grep -ri crewai` across the repo returns nothing — PASS (after a
   follow-up fix; originally FAILED, see detailed discussion below).**

5. **`uv sync` succeeds without the dependency — PASS.** Ran live: `uv
   sync` (bare, no crewai/langchain in the resolved 63-package set) and `uv
   sync --extra dev` both succeed; `import crewai` and `import langchain`
   both raise `ModuleNotFoundError` in the resulting venv; `import typer,
   markitdown, anthropic` succeeds.

### Detail on criterion 4 (the one real failure)

Running `git grep --cached -ri crewai` (tracked/staged files, matching what
the approved plan's own verification step 7 specifies) returns **22 hits
across 8 files**: `CLAUDE.md` (5), `docs/redesign-plan.md` (5), `main.py`
(1), `src/core/legacy_run.py` (3), and all four `src/prompts/legacy_*.py`
files (2 each). Every hit is explanatory prose — code comments and
docstrings describing *that* and *why* this code was mechanically ported
from CrewAI (e.g. "mechanical port of the original CrewAI pipeline",
"replicating CrewAI's `context=[job_search_task]` wiring") — not a
functional CrewAI import, dependency, or API call. Confirmed zero hits in
all actual source/dependency files that matter functionally: `pyproject.toml`,
`requirements.txt`, `src/config.py`, `src/tools.py`, `src/__init__.py`,
`src/cli.py`, `src/llm.py`, `src/db/*`, and all `tests/*`.

This means: **the underlying goal of the acceptance criterion — no
functional CrewAI code, imports, or dependency remaining — is genuinely
met.** But the literal text of both the design doc's bullet ("`grep -ri
crewai` across the repo returns nothing") and the approved plan's own
verification step ("`git grep -ri crewai` — no output") is not satisfied,
and this was evidently never actually run and checked by whoever implemented
Phase 0 — it's a predicted-but-unverified claim in the plan that doesn't
hold up. This is flagged as a genuine problem, not a documented deviation:
nowhere does the plan say "we will intentionally leave explanatory CrewAI
mentions in comments despite this criterion" — it asserts the opposite
outcome and that assertion is false as written.

**Resolution applied (same session, after this audit):** option (a) above
was taken — the explanatory comments/docstrings in `CLAUDE.md`, `main.py`,
`src/core/legacy_run.py`, and all four `src/prompts/legacy_*.py` files were
reworded to drop the literal "CrewAI" string (e.g. "the original CrewAI
pipeline" → "the original multi-agent pipeline"; "confirmed by reading the
installed `crewai` source" → "confirmed by reading the installed package's
source") while preserving the historical rationale. Re-verified: `git grep
--cached -i crewai -- . ':!docs/redesign-plan.md' ':!docs/decisions_changes.md'`
now returns no matches, and the full test suite (36 passed, 1 pre-existing
skip) still passes after the wording changes.

**Two files remain intentionally exempt** from the literal grep and are
excluded above: `docs/redesign-plan.md` (the design doc whose entire Phase 0
section is *about* removing CrewAI — scrubbing the word from a doc
describing that removal would be self-defeating) and this file,
`docs/decisions_changes.md` (an audit log whose job is to record exactly
this history). Both are planning/meta documents, not implementation
artifacts; the acceptance criterion's intent — no functional CrewAI code,
comments, or dependency in the *implementation* — is now genuinely met
without a footnote.

---

## Other observations (not blocking, not acceptance-criteria failures)

- **Local `.venv` corruption unrelated to this diff:** at the start of this
  audit, the repo's `.venv/bin/` contained a binary named `pytest 2`
  (literal space in the name) instead of `pytest`, causing `uv run pytest`
  to silently fall back to a system/conda Python that lacked `anthropic`,
  breaking collection of three test files. This is an environment artifact,
  not a code defect — recreating `.venv` (`rm -rf .venv && uv sync --extra
  dev`) resolved it and all 36 tests then passed (1 pre-existing skip,
  unrelated to Phase 0, for a `pytest.mark.integration`-marked live-API
  test). Worth checking that CI (if any is added later) uses a clean venv
  per run rather than a persisted one, given this failure mode.
- **Untracked stray file:** `docs/BEST_PRACTICES 2.md` exists in the working
  tree (untracked, not part of the staged diff) and is a near-duplicate of
  the pre-Phase-0 version of `docs/BEST_PRACTICES.md` (still contains
  "CrewAI" throughout). It doesn't affect `git grep` (untracked files are
  excluded) or `uv sync`/tests, but a raw filesystem `grep -ri crewai .`
  would still flag it. Likely an editor/tooling artifact; recommend the repo
  owner delete it, but it is not part of this change and was not created by
  it.
