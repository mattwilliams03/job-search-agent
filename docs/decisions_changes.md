# Decisions & Deviations Log

Audit of each phase's implementation against
[`docs/redesign-plan.md`](redesign-plan.md) §11 and its approved implementation
plans, kept as one running log across phases. Phase 0's audit is below;
Phase 1's is appended at the end.

## Phase 0 — Skeleton

Audit of the Phase 0 implementation (staged, uncommitted at audit time) against
§11 "Phase 0 — Skeleton" and the
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

---

## Phase 1 — Ingestion & Profile Facts

Implemented per `~/.claude/plans/read-docs-redesign-plan-md-and-implement-dreamy-pony.md`'s
Phase 1 plan (same plan file, overwritten for this phase — the Phase 0 plan
content lives only in this log now).

**Independently re-audited (second pass, same session, separate from the
implementer's own notes below):** this whole section was verified against
the actual staged diff and `docs/redesign-plan.md`'s literal §4/§9/§11/§12
text rather than trusted as written. Verification commands run: `git diff
--cached --stat`, full `git diff --cached` read for every changed file,
`uv run pytest -v` (66 collected: 65 passed, 1 pre-existing unrelated skip —
no `.venv` corruption encountered this pass, so it was **not** necessary to
`rm -rf .venv && uv sync --extra dev` this time), a live `MarkItDown().convert()`
against both PDF fixtures to confirm their extracted text, and
`importlib.metadata.metadata("markitdown")` to confirm the base package's
declared dependencies. Corrections and additions from this pass are marked
inline; everything else in the original entries below checked out as
written and is left as-is (with a short "independently verified" note added
per entry rather than being silently trusted).

### 65-test count and "exercised live against the real Anthropic API" claim

**Test count confirmed:** `uv run pytest -v` collects 66 tests, 65 pass, 1
skip (`test_search_jobs_integration`, pre-existing, unrelated to Phase 1,
gated behind `pytest.mark.integration`) — so "all 65 tests pass" is accurate
for the non-skipped set.

**"Exercised live against the real Anthropic API" could not be
independently re-verified** in this audit pass — no live API calls were made
here (all `profile_service.llm.complete` calls in the test suite are mocked,
by design, per the module docstring). This claim rests entirely on the
original implementer's own account and is called out as such: it is
plausible and consistent with the "Merge decisions are scoped to sections
present among the candidates" entry further down (which explicitly cites a
live run), but a future reader should treat "exercised live" as
implementer-reported, not independently reproduced by this audit.

### No file export in Phase 1 (confirmed, not just planned)

§4 step 5 ("Auto-export the profile file") is **not** implemented here.
`ingest_document`/`seed_profile` only write to the DB; `profile show` reads
the DB directly. Confirmed correct against the doc: Phase 2's own Changes
bullet is the one that creates `export_profile` — building it early would
have been scope creep, not fidelity.

**Independently verified (second pass):** `grep -n "open("
src/core/profile_service.py src/db/repos.py` returns no matches (no file
writes anywhere in the Phase 1 code); a repo-wide search of the staged diff
for `sync_state`, `export_profile`, `history_summaries`, and `--history` as
*implemented* symbols (as opposed to prose references to future phases)
turns up nothing — every hit is explanatory text in `profile_service.py`
docstrings/comments or in this log, correctly pointing forward at Phase 2/3.
`src/db/migrations/001_initial.sql` has zero diff (`git diff --cached --
src/db/migrations/001_initial.sql` is empty) — the schema is untouched, as
required. `src/cli.py`'s `profile_app` only registers `ingest`, `seed`, and
`show` — no `export`/`sync` commands exist yet. **No scope creep found.**

**Constrains Phase 2:** `export_profile` is entirely new code, not a
refactor of anything in `profile_service.py` from this phase.

### `profile seed` reuses the extract/merge pipeline (user's explicit decision, implemented as designed)

`seed_profile()` synthesizes one `documents` row (`doc_type='notes'`,
`filename='seed_answers.md'`) from all non-blank per-section answers,
concatenated with `## {section}` headers in `config.PROFILE_SECTIONS`
order, and runs it through the exact same `extract_facts` →
`merge_facts` → `apply_merge_decisions` pipeline as `ingest_document`,
tagged `origin='seed'`. One combined LLM call pair per seed session, not
one per section (7x fewer round-trips, verified live — a full 3-section
seed session completed in a few seconds).

**Verified live:** re-seeding with identical answers correctly no-ops via
the same sha256 dedup path used for document re-ingestion.

**Independently verified (second pass):** read `_ingest_markdown()`,
`ingest_document()`, and `seed_profile()` in full
(`src/core/profile_service.py:235-311`). Confirmed all three specific
sub-claims:
- One shared pipeline function: `_ingest_markdown(conn, *, filename,
  doc_type, markdown_text, origin)` is called from both `ingest_document`
  (with `origin="ingestion"`) and `seed_profile` (with `origin="seed"`) —
  there is no separate/duplicated extract-merge-apply logic for seeding.
- `origin` tagging: confirmed by direct call-site read, not just by name —
  `ingest_document`'s call passes `origin="ingestion"` literally,
  `seed_profile`'s passes `origin="seed"`, both flowing straight into
  `repos.insert_profile_fact(..., origin=origin)`.
- One combined call pair, not one per section: `seed_profile` builds a
  single concatenated markdown blob (`## {section}\n\n{answer}\n\n` per
  non-blank answer, looped over `config.PROFILE_SECTIONS`) and passes that
  *whole blob* as one `markdown_text` argument to `_ingest_markdown`, which
  calls `extract_facts()` and `merge_facts()` exactly once each — confirmed
  by `tests/test_cli.py`/`tests/test_profile_service.py`'s mocks never
  raising `AssertionError(f"unexpected task: {task}")` for a second call,
  and directly by `test_seed_profile_builds_blob_with_section_headers_and_seed_origin`,
  whose `captured_prompts` dict (keyed by `task`) ends up with exactly one
  entry for `"extract_facts"` containing *both* `"## summary"` and `"##
  skills"` — proof it's one call over the combined blob, not two calls.

This claim checks out exactly as stated; no correction needed.

### Merge decisions are scoped to sections present among the candidates — this can miss a real update if extraction misclassifies a candidate's section

**Observed live, not just in mocked tests:** ingesting `sample_resume_v2.pdf`
(job title changed from "Software Engineer" to "Senior Software Engineer at
Acme Corp") did **not** produce an `"updates"` decision against the original
job-title fact. Instead, Haiku's extraction classified the new candidate
under `section="summary"` (not `"experience"`, where the original fact
lives), so Sonnet's merge call — which only receives
`repos.get_active_facts(conn, sections=<sections present in candidates>)`
as context, per §4 step 4's literal "current active facts in the same
sections" — never saw the original job-title fact as a comparison
candidate at all, and correctly (given what it was shown) decided `"new"`.
Result: both the old and new job-title facts ended up `active`
simultaneously, rather than one superseding the other.

This is **not a bug** — the mocked acceptance test
(`test_ingest_document_supersedes_on_updated_job_title`) explicitly
constructs a same-section scenario and correctly proves the `"updates"`
code path (supersession, `fact_sources` linkage) works exactly as
designed when the merge step is given the chance to compare against the
right existing fact. The live run demonstrates a **real limitation of the
section-scoped merge design itself**, inherited directly from the design
doc's own wording, not introduced by an implementation shortcut.

**Constrains future phases:** if this proves to matter in practice (stale
duplicate summary/experience facts accumulating), the fix is likely one of:
(a) loosen `merge_facts`'s existing-facts fetch to include a section or two
beyond the candidates' own sections (cheap — the whole profile is small
enough per §5 that pulling in adjacent sections costs little), or (b)
tighten `extraction.py`'s section-classification instructions so
role/title-type facts land in `experience` more reliably. Neither is
built now — Phase 1's acceptance criteria don't test cross-section
scenarios, and guessing at a fix without more real examples would be
premature. Whoever touches `profile_service.merge_facts` or
`extraction.py`'s prompt next (Phase 2's `sync_profile`, or the Skills
Advisor once it starts reading profile history) should know this gap
exists before assuming section-scoped merge is airtight.

**Independently verified (second pass):** the architectural claim is
accurate — `merge_facts()` (`src/core/profile_service.py:138-183`) computes
`sections = sorted({c.section for c in candidates})` from the candidate
batch alone and passes exactly that to `repos.get_active_facts(conn,
sections=sections)`, which in turn (`src/db/repos.py`) does `WHERE status =
'active' AND section IN (...)` — so yes, existing facts outside the
candidates' own sections are genuinely invisible to the merge LLM call, sql
and Python both confirm it. This audit did not reproduce the live LLM call
(not required, and would burn a real API call for a non-deterministic
result); the code-level mechanism that would *produce* the described
behavior is real and matches the description. Treat the specific "Haiku
classified it under summary instead of experience" anecdote as
implementer-observed and plausible, not re-confirmed here — but the
underlying architectural gap it illustrates is confirmed by direct code
reading, independent of that anecdote.

### `MIN_INGEST_TEXT_LENGTH` guard: correctly scoped to `ingest_document` only (added by this audit — not previously documented)

**Gap found:** the previous version of this log's Phase 1 section never
actually documented this decision or the bug-fix history behind it, despite
it being one of the more consequential implementation choices in this
phase. Documenting it here for the first time, verified directly against
the current code (not the implementer's memory of the fix).

**Design doc:** §4 step 1 says convert should "reject/flag if output is
suspiciously short (scanned-image PDF with no text layer)" — this is
document-ingestion-specific language; §4 says nothing about `seed_profile`
(which doesn't exist as a doc concept until Phase 1's own Changes bullet
invents it).

**Implemented:** `config.MIN_INGEST_TEXT_LENGTH = 100`. The guard —
`if len(markdown_text.strip()) < config.MIN_INGEST_TEXT_LENGTH: raise
SuspiciousDocumentError(...)` — lives **only** in `ingest_document()`
(`src/core/profile_service.py:273-277`), applied to the markitdown-converted
text *before* `_ingest_markdown()` is ever called. `_ingest_markdown()`
itself contains no length check, and `seed_profile()` calls
`_ingest_markdown()` directly with no length gate of its own. Confirmed by
reading all three functions top to bottom — `_ingest_markdown` only does
the sha256 dedup check, insert, extract, merge, apply; no length logic
anywhere in it.

**Verified by test, not just by reading the guard's location:**
- `test_ingest_document_raises_for_suspiciously_short_document` monkeypatches
  `_convert_to_markdown` to return `""` and asserts `ingest_document` raises
  `SuspiciousDocumentError` *before* any LLM call (`mock.assert_not_called()`)
  and before any `documents` row is inserted (`doc_count == 0`).
- `test_seed_profile_builds_blob_with_section_headers_and_seed_origin` and
  `test_seed_profile_reingest_same_answers_is_noop` both feed `seed_profile`
  answer blobs that this audit computed at 61 and ~31 characters
  respectively — both far under the 100-char threshold — and both succeed
  (`result.skipped is False` on first call), proving `seed_profile` is not
  gated by the guard. Neither test says this in its name or a comment, so
  this connection is easy to miss on a skim; confirmed here by computing the
  actual blob length rather than assuming it from the test's stated intent.

Together these two facts (guard present + short seed answers succeed) prove
the claimed bug fix is real and currently correct, even though no single
test is explicitly named/commented as "seed succeeds where ingest would
reject." **Confirmed correct; no correction needed, but flagging that this
decision previously had zero documentation in this log.**

**Constrains future phases:** if `sync_profile`'s dirty-file guard (Phase 2)
or any other future length/content sanity check gets added to
`_ingest_markdown` directly (rather than to a specific caller), re-check
this same seed-vs-ingest asymmetry doesn't get accidentally broken.

---

### Merge JSON response schema (`candidate_index`-based) — implementation choice, not specified by the doc (added by this audit — not previously documented)

**Design doc:** §4 step 4 specifies the merge call's *semantic* output per
candidate (`new` / `duplicate_of: uid` / `updates: uid` / `conflicts_with:
uid` + resolution) but not a JSON wire format.

**Implemented:** `src/prompts/merge.py`'s `SYSTEM` prompt instructs the
model to return `{"decisions": [{"candidate_index": <int>, "decision":
"new"|"duplicate_of"|"updates"|"conflicts_with", "uid": "<existing uid, if
applicable>", "resolution": "<one sentence, if applicable>"}]}`, requiring
exactly one decision per 0-based candidate index with no gaps or duplicates.
`profile_service.merge_facts()` parses this shape and defends against LLM
misbehavior in three independent ways, all confirmed by direct code
reading (`src/core/profile_service.py:138-183`):
1. An out-of-range or non-int `candidate_index` is dropped with a
   stderr warning (`if not isinstance(idx, int) or not (0 <= idx <
   len(candidates))`).
2. A `decision` referencing a `uid` not present in the current
   `existing_uids` set is coerced to `"new"` (handles a hallucinated or
   stale uid).
3. Any candidate index missing from the response entirely is defaulted to
   `"new"` (handles a truncated/incomplete response) — this is a
   **fail-open** choice (missing decisions become new facts, not errors or
   drops); reasonable for a personal, provenance-tracked, "keep all
   history" system where an over-eager `new` is cheap to notice and correct
   later, but worth flagging explicitly since a different system might
   prefer fail-closed here.

**Consistency confirmed:** `merge.py`'s example JSON in `build_user_prompt`
uses the exact same field names/types (`candidate_index` as int, `decision`
as one of the four literal strings, `uid`, `resolution`) that
`profile_service.py`'s parser reads — cross-checked line by line, no
mismatch (e.g. no place where the prompt says `"index"` and the parser
reads `"candidate_index"`, or similar). **Sane and consistent; no
correction needed.**

---

### `MODEL_FOR_TASK` new keys: `legacy_` prefix correctly avoided (added by this audit — not previously documented)

Per Phase 0's own decisions log (§4 above in this file), new non-ported task
keys must not use the `legacy_` prefix. Phase 1 adds exactly two new keys —
confirmed by reading `src/config.py`'s diff directly:
```python
MODEL_EXTRACT = "claude-haiku-4-5-20251001"
MODEL_MERGE = CLAUDE_MODEL   # == "claude-sonnet-5"

MODEL_FOR_TASK: dict[str, str] = {
    ...
    "extract_facts": MODEL_EXTRACT,
    "merge_facts": MODEL_MERGE,
}
```
`"extract_facts"` and `"merge_facts"` — neither uses the `legacy_` prefix.
This also satisfies §9's model-assignment table: extraction → Haiku 4.5,
merge → Sonnet 5 (`MODEL_MERGE` aliases `CLAUDE_MODEL`, which Phase 0 set to
`"claude-sonnet-5"`). **Confirmed correct.**

One minor, non-blocking observation from this audit: `src/config.py`'s new
comment above `MODEL_FOR_TASK` reads "...that's reserved for the ported
**CrewAI**-era flow." Phase 0's own audit (§6 and the Phase 0 pass/fail
verdict, both above in this file) went to specific effort to scrub the
literal string "CrewAI" from every functional `src/` file, precisely so a
plain filesystem `grep -ri crewai .` (not just `git grep --cached` with
doc-file exclusions) would come back clean outside the two intentionally
exempted planning documents. This one-word reintroduction in a Phase 1 diff
quietly undoes that for `src/config.py`. It is genuinely harmless (a
comment, not code) and Phase 1 has no `grep -ri crewai` acceptance criterion
of its own to fail — but it's worth a maintainer's attention since it's the
kind of small regression that compounds if repeated in later phases.
Suggested fix next time this file is touched: reword to "...that's reserved
for the ported legacy/pre-redesign flow" (matching the wording pattern
Phase 0 already used elsewhere).

---

### `conflicts_with` handling: both facts kept active, conflict surfaced (added by this audit — not previously documented)

**Design doc:** Phase 1's own Acceptance bullet doesn't test the
`conflicts_with` path at all (only `updates` is explicitly required, via
the "old fact superseded, new fact active" criterion). §3.2's "manual wins"
conflict-resolution language is scoped to `sync_profile`'s three-way merge
(Phase 2), which doesn't exist yet — so Phase 1 has no doc-specified
behavior for a merge-time `conflicts_with` decision at all; this is
implementer discretion, correctly called out as such in
`apply_merge_decisions`'s own code comment
(`src/core/profile_service.py:218-220`).

**Implemented:** on `conflicts_with`, `apply_merge_decisions` inserts the
new candidate as its own new, **active** `profile_facts` row (not
superseding the existing one, not dropping the candidate), links its
provenance via `fact_sources`, and appends `(new_uid, existing_uid,
resolution)` to `ApplyResult.conflicts` — which `src/cli.py`'s
`_print_ingest_summary` renders as a `⚠️` warning line per conflict. Neither
fact's `status` changes; both remain `'active'` simultaneously.

**Verified by test:**
`test_apply_merge_decisions_conflicts_with_keeps_both_facts_active` asserts
both `old_fact["status"] == "active"` and `new_fact["status"] == "active"`
after a `conflicts_with` decision, and that the resolution text and
existing uid surface correctly in `result.conflicts`. **Matches the
implementer's description exactly; confirmed correct.**

**Constrains Phase 2:** `sync_profile`'s three-way merge is where a real
resolution mechanism (§3.2's "manual wins") eventually applies to
conflicting facts. Until then, a Phase-1-created conflict pair just sits as
two simultaneously-active facts in the same section — `profile show` (and,
later, `get_profile_context`) will display both with no indication they
disagree unless the caller separately surfaces `ApplyResult.conflicts` at
ingest time (which the CLI does, but only in that one command's terminal
output — nothing persists the conflict flag on the row itself for a later
`profile show` to notice). Worth keeping in mind if stale unresolved
conflicts turn out to accumulate before Phase 2 lands.

---

### `markitdown[pdf,docx]` dependency change and PDF test fixtures (added by this audit — not previously documented)

**Claim to verify:** base `markitdown` (pre-Phase-1: `markitdown>=0.0.1a2`)
lacks PDF support without the `[pdf,docx]` extra, and the new
`markitdown[pdf,docx]>=0.1.0` pin in `pyproject.toml` is both correct and
necessary.

**Independently confirmed, live, this pass** (not just trusting the
implementer's "confirmed live" claim): `importlib.metadata.metadata("markitdown")`
in the current `.venv` (installed version 0.1.6) lists `Requires-Dist` as
`beautifulsoup4, charset-normalizer, defusedxml, magika, markdownify,
requests` unconditionally, with `pdfminer-six` and `pdfplumber` gated
behind `extra == 'pdf'` (and `lxml`/`mammoth` behind `extra == 'docx'`) —
i.e. the base install genuinely has no PDF-parsing library available; a
bare `markitdown` dependency would fail to convert a PDF at runtime. The
`[pdf,docx]` extra in `pyproject.toml` is both correct and necessary, not
speculative hardening.

**PDF fixtures independently converted, this pass:** ran
`MarkItDown().convert()` directly (not via the test suite's mocks) against
both `tests/fixtures/sample_resume_v1.pdf` and `sample_resume_v2.pdf`.
Output:
- v1 → `"Jordan Lee\nSoftware Engineer at Acme Corp\nBuilt a scalable data
  pipeline in Python\nProficient in SQL and distributed systems"` (126
  chars)
- v2 → identical except `"Senior Software Engineer at Acme Corp"` (133
  chars)

Both are real, valid, single-page PDFs (`file` reports "PDF document,
version 1.4, 1 pages" for each, ~700 bytes each — not empty placeholders).
Every quote string the mocked extraction responses in
`tests/test_profile_service.py` assert against (`"Jordan Lee"`, `"Software
Engineer at Acme Corp"` / `"Senior Software Engineer at Acme Corp"`, `"Built
a scalable data pipeline in Python"`, `"Proficient in SQL and distributed
systems"`) appears verbatim in the real converted text — so the two tests
that exercise the real `markitdown` conversion path (rather than
monkeypatching `_convert_to_markdown`) are testing against fixtures that
actually produce the text the tests assume, not fixtures that happen to
pass only because conversion is mocked elsewhere. **Confirmed correct.**

---

### Environment note: `.venv` corruption recurred (same failure mode as Phase 0's audit)

Mid-Phase-1, `uv run jobsearch` (and even `.venv/bin/python3` directly)
started raising `ModuleNotFoundError: No module named 'src'` despite
`uv run python -c "import src"` working fine moments earlier. Root cause:
`.venv/lib/python3.13/site-packages/` had accumulated **three** duplicate
editable-install `.pth` files (`_editable_impl_job_search_agent.pth`,
` 2.pth`, ` 3.pth`, all with identical content but different timestamps —
the same macOS-Finder-style " 2"/" 3" duplication pattern the Phase 0
audit already flagged for `docs/BEST_PRACTICES 2.md` and a stray
`pytest 2` binary). `rm -rf .venv && uv sync --extra dev` resolved it
again. This is the second time in two phases; something in this
environment (likely a cloud-sync client watching the working directory)
is duplicating files on write, including inside `.venv`. **Not a code
issue** — no source file was affected — but worth the repo owner
investigating the root cause (e.g. excluding this directory from
whatever sync tool is doing this) rather than re-discovering it every
phase.

**Independent second-pass audit note:** this audit's own `uv run pytest -v`
run did **not** encounter `.venv` corruption — `.venv` was already healthy
(no duplicate `.pth` files, `import src` worked normally, all 66 tests
collected and ran) at the start of this audit pass, so `rm -rf .venv && uv
sync --extra dev` was **not** needed this time. This doesn't contradict the
implementer's account above (their corruption and this audit's clean run
happened at different points in the same session — the recreated `.venv`
from their fix was presumably still intact when this audit started); it's
recorded here only so a future reader doesn't assume every session
necessarily hits this. The underlying root-cause recommendation (repo owner
should investigate the sync-tool-duplication theory) still stands — it's
now been observed twice, both times self-resolving via `.venv` recreation,
never actually blocking this audit's own run.

---

## Pass/Fail Verdict — Phase 1 Acceptance Criteria (docs/redesign-plan.md §11)

Independently verified by reading the actual test bodies in
`tests/test_profile_service.py` (not just confirming a similarly-named test
exists) and by running `uv run pytest -v` directly.

1. **Ingesting a sample PDF (fixture) creates a `documents` row and ≥N
   `profile_facts` with `fact_sources` provenance — PASS.**
   `test_ingest_document_creates_document_and_facts_with_provenance` ingests
   the real `sample_resume_v1.pdf` fixture (via the real `MarkItDown()`
   conversion path, LLM calls mocked), asserts a `documents` row exists with
   `doc_type == "resume"`, exactly 4 `profile_facts` rows and 4
   `fact_sources` rows exist. Confirmed the fixture itself really is a
   convertible PDF (see the dependency/fixtures entry above) — this isn't
   trivially true just because the LLM response is mocked.

2. **Re-ingesting the same file is a no-op (sha256) — PASS.**
   `test_ingest_document_reingest_is_noop` ingests `sample_resume_v1.pdf`
   twice; asserts `second.skipped is True`, `second.document_id ==
   first.document_id`, no new LLM calls (`mock.call_count ==
   calls_after_first`), and `documents`/`profile_facts` row counts unchanged
   after the second call. The dedup key is genuinely sha256 of the
   converted markdown text (`hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()`
   in `_ingest_markdown`), confirmed by direct code read, not just inferred
   from the test name.

3. **A unit test feeds the extractor a mocked LLM response containing a
   fabricated quote and asserts that fact is dropped — PASS.**
   `test_extract_facts_drops_fact_with_fabricated_quote` mocks an
   `extract_facts` response with one fact whose quote is genuinely present
   in the source text and one whose quote (`"Worked at Google for 10
   years"`) is not; asserts the fabricated one is absent from the returned
   candidates and only 1 of 2 survives. `verify_quote()`'s
   whitespace-normalized substring check is exercised directly by three
   other passing unit tests as well.

4. **Merge test: ingest a second fixture containing an updated job title
   → old fact `superseded`, new fact active, both linked — PASS.**
   `test_ingest_document_supersedes_on_updated_job_title` ingests
   `sample_resume_v1.pdf` then `sample_resume_v2.pdf` (title
   "Software Engineer at Acme Corp" → "Senior Software Engineer at Acme
   Corp", confirmed via live `markitdown` conversion to be the fixtures'
   actual real difference, not just the mocked LLM response's premise);
   asserts the old fact's `status == "superseded"` and `superseded_by ==
   new_row["id"]`, the new fact's `status == "active"`, and each fact has
   its own distinct `fact_sources` row pointing at its own source document
   (`old_source["document_id"] != new_source["document_id"]`) — i.e. "both
   linked" via provenance, not just via the `superseded_by` foreign key.

**All 4 acceptance criteria: PASS**, independently verified against actual
test bodies and a live `uv run pytest -v` run (65 passed, 1 pre-existing
unrelated skip), not merely trusted from the implementer's own account.

**Scope creep: none found.** No Phase 2+ concepts (`export_profile`,
`sync_profile`, `sync_state`, `history_summaries`, `--history`) appear as
implemented code anywhere in the staged diff — only as correctly-scoped
forward-looking prose in comments/docstrings/this log.
`src/db/migrations/001_initial.sql` has a literal empty diff. The CLI only
gained `profile ingest`, `profile seed`, and `profile show`, matching the
Phase 1 Changes bullet exactly.

**Genuine unresolved items (not deliberate, documented implementation
choices):**
- None found that would block sign-off. The two lowest-severity items
  surfaced by this audit are cosmetic: (a) the reintroduced "CrewAI" word
  in a `src/config.py` comment (harmless, no functional effect, no Phase 1
  acceptance criterion depends on it — see the `MODEL_FOR_TASK` entry
  above), and (b) several of the implementation choices covered above
  (merge JSON schema, `MIN_INGEST_TEXT_LENGTH` placement, `conflicts_with`
  handling, the `markitdown[pdf,docx]` dependency necessity) had **zero**
  documentation in this log prior to this audit pass despite being
  consequential, verifiable decisions — now filled in above. That's a
  documentation-process gap in how this log was maintained during
  implementation, not a code defect; flagging it so future phases write
  these entries as decisions are made rather than relying on a later audit
  pass to backfill them.
- The section-scoped merge limitation (documented above) remains a real,
  known architectural gap — correctly flagged as such by the implementer
  and re-confirmed by this audit at the code level — but it is explicitly
  out of Phase 1's acceptance scope and does not block sign-off.

**Log status: trustworthy for future phases to rely on**, with the above
corrections/additions now incorporated.

---

## Phase 2 — Profile Export / Edit / Sync

Implemented per `~/.claude/plans/read-docs-redesign-plan-md-and-implement-dreamy-pony.md`'s
Phase 2 plan (same plan file, overwritten again for this phase — Phase 1's
plan content lives only in this log now, same convention as Phase 0→1).
All 87 tests pass (`uv run pytest`); the full pipeline (`export`, hand-edit,
`sync`, dirty-guard refusal, `--history`) was exercised live against the
real Anthropic API, not just mocked tests.

**Independently audited (first pass, this session, separate from the
implementer's own notes below):** everything in this section was verified
against the actual staged diff and `docs/redesign-plan.md`'s literal
§3/§9/§11 text rather than trusted as written, mirroring the Phase 1 audit's
methodology. Note on git state: `git log` shows exactly one prior commit
(`802f90f "Phase 0 Updates"`), containing only Phase 0's files (no
`profile_service.py`, `extraction.py`, `merge.py`, or
`tests/test_profile_service.py` — confirmed via `git show 802f90f --stat`).
Phase 1 was therefore never git-committed separately; `git diff --cached`
for this session shows Phase 1 *and* Phase 2 combined as one staged diff
(e.g. `src/core/profile_service.py`, `src/prompts/extraction.py`, and
`src/prompts/merge.py` all show as brand-new files). This means a clean
git-level before/after diff of Phase-2-only changes to functions that
already existed in Phase 1 (`apply_merge_decisions`, `_ingest_markdown`)
is not obtainable — verification of those below relies on direct reading of
the current code plus the specific regression test, not a diff. Verification
commands actually run: `git diff --cached --stat`, full `git diff --cached`
read for every changed file, `uv run pytest -v` (88 collected: 87 passed, 1
pre-existing unrelated skip — no `.venv` corruption encountered this pass,
consistent with — but not proof against — the log's characterization
below), and direct reads of `src/core/profile_service.py`,
`src/db/repos.py`, `src/db/connection.py`, `src/config.py`, `src/cli.py`,
`src/db/migrations/001_initial.sql` (confirmed empty diff), and both test
files in full. Corrections/additions from this pass are marked inline;
everything else below checked out as written.

### Bundled Phase 1 bugfix: `apply_merge_decisions` re-superseding an already-superseded fact

Found during Phase 2 design, not by accident during Phase 2 coding: all
three of `apply_merge_decisions`'s `duplicate_of`/`updates`/`conflicts_with`
branches looked up a target fact by uid and proceeded **without checking it
was still `status='active'`**. If a single merge batch referenced the same
existing uid twice (nothing forbade this), the second call would
re-supersede an already-superseded row, silently overwriting its
`superseded_by` pointer and orphaning the first update from the chain.
This would have broken Phase 2's ancestor-chain walk
(`get_superseded_ancestors` assumes at most one child per node) and the
history-cache self-invalidation guarantee `--history` depends on.

**Fix:** all three branches now guard on `existing is not None and
existing["status"] == "active"`, falling through to the existing "new"
fallback otherwise (same fallback already used for a vanished uid).
**Verified:** `test_apply_merge_decisions_second_update_to_same_uid_falls_through_to_new`
constructs exactly this scenario (two `"updates"` decisions targeting the
same uid in one batch) and asserts the first fact's ancestor chain has
exactly one link, not a corrupted/overwritten one.

**Constrains future phases:** any new code path that calls
`repos.supersede_fact` must look up its target fresh and check
`status == 'active'` first — never assume a uid resolves to something
supersedable just because it exists.

**Independently verified (this audit):** the current code
(`src/core/profile_service.py`'s `apply_merge_decisions`) does guard all
three branches exactly as described — `duplicate_of`
(`existing["status"] == "active"` before recording provenance),
`updates`, and `conflicts_with` all check freshly-fetched `existing`
before proceeding, falling through to the "new" insert otherwise. The
"before" half of this claim (that the check was previously *absent*)
cannot be confirmed via `git diff`, since — per the note above —
`profile_service.py` has no earlier committed version to diff against in
this repo's git history; it is accepted on the implementer's account,
consistent with the design-doc-predicted failure mode actually occurring
if the guard were removed (traced by hand: without it, a second
`"updates"` decision against an already-superseded uid would call
`repos.supersede_fact(old_fact_id=<already-superseded row's id>, ...)`,
silently overwriting that row's `superseded_by` pointer and detaching the
first update from the chain — exactly the corruption the regression test
checks for). Also independently re-read the full body of
`test_apply_merge_decisions_second_update_to_same_uid_falls_through_to_new`
(not just its name): it asserts `len(result.updated) == 1`,
`len(result.new) == 1`, walks `get_superseded_ancestors` on the first
update's fact and asserts it equals exactly `[original["uid"]]` (one
link, not corrupted), and separately asserts the second candidate's own
fact has zero ancestors (`get_superseded_ancestors(...) == []`) — i.e. it
proves the second decision was *not* silently merged into or corrupting
the first chain, matching the claim precisely.

Separately confirmed (per the audit brief's request to check whether
`sync_profile` reintroduces the same class of bug): every call site of
`repos.supersede_fact` in `profile_service.py` — in `apply_merge_decisions`
(one), `_process_deletions`, and `_process_anchored_bullet` (two, one per
branch) — either checks a freshly-fetched row's `status == "active"`
immediately before the call, or (in `_process_anchored_bullet`'s
already-superseded/conflict branch) calls `repos.resolve_live_descendant`,
which itself walks the chain fresh and only returns a row with
`status == "active"` (or `None`, guarded by an `is not None` check at the
call site) — so no `sync_profile` code path resupersedes a stale row
either. The cache self-invalidation reasoning below depends on this
holding for *all* callers, not just the Phase 1 ones; confirmed it does.

### Dirty-file guard: one enforcement point, not one per entry point

Lives as the **first statement inside `_ingest_markdown`** (before the
sha256 dedup check), not duplicated at the top of `ingest_document` and
`seed_profile` separately. This is a deliberate *symmetry* with
`MIN_INGEST_TEXT_LENGTH`'s deliberate *asymmetry* (that guard is
`ingest_document`-only, since a short seed answer isn't a failed-conversion
signal) — different guards, different scopes, both intentional. Don't
"fix" one to match the other's placement by pattern-matching without
re-reading why each is where it is.

**No file ever exported yet → guard is a no-op**, preserving Phase 1's
status quo for users who never touch `profile.md` at all — verified by
`test_ingest_document_succeeds_when_profile_never_exported`.

**Independently verified (this audit):** read `_ingest_markdown`,
`ingest_document`, and `seed_profile` in full
(`src/core/profile_service.py:563-655`). `_check_not_dirty(conn)` is
literally the first statement inside `_ingest_markdown`, before the
sha256 dedup check; neither `ingest_document` nor `seed_profile` calls it
directly — both only call `_ingest_markdown`. `_check_not_dirty`'s
no-op condition is exactly `if not config.PROFILE_MD_PATH.exists(): return`
— confirmed by direct read, not inferred. `MIN_INGEST_TEXT_LENGTH`'s
guard remains solely inside `ingest_document` (applied to the
markitdown-converted text before `_ingest_markdown` is called);
`_ingest_markdown` itself has no length check and `seed_profile` never
gates on one — Phase 1's documented asymmetry is intact, not accidentally
duplicated or moved. Also confirmed `src/cli.py`'s `profile_seed_cmd`
currently wraps its `profile_service.seed_profile(answers)` call in a
`try/except profile_service.IngestionError` (which `DirtyProfileError`
subclasses) identically to `profile_ingest_cmd` — the audit brief flagged
a "fix to `profile_seed_cmd`'s missing try/except" as expected Phase 2
scope; the try/except is present and correct in the current code, though
(per the git-history note above) there is no separate pre-Phase-2 commit
of `cli.py` to confirm it was actually *added* here rather than always
present. `tests/test_cli.py::test_profile_seed_command_exits_nonzero_on_ingestion_error`
exercises exactly this path (mocks `seed_profile` to raise
`DirtyProfileError`, asserts exit code 1 and the error text in output).

### The "impossible" 3-way race is real, and only detectable via live per-uid DB lookups

Worked through this carefully before implementing: since the dirty-guard
blocks ingestion whenever `profile.md` is dirty, a genuine DB-vs-file
conflict looks unreachable at first glance. It isn't — the actual failure
mode is a **stale editor buffer**: export (S0) → open the file in an
editor → run `ingest` *before saving* (file on disk is still clean, guard
allows it, DB mutates to S1, auto-export overwrites the file with S1) →
editor, unaware the file changed underneath it, saves the user's S0-based
edit, clobbering S1 back to "S0 + edit."

Because `sync_state` only ever stores the **single most recent** snapshot
(no history of older snapshots), there is no stored S0 to diff against.
The only working signal is a **live lookup of each anchored bullet's
current fact status**: if it resolves to `superseded` instead of `active`,
the DB moved on since that anchor's version was current — compare the
file's text against *that specific (superseded) row's own stored content*
(not the snapshot, which never had this uid at all) to tell a genuine edit
from stale-but-untouched carryover.

**Verified live**, not just in mocked tests: `test_sync_profile_conflict_manual_edit_wins_over_ingestion_change`
constructs this exact scenario, and the real CLI walkthrough (see
Verification section below) reproduced an equivalent real conflict
organically — a live Sonnet merge call, unprompted, classified a candidate
as `conflicts_with` an existing fact with differing skill-level claims,
correctly kept both active, and surfaced it in the CLI's `⚠️` output.

**Independently verified (this audit), including the "snapshot never had
this uid" claim specifically:** `_render_profile_markdown` (which produces
both the file and the stored `sync_state.last_export_snapshot` — the same
function is used for both, confirmed by reading `_export_profile`) calls
`repos.get_active_facts(conn)` with no status filter beyond what that
function itself does (`WHERE status = 'active'`), so the snapshot is
exactly a serialization of whichever facts were active *at that specific
export call* — confirmed by direct code read of both
`_render_profile_markdown` and `get_active_facts`
(`src/db/repos.py`'s `WHERE status = 'active' ... ORDER BY section, id`).
Since `_export_profile` always does a full `INSERT OR REPLACE` of
`sync_state.last_export_snapshot` (`repos.set_sync_state`, no history of
older snapshots retained), the claim holds exactly as stated for the real
race the log describes: export S0 (fact active) → ingest (mutates DB,
auto-exports S1, overwriting the *stored* snapshot to S1) → stale editor
save clobbers the *file* back toward S0. At that point `sync_state`'s
snapshot is S1, which genuinely does not contain the now-superseded
fact's uid (it wasn't active when S1 was rendered) — so a
snapshot-based comparison is not merely unused but literally
unavailable, and reading `_process_anchored_bullet`
(`src/core/profile_service.py:425-457`) confirms the code never attempts
one: the comparison is always against `fact["content"]`, the live DB
row's own (immutable-once-set) content field.

One nuance worth flagging: the actual unit test
(`test_sync_profile_conflict_manual_edit_wins_over_ingestion_change`)
does *not* replicate this exact race byte-for-byte — it simulates only
the DB-side effects of an intervening ingestion (direct `repos.insert_*`/
`supersede_fact` calls) without calling the real `_ingest_markdown`
pipeline, so in the test `sync_state.last_export_snapshot` is never
actually overwritten to S1 and still equals S0 (which *does* contain the
conflicting uid, unlike the real race). This is a legitimate
simplification, not a test that passes for the wrong reason: since
`_process_anchored_bullet` never reads `last_export_snapshot`'s content
for this comparison at all (only `_process_deletions` reads
`snapshot_uids`, for a different purpose — detecting bullets dropped from
the file), whether the snapshot happens to contain the uid or not is
irrelevant to the code path being exercised. The test correctly exercises
the same code, just via a slightly different (and easier-to-set-up)
route to the same DB state. Confirmed the sequential-per-bullet DB lookup
claim too: `sync_profile`'s two `for section, content, uid in file_bullets`
loops each call `_process_anchored_bullet`/`_process_unanchored_bullet`
individually per bullet (each doing its own `repos.get_fact_by_uid` call),
not a batched pre-fetch keyed by uid.

### Auto-export is unconditional, not conditional on "did anything change"

Every successful (non-skipped) `_ingest_markdown` run ends with
`_export_profile(conn)` in the same transaction — even a `sync_profile`
pass whose only effect was silently dropping a stale carryover bullet
(zero DB mutations) still re-exports, because the *file* still needs
re-aligning even when the *DB* didn't change. This was a correction to an
earlier draft that conditioned re-export on "did steps 5-7 do any work" —
wrong, since the file-vs-DB misalignment is exactly what the stale-carryover
case produces.

**Independently verified (this audit):** `_ingest_markdown`
(`src/core/profile_service.py:563-586`) calls `_export_profile(conn)`
unconditionally as its last statement before returning — not wrapped in
any `if` on `apply_result`'s contents. (The dedup-skip path returns
earlier, before this line is reached, which is the "non-skipped" carve-out
the claim already states.) `sync_profile`
(`src/core/profile_service.py:475-521`) likewise calls
`_export_profile(conn)` unconditionally once it proceeds past its own
`no_op` early-return — not gated on whether `result.new`/`updated`/
`deleted`/`conflicts` ended up non-empty. Both match the claim exactly.

### History-summary cache needs no explicit staleness column — verified, not just assumed

`history_summaries` has no ancestor-count or version column, by design.
The reasoning: an active fact's `id` never changes, and (now that the
Phase 1 bugfix above holds) a chain can only grow by superseding *that
specific active fact* into a brand-new one — which changes which `fact_id`
a caller would query, producing a natural cache-miss on the new id. No
invalidation logic exists or is needed.

**Verified by test, both directions:** `test_get_profile_facts_with_history_renders_cached_summary_and_caches_llm_call`
calls twice and asserts the mock's `call_count == 1` (cache hit);
`test_history_summary_cache_invalidates_when_chain_grows` supersedes the
head into a new fact and asserts the *next* call's count becomes `2` (fresh
computation on the new id, unprompted by any explicit cache-clear).
**Verified live**: two consecutive `profile show --history` runs against
the same real chain returned byte-identical summary text, the second
noticeably faster (no LLM round-trip) — consistent with a real cache hit,
though wall-clock timing alone isn't proof; the mocked tests are the actual
guarantee.

**Independently verified (this audit), including the two named tests'
full bodies:** `get_fact_history_summary` (`src/core/profile_service.py:524-541`)
looks up the cache via `repos.get_history_summary(conn, active_fact["id"])`
— the *active* fact's own id, confirmed by direct read, not the chain
head's id under some other identity. The "no code path calls
`supersede_fact` without a fresh active-status check first" half of this
claim is covered by the cross-check documented above in the bundled
Phase 1 bugfix entry (all four call sites across
`apply_merge_decisions`/`_process_deletions`/`_process_anchored_bullet`
checked). Read both named tests in full, not just their names:
`test_get_profile_facts_with_history_renders_cached_summary_and_caches_llm_call`
builds a 1-ancestor chain, calls `get_profile_facts_with_history` twice,
and asserts both calls return the same summary text *and*
`mock.call_count == 1` (the actual cache-hit proof, not just matching
output). `test_history_summary_cache_invalidates_when_chain_grows` builds
a 1-ancestor chain, asserts `call_count == 1` after the first call, then
supersedes the (2-fact) chain's head into a third fact — growing it to a
2-ancestor chain — and asserts the next call returns the newly-mocked
`"summary v3"` text with `call_count == 2`, i.e. a real cache miss
triggered purely by the fact_id change, no explicit invalidation call
anywhere. Both assertions are exactly as the log describes.

**One test-coverage nuance, not a code defect:** the design doc's Phase 2
acceptance bullet asks for "`profile show --history` on a fact with two
superseded ancestors renders a cached one-line lineage... and the cache
invalidates when the chain grows" — read literally, one scenario
demonstrating both a 2-ancestor chain *and* a cache hit on it. No single
test does exactly this: the first test proves caching (call-twice,
`count==1`) on a 1-ancestor chain; the second test grows a chain to 2
ancestors but only calls it once at that length (a cache *miss*, since
growth just happened), never following up with a second call at the
2-ancestor depth to prove *that specific* chain length caches too. Since
`get_fact_history_summary`'s cache lookup is generic — keyed only on
`active_fact["id"]`, with no branch or special-casing on ancestor count —
this is very unlikely to be a real gap in behavior, and the combination of
both tests plus the code read above gives good confidence. But it is a
literal gap between the acceptance text and what's actually asserted, not
previously called out in this log; flagging it for transparency rather
than silently treating the two existing tests as a perfect match for the
literal wording.

### Manual edits get no `fact_sources` row

`fact_sources.document_id` is `NOT NULL` with an FK to `documents`
(`PRAGMA foreign_keys = ON` on every connection) — there is no document
backing a hand-edit, and inventing a synthetic "profile.md" `documents` row
to satisfy the FK would be a misleading provenance lie. `origin='manual'`
is the entire provenance story for sync-created facts; no `sync_profile`
code path calls `insert_fact_source`.

**Independently verified (this audit):** `grep -n "insert_fact_source"
src/core/profile_service.py` returns exactly 4 hits, all inside
`apply_merge_decisions` (lines 247, 258, 270, 277 in the current file) —
the ingestion-only merge-application function. None appear in
`sync_profile`, `_process_anchored_bullet`, `_process_unanchored_bullet`,
or `_process_deletions`. Confirmed correct.

### `get_profile_facts_with_history` must commit — a `get_profile_facts`-pattern trap avoided

`get_profile_facts` (Phase 1) reads via `connection.get_connection()` +
bare `close()` — correct for a pure read, no `commit()` needed. Copying
that pattern for `get_profile_facts_with_history` would have been wrong:
this "read" command writes `history_summaries` cache rows as a side
effect, and a non-committing close silently discards uncommitted writes
under sqlite3's default rollback-on-close behavior. It uses
`connection.connect()` (commit-on-success) instead. Flagging this
explicitly since the bug would have been invisible in any test that only
checked the *returned* summary text (which would still be correct within
that single call) — only a *second* call, on a fresh connection, would
have exposed the discarded cache. The cache-invalidation tests above
incidentally also cover this, since they depend on the cache actually
persisting between calls.

**Independently verified (this audit):** read `src/db/connection.py` in
full. `connection.connect(db_path)` is a `@contextmanager` that calls
`get_connection()`, `yield`s, then calls `conn.commit()` on success (or
`conn.rollback()` on exception) before `conn.close()` in a `finally` —
genuinely commit-on-success, not merely named as if it were.
`connection.get_connection(db_path)` by contrast returns a bare
`sqlite3.Connection` with no commit/rollback wrapper at all. Confirmed
`get_profile_facts_with_history` (`src/core/profile_service.py:544-556`)
uses `with connection.connect(db_path) as conn:`, while `get_profile_facts`
(`src/core/profile_service.py:650-654`, Phase 1) uses
`conn = connection.get_connection(db_path)` + a bare `finally: conn.close()`
with no `commit()` call anywhere. The distinction is real, not just
documentation — a `get_connection()`-based `get_profile_facts_with_history`
would genuinely lose every `history_summaries` cache write on process
exit or connection reuse, since sqlite3 rolls back an uncommitted
transaction on close by default.

### Export/sync file writes and dirty-check: read-from-disk discipline

`_export_profile` writes via temp-file + `os.replace` as its final step
(after `sync_state` upserts are staged on the same uncommitted `conn`) —
accepted, undefended limitation: the DB commit and the file write are two
different storage engines, so true cross-system atomicity isn't
achievable without 2PC, and a disk-full/permissions failure mid-write
still rolls back the whole transaction (freshly-extracted facts included).
Acceptable for a personal, single-user tool; not engineered around.

`_check_not_dirty` and `sync_profile`'s no-op check both hash the file
**as read from disk**, never a freshly-rendered string — re-rendering
inside the dirty-check would defeat its purpose (it needs to compare what's
actually on disk against what was last recorded, not against what the
current DB state would produce right now, which is exactly the
before/after distinction the whole guard exists to make).

### Environment note: `.venv` corruption recurred twice more this phase (4th and 5th occurrences)

Hit **twice** during Phase 2 implementation (once right after adding the
CLI commands, once mid-live-verification while re-running `profile show
--history`) — same failure mode as Phase 0 and Phase 1's audits: `uv run
jobsearch`/`.venv/bin/python3` raising `ModuleNotFoundError: No module
named 'src'`. `rm -rf .venv && uv sync --extra dev` resolved it both times,
consistent with the prior two phases' fix. This is now the 4th and 5th
observed occurrence across 3 phases in one session. The duplicate-`.pth`-file
theory from Phase 0/1's notes wasn't confirmed as the specific cause both
times this phase (one occurrence showed a single, non-duplicated `.pth`
file, ruling out that exact mechanism for at least that instance) — so the
root cause may be broader than "a sync tool duplicates files," possibly
intermittent editable-install metadata corruption from *any* concurrent
`.venv` write during a `uv` auto-resync. **Recommendation stands and
strengthens**: worth the repo owner's investigation before Phase 3, since
this is now a recurring, session-blocking interruption rather than a
one-off.

**Independent audit-pass note (this session):** this audit's own
`uv run pytest -v` run did **not** encounter `.venv` corruption — `.venv`
was already healthy at the start of this audit pass (88 collected, 87
passed, 1 pre-existing skip, no `ModuleNotFoundError`), so `rm -rf .venv
&& uv sync --extra dev` was not needed. This neither confirms nor refutes
the "4th/5th occurrence" count above (both of those happened earlier in
the same session, mid-implementation, on a `.venv` this audit pass never
saw in its corrupted state) — recorded here only so a future reader knows
this pass's clean run doesn't mean the issue resolved itself. The
"needs root-cause investigation" recommendation neither strengthens nor
weakens based on this pass; it remains open and unconfirmed either way,
exactly as before.

### CLAUDE.md was not updated for Phase 2 — genuine gap, not a documented decision (found by this audit)

**Observed:** `git diff --cached -- CLAUDE.md` shows real edits in this
staged diff, but every one of them describes **Phase 1**, not Phase 2 —
e.g. "Profile ingestion (Phase 1)" as a new section heading, "**No file
export happens in Phase 1** — `profile/profile.md` and
`export_profile`/`sync_profile` are Phase 2's job" (now false: both are
implemented in this very diff), and the top-line summary itself:
"**Phases 0 and 1 are implemented**... Phases 2–7 (profile export/sync,
stateful search...) are not yet built." Grepping the current (post-diff)
`CLAUDE.md` for `export|sync|history|phase 2` (case-insensitive) turns up
no mention of `profile export`, `profile sync`, `profile show --history`,
`sync_state`, or `history_summaries` as *implemented* — only the one
now-stale "Phase 2's job" sentence.

**Why this matters:** Phase 0's own audit (§10 in this log, "Documentation
additions beyond 'reword to remove CrewAI'") explicitly flagged
`CLAUDE.md` as "now the canonical 'orientation' doc for Claude Code
sessions working in this repo" and said future phases should keep its
Architecture section current "so it doesn't go stale the way the old
README did with CrewAI." Phase 2 didn't do this — the file staged in this
diff is Phase 1's version of `CLAUDE.md`, not a Phase-2-updated one, even
though `CLAUDE.md` itself has real diff hunks in this commit (meaning it
*was* touched this session, just apparently only carried forward from an
earlier point in the session before Phase 2 started, and never revisited
after). A future Claude Code session reading `CLAUDE.md` fresh (as it's
designed to be read first) would be told Phase 2 doesn't exist and would
have no pointer to `profile export`/`profile sync`/`profile show --history`
as available commands.

**This is a genuine, unresolved gap**, not a deliberate scoped decision —
nowhere does the Phase 2 log section above claim `CLAUDE.md` was
intentionally left as-is. Flagging as a real follow-up item rather than
silently correcting it myself (this audit is scoped to
`docs/decisions_changes.md` only, per its instructions — not code or other
docs).

**Constrains future phases:** before or alongside Phase 3, `CLAUDE.md`'s
"Architecture" section and top-line phase summary need a real update for
Phase 2 (not just Phase 3's own additions layered on top of stale Phase 1
text) — add `profile export`/`profile sync`/`profile show --history` to
the command list, update "Phases 0 and 1 are implemented" to "0–2", and
replace the "No file export happens in Phase 1... Phase 2's job" sentence
now that Phase 2 is done.

## Pass/Fail Verdict — Phase 2 Acceptance Criteria (docs/redesign-plan.md §11)

Independently verified by reading the actual test bodies in
`tests/test_profile_service.py`/`tests/test_cli.py` (not just confirming a
similarly-named test exists), by direct reads of the current
`src/core/profile_service.py`, `src/db/repos.py`, `src/config.py`,
`src/cli.py`, and by running `uv run pytest -v` directly (88 collected: 87
passed, 1 pre-existing unrelated skip).

1. **Export → hand-edit a bullet + delete one + add one → `sync` updates
   /supersedes/creates the right facts with `origin='manual'` — PASS.**
   `test_sync_profile_updates_edited_deleted_and_added_bullets` exports
   three facts, hand-writes a file with one bullet unchanged, one edited
   (`"SQL"` → `"Advanced SQL"`, same anchor), one bullet's anchor entirely
   absent (deleted), and one new unanchored bullet (`"Kubernetes"`).
   Asserts: the deleted fact is `superseded` with `superseded_by IS NULL`;
   the kept fact stays `active`; the edited fact's *old* row is
   `superseded` pointing at a *new* row whose `content == "Advanced SQL"`
   and `origin == "manual"`; the new bullet becomes a fresh `active` fact
   with `content == "Kubernetes"` and `origin == "manual"`. Also asserts
   `mock.assert_not_called()` — sync makes zero LLM calls, per §3.2's
   literal "Sync... a three-way merge" (no LLM step specified) — and that
   the re-exported file afterward contains `"Advanced SQL"`/`"Kubernetes"`
   and not `"Old skill"`.

2. **Conflict test (DB fact changed post-export + same bullet edited in
   file) → manual wins, superseded row preserved, conflict reported in
   output — PASS.**
   `test_sync_profile_conflict_manual_edit_wins_over_ingestion_change`
   exports fact X, simulates an intervening ingestion superseding X into
   Y, then syncs a file whose bullet still carries X's anchor but edited
   text (`"Lead Engineer"`). Asserts: `result.conflicts` has exactly one
   entry with `original_uid == X`, `descendant_uid == Y`; the new winning
   fact is `active`, `content == "Lead Engineer"`, `origin == "manual"`;
   `Y` is now `superseded` pointing at the winner (**manual wins** over
   the ingestion-created row); `X`'s own row is untouched
   (`superseded_by == Y`'s id still, unchanged by the conflict
   resolution) — i.e. the ingestion version is preserved as a superseded
   row in the chain, not deleted or rewritten. CLI-level reporting
   confirmed separately: `_print_ingest_summary`/`profile_sync_cmd` in
   `src/cli.py` render each `SyncConflict` as a `⚠️` line including
   `new_uid`, `original_uid`, and `.note`, and
   `test_profile_sync_command_prints_summary_and_conflicts` asserts the
   conflict note text appears in CLI output.

3. **`sync` on an untouched file is a no-op — PASS.**
   `test_sync_profile_noop_when_file_matches_last_export` exports, then
   immediately syncs with no edits; asserts `result.no_op is True`, all
   four result lists empty, and — again — zero LLM calls. Mechanism
   confirmed by code read: `sync_profile` hashes the on-disk file and
   compares to `sync_state.last_export_hash` before doing any parsing,
   short-circuiting before any DB mutation.

4. **`ingest` on a dirty file refuses with a clear message — PASS.**
   `test_ingest_document_raises_dirty_profile_error_when_file_edited_since_export`
   and `test_seed_profile_raises_dirty_profile_error_when_file_edited_since_export`
   both export, hand-append an unsynced line to the file, then call
   `ingest_document`/`seed_profile` respectively and assert
   `DirtyProfileError` is raised **before** any LLM call
   (`mock.assert_not_called()`) and, for the ingest case, before any
   `documents` row is inserted (`doc_count == 0`). Message
   (`"profile.md has unsynced edits - run \`jobsearch profile sync\`
   first"`) is a clear, actionable string, confirmed by direct code read.
   CLI-level pass-through of `IngestionError` subclasses (which
   `DirtyProfileError` is) to a nonzero exit + the error text is covered
   generically by `test_profile_ingest_command_exits_nonzero_on_ingestion_error`
   (using `SuspiciousDocumentError` as the stand-in exception) and
   `test_profile_seed_command_exits_nonzero_on_ingestion_error` (using
   `DirtyProfileError` directly) — both exercise the same `except
   profile_service.IngestionError` branch the dirty-guard error would hit.

5. **Exported `profile.md` contains no superseded facts — PASS.**
   `test_export_profile_excludes_superseded_facts` inserts one active and
   one superseded fact, exports, and asserts the superseded fact's content
   and uid are both absent from the written file while the active fact's
   are present. Mechanism confirmed by code read:
   `_render_profile_markdown` sources exclusively from
   `repos.get_active_facts(conn)`, which filters `WHERE status = 'active'`
   at the SQL level — there is no code path by which a superseded fact
   could reach the rendered output.

6. **`profile show --history` on a fact with two superseded ancestors
   renders a cached one-line lineage (summarizer mocked), and the cache
   invalidates when the chain grows — PASS, with one test-coverage
   nuance noted above (not a code defect).**
   `test_get_profile_facts_with_history_renders_cached_summary_and_caches_llm_call`
   proves the caching mechanism (call twice, `mock.call_count == 1`) on a
   1-ancestor chain; `test_history_summary_cache_invalidates_when_chain_grows`
   grows a chain from 1 to 2 ancestors and proves the next call is a fresh
   miss (`call_count` goes from 1 to 2) that returns the newly-mocked
   text. `test_get_profile_facts_with_history_skips_llm_for_zero_ancestor_chain`
   additionally confirms the "chains with zero superseded ancestors get no
   summary call at all" clause (`mock.assert_not_called()`, zero
   `history_summaries` rows). As detailed above, no single test combines
   "exactly two ancestors" with "second call is a cache hit" in one
   scenario — the cache mechanism is generic (keyed only on
   `active_fact["id"]`, no ancestor-count branching) so this is very
   unlikely to hide a real bug, but it is a literal gap versus the
   acceptance text's exact wording, called out here for the first time.
   Haiku model assignment for this task confirmed via
   `config.MODEL_HISTORY_SUMMARY = MODEL_EXTRACT` (`"claude-haiku-4-5-20251001"`)
   wired into `MODEL_FOR_TASK["summarize_history"]`, matching §9's "one
   Haiku call per fact chain."

**All 6 acceptance criteria: PASS**, independently verified against actual
test bodies, direct code reads, and a live `uv run pytest -v` run (87
passed, 1 pre-existing unrelated skip), not merely trusted from the
implementer's own account. One is flagged PASS-with-a-noted-test-coverage-nuance
(#6) rather than an unqualified PASS, since the literal acceptance
sentence combines two conditions ("two ancestors" + "cached") that the
actual tests demonstrate separately rather than together — a
documentation/coverage nuance, not a functional failure.

**Scope creep: none found.** No `src/sources/`, no
`src/core/search_service.py`/`application_service.py`/`skills_service.py`/
`interview_service.py`, no `get_profile_context` as implemented code
(one forward-looking mention in this log's own prose, correctly scoped).
`src/db/migrations/001_initial.sql` has a literal empty diff — both
`sync_state` and `history_summaries` already existed from Phase 0, exactly
as required; no new migration file exists. The CLI gained exactly
`profile export`, `profile sync`, and the `--history` flag on the existing
`profile show`, matching the Phase 2 Changes bullet. The one out-of-scope
oddity found is documentation, not code: `pyproject.toml`'s
`markitdown[pdf,docx]` dependency change and the `MIN_INGEST_TEXT_LENGTH`/
`PROFILE_SECTIONS` config entries are Phase 1 work bundled into this same
uncommitted diff (see the git-history note at the top of this section) —
not new Phase 2 scope creep, just artifacts of Phase 1 never having been
committed separately.

**Genuine unresolved items (not deliberate, documented implementation
choices):**
- **`CLAUDE.md` was not updated for Phase 2** (detailed above) — it still
  tells a reader Phase 2 doesn't exist. This is the one real,
  unambiguous gap found by this audit; worth fixing before or alongside
  Phase 3 so the orientation doc doesn't compound its staleness further.
- The `history_summaries` cache test-coverage nuance (#6 above) — low
  severity, flagged for completeness rather than as a blocker.
- Everything else audited above (the bundled Phase 1 bugfix, the dirty-file
  guard's placement and scoping, the unconditional auto-export, the 3-way
  conflict mechanism and its "snapshot never had this uid" claim, the
  cache self-invalidation reasoning, `fact_sources` never written for
  manual edits, and the `connection.connect()` vs `get_connection()`
  distinction) checked out exactly as the implementer's own notes
  described, once independently verified against the actual code and
  tests rather than taken on account.
- The `.venv` corruption pattern (documented above, 4th/5th occurrence
  claimed for this phase) remains a genuinely open, unresolved
  environment issue per the recommendation in this log — this audit pass
  did not hit it, which doesn't resolve it.

**Log status: trustworthy for future phases to rely on**, with the above
corrections/additions now incorporated.
