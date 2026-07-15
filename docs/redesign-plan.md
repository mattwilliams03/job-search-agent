# Job-Search System Redesign: Stateful, SQLite-Backed, CLI-Orchestrated

A design + implementation plan intended to be executed incrementally by Claude Code inside the existing repo. Phases are ordered so each leaves the system working and tested.

---

## 1. Architecture Overview

### 1.1 New agent roster

| Agent | Fate of old agents | Responsibility |
|---|---|---|
| **Job Searcher** | Kept, extended | Multi-source search (Adzuna broad search + ATS company watchlist, with automatic board resolution — §10.3). Normalizes and dedupes into the `listings` table. |
| **Profile Curator** (new) | — | Ingests documents (via markitdown), extracts facts + style observations, merges them autonomously into the profile store, handles file↔DB sync reconciliation. |
| **Application Writer** (new) | Absorbs **Career Advisor** and **Interview Coach** | Given one `listing_id`: drafts tailored resume + cover letter (draft-only, never submits — enforced structurally: the system has no submission code path at all). Also owns on-demand interview prep for a specific job and interview-outcome capture. |
| **Skills Advisor** | Kept, made stateful | Reads profile + accumulated search/listing/application/interview-outcome history; produces compounding upskilling analysis. |

Career Advisor's useful content (resume/LinkedIn positioning) folds into the Application Writer's tailored drafts; its generic-tips role is retired. Interview Coach is retired as an always-on step and reborn as two on-demand commands (`interview-prep`, `interview-outcome`).

### 1.2 Layered structure (decoupling for a future conversational UI)

```
┌─────────────────────────────────────────────┐
│  CLI (src/cli.py, typer)   ← thin, swappable │
├─────────────────────────────────────────────┤
│  Core services (src/core/)                   │
│   profile_service.py   search_service.py     │
│   application_service.py skills_service.py   │
│   interview_service.py                       │
├──────────────┬──────────────┬───────────────┤
│ LLM layer    │  DB layer    │  Sources      │
│ src/llm.py   │ src/db/      │ src/sources/  │
│ src/prompts/ │ (schema,     │ (adzuna,      │
│ (per-task    │  migrations, │  greenhouse,  │
│  prompt+model│  repos)      │  lever, ashby)│
│  configs)    │              │               │
└──────────────┴──────────────┴───────────────┘
         SQLite (data/jobsearch.db)  ← system of record
         profile/profile.md          ← human-editable export
         outputs/                    ← rendered views, regenerable
```

Rule: the CLI layer contains zero business logic. Every command is a one-line call into a `src/core/` service function that takes plain Python arguments and returns plain data. A future conversational interface (or an MCP server exposing these services as tools — a natural fit) calls the same functions.

**CrewAI is removed entirely, in Phase 0.** (Decided.) Every LLM interaction goes through a thin `src/llm.py` wrapper over the Anthropic SDK; each "agent" becomes a prompt template + model assignment in `src/prompts/`, invoked by a core service. The existing 4-step sequential flow is mechanically ported to plain function calls in Phase 0 so the tool keeps working while later phases replace those functions one by one. `crewai` is deleted from `pyproject.toml` on day one — no transition period where both layers coexist.

### 1.3 Data flow

```
PDF/DOCX ──markitdown──▶ documents table ──Haiku extract──▶ candidate facts
                                              │ (quote-verified)
                                              ▼
                              Sonnet merge ──▶ profile_facts (+ fact_sources provenance)
                                              │
              profile/profile.md ◀──export───┘◀──sync (3-way merge)──

search --role X ──▶ Adzuna + ATS watchlist ──normalize/dedupe──▶ listings
apply <job_id> ──▶ profile context selection + listing + style notes
                   ──▶ resume.md / cover_letter.md drafts (+ cited fact IDs)
interview-prep <job_id> ──▶ prep doc tied to that listing + application
interview-outcome <job_id> ──▶ interview_outcomes ──┐
skills ──▶ reads profile + listings history + outcomes ◀┘ (direct query)
```

---

## 2. SQLite Schema

Use a single `data/jobsearch.db`, WAL mode, `PRAGMA foreign_keys=ON`. Migrations as ordered SQL files in `src/db/migrations/` applied by a tiny runner (track applied versions in a `schema_migrations` table — no need for Alembic at this scale).

```sql
-- ============ documents & provenance ============
CREATE TABLE documents (
  id            INTEGER PRIMARY KEY,
  filename      TEXT NOT NULL,
  doc_type      TEXT NOT NULL CHECK (doc_type IN ('resume','cover_letter','notes','other')),
  sha256        TEXT NOT NULL UNIQUE,          -- idempotent re-ingestion
  markdown_text TEXT NOT NULL,                 -- markitdown output, the LLM-facing form
  ingested_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============ profile ============
CREATE TABLE profile_facts (
  id           INTEGER PRIMARY KEY,
  uid          TEXT NOT NULL UNIQUE,           -- short stable id, e.g. 'f_a3k9' (used as file anchor)
  section      TEXT NOT NULL,                  -- 'summary','experience','skills','achievements',
                                               -- 'education','preferences','style'
  content      TEXT NOT NULL,                  -- one atomic fact/bullet, markdown
  origin       TEXT NOT NULL CHECK (origin IN ('seed','ingestion','manual')),
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded')),
  superseded_by INTEGER REFERENCES profile_facts(id),
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE fact_sources (                    -- which document(s) support which fact
  fact_id     INTEGER NOT NULL REFERENCES profile_facts(id),
  document_id INTEGER NOT NULL REFERENCES documents(id),
  quote       TEXT,                            -- verbatim span from the doc that supports the fact
  PRIMARY KEY (fact_id, document_id)
);

CREATE TABLE sync_state (                      -- profile.md export bookkeeping
  key   TEXT PRIMARY KEY,                      -- 'last_export_hash', 'last_export_at',
  value TEXT NOT NULL                          -- 'last_export_snapshot' (full text for 3-way merge)
);

-- ============ search & listings ============
CREATE TABLE searches (
  id        INTEGER PRIMARY KEY,
  role      TEXT NOT NULL,
  location  TEXT,
  sources   TEXT NOT NULL,                     -- json array: ["adzuna","greenhouse",...]
  run_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE listings (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,                -- 'adzuna','greenhouse','lever','ashby'
  external_id    TEXT NOT NULL,
  canonical_url  TEXT,
  company        TEXT NOT NULL,
  company_norm   TEXT NOT NULL,                -- lowercased, suffix-stripped, for dedup
  title          TEXT NOT NULL,
  title_norm     TEXT NOT NULL,
  location       TEXT,
  remote         INTEGER,                      -- nullable bool
  salary_min     REAL, salary_max REAL,
  description_md TEXT,
  posted_at      TEXT,
  first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
  dedupe_key     TEXT NOT NULL,                -- company_norm + '|' + title_norm + '|' + loc_norm
  UNIQUE (source, external_id)
);
CREATE INDEX idx_listings_dedupe ON listings(dedupe_key);

CREATE TABLE search_results (                  -- which search surfaced which listing
  search_id  INTEGER NOT NULL REFERENCES searches(id),
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  rank       INTEGER,
  PRIMARY KEY (search_id, listing_id)
);

CREATE TABLE watched_companies (               -- ATS watchlist
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  ats        TEXT NOT NULL CHECK (ats IN ('greenhouse','lever','ashby')),
  board_token TEXT NOT NULL,                   -- greenhouse board token / lever slug / ashby board name
  active     INTEGER NOT NULL DEFAULT 1,
  UNIQUE (ats, board_token)
);

-- ============ applications & interviews ============
CREATE TABLE applications (
  id              INTEGER PRIMARY KEY,
  listing_id      INTEGER NOT NULL REFERENCES listings(id),
  status          TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','submitted','rejected','interviewing','offer','withdrawn')),
  resume_md       TEXT,
  cover_letter_md TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (listing_id)                          -- one application record per listing
);

CREATE TABLE application_citations (           -- which profile facts fed which draft
  application_id INTEGER NOT NULL REFERENCES applications(id),
  fact_id        INTEGER NOT NULL REFERENCES profile_facts(id),
  used_in        TEXT NOT NULL CHECK (used_in IN ('resume','cover_letter','both')),
  PRIMARY KEY (application_id, fact_id)
);

CREATE TABLE interviews (
  id             INTEGER PRIMARY KEY,
  application_id INTEGER NOT NULL REFERENCES applications(id),
  round          INTEGER NOT NULL DEFAULT 1,
  prep_md        TEXT,                         -- generated prep doc
  scheduled_at   TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE interview_outcomes (
  id            INTEGER PRIMARY KEY,
  interview_id  INTEGER NOT NULL REFERENCES interviews(id),
  questions_md  TEXT,                          -- what was asked
  reflection_md TEXT,                          -- how it went / what I'd do differently
  weak_spots    TEXT,                          -- json array of short tags, LLM-extracted at log time
  result        TEXT CHECK (result IN ('advanced','rejected','pending','withdrew')),
  logged_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============ skills analyses ============
CREATE TABLE skills_analyses (
  id        INTEGER PRIMARY KEY,
  report_md TEXT NOT NULL,
  inputs    TEXT NOT NULL,                     -- json: which search/outcome IDs were considered
  run_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Linkage summary: `documents → fact_sources → profile_facts` (provenance); `searches → search_results → listings → applications → application_citations → profile_facts` (so any cover letter can be traced back both to its listing and to the source documents behind each claim); `applications → interviews → interview_outcomes` (feedback loop input for Skills Advisor).

---

## 3. Profile ↔ File Sync

### 3.1 File format

Export to `profile/profile.md`: readable markdown, one fact per bullet, each carrying a stable anchor as a trailing HTML comment (invisible in rendered view, survives casual editing):

```markdown
# Professional Profile
_Exported 2026-07-14 09:32 — edit freely, then run `jobsearch profile sync`_

## Experience
- Led migration of billing platform to event-driven architecture, cutting p95 latency 40% <!-- f_a3k9 -->
- Managed a team of 5 engineers across two product lines <!-- f_b7x2 -->

## Style
- Prefers concrete metrics over adjectives; short declarative sentences <!-- f_c1m4 -->
```

Markdown over YAML: you'll actually read and edit this; the anchor-comment trick gives YAML-grade addressability without YAML-grade friction.

### 3.2 The cycle

1. **Export** (`profile export`, also auto-run after every merge): render active facts grouped by section; store the full exported text in `sync_state.last_export_snapshot` plus its hash.
2. **Edit**: you change the file directly.
3. **Sync** (`profile sync`): a three-way merge between (a) the last-export snapshot, (b) the current file, (c) the current DB:
   - Bullet with anchor, text changed vs. snapshot → update that fact, `origin='manual'`.
   - Bullet with anchor, deleted from file → mark fact `superseded`.
   - Bullet without anchor (you added a line) → new fact, `origin='manual'`, anchor assigned on next export.
   - **Conflict** (fact changed in DB by ingestion since export AND edited in file): **manual wins**, the ingestion version is preserved as a `superseded` row pointing at the winner, and the sync output lists each conflict so you can eyeball it. This is safe precisely because of your no-auto-submit invariant.
4. Sync ends with a fresh export, so file and snapshot are aligned again.

If `profile sync` runs when the file hash matches the last export, it's a no-op. If ingestion runs while the file has unexported manual edits, ingestion warns and refuses until you sync (cheap way to avoid true three-way messes: only one "dirty" side at a time).

### 3.3 Superseded-fact history (decided: keep all, summarize, surface only on `--history`)

`profile/profile.md` and default `profile show` contain **active facts only** — history never bloats the file. Full superseded rows are retained in the DB forever. `profile show --history [--section X]` renders each active fact followed by a one-line summarized lineage, e.g.:

```
- Led migration of billing platform... <f_a3k9>
    ↳ evolved: "Worked on billing infrastructure" (resume_2023.pdf) → reworded with metrics (manual edit, 2026-03)
```

Lineage summaries are generated by one Haiku call per fact chain and cached in a small table so repeat calls are free; the cache invalidates whenever the chain gains a new superseded row:

```sql
CREATE TABLE history_summaries (
  fact_id     INTEGER PRIMARY KEY REFERENCES profile_facts(id), -- the active chain head
  summary     TEXT NOT NULL,
  computed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

(Add to `001_initial.sql`.) Chains with zero superseded ancestors get no summary call at all.

---

## 4. markitdown Ingestion Pipeline

`profile ingest resume_2024.pdf`:

1. **Convert** — `markitdown` → markdown text. No LLM. Reject/flag if output is suspiciously short (scanned-image PDF with no text layer).
2. **Dedupe** — sha256 against `documents`; skip if already ingested.
3. **Extract** — *first LLM call*, **Haiku 4.5**. Prompt: return JSON of candidate facts, each with `section`, `content`, and a **verbatim `quote`** copied from the source markdown, plus separate `style_observations`. Then a **code-level check verifies each quote actually appears in the document text** (normalized whitespace); facts with fabricated quotes are dropped. This mechanical grounding check is what makes the cheapest tier safe here — hallucinations can't survive it.
4. **Merge** — *second LLM call*, **Sonnet 5**. Input: candidate facts + current active facts in the same sections. Output per candidate: `new` / `duplicate_of: uid` / `updates: uid` / `conflicts_with: uid` (+ proposed resolution). Code applies the decisions: updates supersede old rows, provenance rows written to `fact_sources` with the quote.
5. **Auto-export** the profile file (per §3).

Style observations land as facts in the `style` section — they flow through the same merge/provenance machinery and are always included in Application Writer context.

---

## 5. Retrieval for Downstream Agents

At solo-user scale the profile will realistically sit at 5–20k tokens. Recommendation: **full injection now, section selection later, build the seam today.**

Every service gets its profile context through one function:

```python
def get_profile_context(purpose: str, listing: Listing | None = None) -> str
```

- **Phase 4 implementation:** returns the full active profile (always including `style`).
- **Trigger to upgrade:** when the rendered profile exceeds ~15k tokens, switch the internals to deterministic-first selection: always include `summary`, `skills`, `style`; select `experience`/`achievements` facts by keyword overlap with the listing description; only if that's ambiguous, one Haiku call to pick fact UIDs. No embeddings, no vector store — unjustified complexity for one person's profile.

Callers never know which strategy is live; nothing downstream changes when it flips.

---

## 6. CLI / Orchestration

`typer` app in `src/cli.py`, entry point `jobsearch` via `pyproject.toml [project.scripts]`.

```
jobsearch profile seed                      # interactive Q&A → initial facts
jobsearch profile ingest <file> [--type resume|cover_letter|notes]
jobsearch profile export
jobsearch profile sync
jobsearch profile show [--section X] [--history]   # --history appends summarized fact lineage (§3.3)

jobsearch watch add <company-name | careers-url>   # resolver auto-detects ATS + slug (§10.3);
                                                   #   --ats/--token remain as manual override
jobsearch watch list | remove <id>

jobsearch search --role "X" [--location "Y"] [--sources adzuna,ats] [-n 25]
jobsearch listings [--search <id>] [--company X]   # browse stored listings

jobsearch apply <listing_id>                # drafts resume + cover letter → application record + files
jobsearch app status [<id-or-company>] [<status>]  # see below — must be near-zero friction
jobsearch app list                          # history at a glance (see §8)

jobsearch interview-prep <listing_id> [--round N]
jobsearch interview-outcome <listing_id>    # opens $EDITOR with a template (§7)

jobsearch skills                            # stateful analysis
```

Replaces `main.py`'s single sequential run entirely. Each command → one `src/core/` service call.

**Status-update ergonomics (decided: user-maintained, so make it effortless).** Since the pipeline view is only as accurate as manual updates, `app status` is designed for zero friction:

- `jobsearch app status` with no args → numbered picker of non-terminal applications, then a status picker. Two keystrokes total.
- `jobsearch app status stripe submitted` → fuzzy company-name match; if ambiguous, shows the candidates.
- Accepts application ID, listing ID, or company substring interchangeably.
- `app list` visually separates terminal statuses and flags drafts older than 14 days as `draft (stale?)` so untracked drafts never masquerade as live applications.

---

## 7. Interview Feedback Loop Mechanics

**Direct query, no periodic summarization job.** A solo user accumulates outcomes in the dozens, not thousands — a background rollup is enterprise reflexes applied to a personal tool.

- **Capture UX (decided: editor-template).** `interview-outcome <listing_id>` writes a pre-filled markdown template to a temp file and opens `$EDITOR` (fall back to `EDITOR`-less environments by printing the temp path and waiting for Enter). Template sections: `## What was asked`, `## How it went`, `## What I'd do differently`, `## Result` (with the allowed values listed). On save/close, the file is parsed by heading; missing sections are tolerated (stored empty), and an unparseable `Result` prompts once for a picker. The human gets free-form editing; the model gets consistent structure — exactly the tradeoff that motivated this choice.
- At log time, one Haiku call extracts 2–5 short `weak_spots` tags (e.g. `"system-design depth"`, `"salary negotiation"`) from the free-text reflection, stored as JSON on the row. Tagging at write time keeps read time cheap.
- When `skills` runs, the service queries: active profile skills facts; skill demands across listings from the last N searches; all `weak_spots` + reflections from the last 20 outcomes; application statuses (rejection patterns). Recurring tags are counted in code and handed to Sonnet pre-aggregated ("`system-design depth` appeared in 4 of 6 outcomes"), so the model reasons over signal, not raw logs.
- Only if outcome volume ever blows the context budget: compress the oldest reflections with one Haiku pass at read time. Don't build this until it hurts.

---

## 8. Output Model

SQLite is the system of record; **everything under `outputs/` is a regenerable rendered view.**

```
profile/profile.md                          # living, editable (see §3)
outputs/applications/{yyyy-mm}-{company-slug}-{listing_id}/
    resume.md
    cover_letter.md
    interview-prep-r1.md
    meta.md          # listing link/snapshot, status, cited fact uids → source documents
outputs/skills/{date}-analysis.md
```

`meta.md` is the self-referential piece: it names the listing (URL + stored snapshot), the application status, and each cited profile fact with its `uid` and originating document — a draft claim traces to the resume it came from. `jobsearch app list` renders the at-a-glance table (company, title, status, drafted date, interview rounds, result) straight from the DB. The old 5-flat-files pattern is deleted.

---

## 9. Model Assignment

Confirmed current lineup and pricing: <cite index="5-1">Sonnet 5 (`claude-sonnet-5`) is at an introductory $2/$10 per MTok through August 31, 2026, then $3/$15</cite>, and <cite index="7-1">Fable 5 is priced at $10/$50 per MTok</cite>. Verify exact strings against https://platform.claude.com/docs/en/about-claude/models/overview before pinning in `src/config.py`.

| Task | Model | Why |
|---|---|---|
| Fact extraction from markitdown output | **Haiku 4.5** | Structured extraction with the quote-verification guardrail (§4) — mechanically checked, so cheapest tier is safe |
| Weak-spot tagging, listing normalization assist, future section selection | **Haiku 4.5** | Small classify/extract tasks |
| Profile merge & conflict reasoning | **Sonnet 5** | Real judgment (is this an update or a contradiction?), but bounded and low-stakes given manual-wins + provenance |
| Skills analysis, interview prep, resume tailoring | **Sonnet 5** | Daily-driver reasoning/writing; resume work is largely structural selection from existing facts |
| **Cover letter drafting** | **Opus 4.8** | The one output where voice-matching quality is the entire point and a human recruiter is the audience — the only task meeting your "highest intensity" bar |
| Fable 5 | **Not used** | 5× Sonnet's cost; nothing here needs frontier capability. Optionally expose as a `--model` override for an occasional whole-profile audit |

Make per-task model IDs config keys in `src/config.py` (e.g. `MODEL_EXTRACT`, `MODEL_MERGE`, `MODEL_COVER_LETTER`) so escalating/downgrading any single task is a one-line change. If Opus on cover letters feels unnecessary after a few real drafts, drop it to Sonnet 5 — that's a config edit, not a design decision.

---

## 10. Job Source Expansion

### Recommendation

**Add the ATS watchlist as a second source type. Skip the paid aggregator for now.**

- **Adzuna stays** as the broad keyword-search layer — it's the only free cross-company search you have.
- **ATS watchlist (Greenhouse/Lever/Ashby)** is high value at near-zero cost: free, unauthenticated, JSON, canonical (fresher and cleaner than aggregator copies), and it precisely covers Adzuna's blind spot for companies you actually care about. The "must know which companies to query" limitation is a feature for a personal tool: your watchlist *is* your target list. Endpoints:
  - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`
  - Lever: `https://api.lever.co/v0/postings/{company}?mode=json`
  - Ashby: `https://api.ashbyhq.com/posting-api/job-board/{board_name}`
  (Confirm each shape at implementation time; keep one thin client per ATS in `src/sources/`.)
- **Paid aggregator: not yet.** For a single user, a monthly subscription buys mostly redundant coverage of what Adzuna + your watchlist already surface. Revisit only if, after a month or two of logged searches, you can point at concrete listings you missed (the `searches`/`listings` history makes that measurable — a nice side effect of statefulness).
- Indeed: excluded per your constraint; no scraping.

### Normalization & dedup

Every source client returns the same `NormalizedListing` dataclass; only `src/sources/*` knows source-specific JSON. Dedup on upsert into `listings`:

1. Exact: `(source, external_id)` unique constraint → update `last_seen_at`.
2. Cross-source: match on `dedupe_key` (normalized company + normalized title + normalized location). On collision between an Adzuna row and an ATS row for the same job, **the ATS record wins** (canonical URL, fuller description); the Adzuna duplicate is merged into it rather than inserted. Keep matching deterministic (string normalization: lowercase, strip legal suffixes like "Inc.", collapse seniority synonyms via a small lookup table). Don't spend an LLM call on dedup unless real-world false negatives prove annoying — and if they do, a Haiku "same job? yes/no" tiebreak on near-misses is the ceiling.

### 10.3 Watchlist bootstrapping: automatic ATS + slug resolution

Research into how commercial ATS aggregators handle discovery shows three techniques in production use, in ascending cost:

1. **URL parsing.** The slug is embedded in every ATS careers URL — <cite index="12-1">`boards.greenhouse.io/{slug}`, `jobs.lever.co/{slug}`, `jobs.ashbyhq.com/{slug}`</cite> (Greenhouse also uses the newer `job-boards.greenhouse.io/{token}` domain). Deterministic and free when the user can paste a URL.
2. **Probe-and-detect.** <cite index="16-1">Several aggregators accept just a company name and auto-detect the ATS and slug</cite> by <cite index="19-1">probing each ATS's public job-board endpoint with candidate slugs and seeing which responds</cite>. One cheap GET per candidate per ATS; a 200 with a valid jobs payload is a hit. This works because slugs are usually the obvious normalization of the company name.
3. **Bulk harvesting.** Large aggregators build company directories by <cite index="20-1">scanning web-crawl archives (Common Crawl CDX) for URLs matching ATS domain patterns and regex-extracting slugs — yielding ~95,000 company identifiers</cite>. Enterprise machinery; explicitly out of scope for a personal watchlist.

**Design — `watch add <company-name | url>` resolver (`src/sources/resolver.py`):**

1. If the argument parses as a URL matching a known ATS pattern (including `job-boards.greenhouse.io`), extract slug directly. Done.
2. Otherwise generate candidate slugs from the name: lowercase-stripped (`"Acme Corp"` → `acmecorp`), hyphenated (`acme-corp`), and first-word (`acme`). Probe all three ATS endpoints for each candidate concurrently (≤9 fast GETs).
3. If no probe hits, fetch the company's website careers page (found via one web search or `https://{domain}/careers`) and regex the HTML for embedded ATS URLs — the same auto-detect fallback commercial tools use, and it catches non-obvious slugs.
4. **Always confirm before saving:** display the resolved ATS, slug, and 3 sample live job titles from the board; the user approves. This guards the known failure mode where a slug collides with a *different* company's board — a wrong watchlist entry would silently pollute every future search.
5. On later searches, a board that starts returning 404/empty is flagged (<cite index="14-1">the most common cause is that the company migrated to a different ATS</cite>) rather than silently skipped, with a hint to re-run `watch add`.

Manual `--ats`/`--token` flags remain as an escape hatch when resolution fails.

---

## 11. Phased Implementation Roadmap

Each phase = one discrete Claude Code work unit: branch, implement, tests green, done. File paths refer to the existing repo layout.

### Phase 0 — Skeleton: DB layer, core/CLI split, **CrewAI removal**
**Changes:** new `src/db/` (`connection.py`, `migrations/001_initial.sql` with the full §2 schema incl. `history_summaries`, `migrate.py` runner, `repos.py` with typed helpers); new `src/core/` package; new `src/llm.py` (Anthropic SDK wrapper with per-task model lookup from `src/config.py`) and `src/prompts/`; **delete `crewai` from `pyproject.toml`**, delete `src/agents.py`/`src/tasks.py`, and mechanically port the existing 4-step sequential flow into `src/core/legacy_run.py` as plain functions calling `src/llm.py` (same prompts, same behavior — this is a transliteration, not a redesign, and it gets deleted piecewise in Phases 3–6); new `src/cli.py` with `typer`: `jobsearch db migrate`, `jobsearch db status`, and `jobsearch run` (the ported legacy flow); `pyproject.toml` gains `typer`, `markitdown` deps and the `jobsearch` script entry.
**Acceptance:** `uv run jobsearch db migrate` creates all tables; running twice is idempotent; schema tests (tables/FKs/CHECK constraints); `jobsearch run` reproduces the old flow's outputs with LLM calls mocked (snapshot test on prompt assembly); `grep -ri crewai` across the repo returns nothing; `uv sync` succeeds without the dependency.

### Phase 1 — Ingestion & profile facts
**Changes:** `src/core/profile_service.py` (`ingest_document`, `seed_profile`); extraction (Haiku) + merge (Sonnet) prompts in `src/prompts/`; quote-verification check; CLI `profile ingest`, `profile seed`, `profile show`. One-bullet-one-fact granularity is locked in for the extraction prompt (decided; revisit only if fragmentation proves real).
**Acceptance:** ingesting a sample PDF (fixture) creates a `documents` row and ≥N `profile_facts` with `fact_sources` provenance; re-ingesting the same file is a no-op (sha256); a unit test feeds the extractor a mocked LLM response containing a fabricated quote and asserts that fact is dropped; merge test: ingest a second fixture containing an updated job title → old fact `superseded`, new fact active, both linked.

### Phase 2 — Profile export / edit / sync
**Changes:** `profile_service.export_profile`, `sync_profile` (three-way merge per §3); `sync_state` bookkeeping; `profile show --history` with Haiku lineage summaries cached in `history_summaries` (§3.3); CLI `profile export`, `profile sync`; ingestion gains the dirty-file guard.
**Acceptance:** export → hand-edit a bullet + delete one + add one → `sync` updates/supersedes/creates the right facts with `origin='manual'`; conflict test (DB fact changed post-export + same bullet edited in file) → manual wins, superseded row preserved, conflict reported in output; `sync` on an untouched file is a no-op; `ingest` on a dirty file refuses with a clear message; exported `profile.md` contains no superseded facts; `profile show --history` on a fact with two superseded ancestors renders a cached one-line lineage (summarizer mocked), and the cache invalidates when the chain grows.

### Phase 3 — Stateful multi-source search
**Changes:** `src/sources/` package: move Adzuna logic out of `src/tools.py` into `sources/adzuna.py`; add `greenhouse.py`, `lever.py`, `ashby.py`; shared `NormalizedListing` + `normalize.py` (dedupe-key logic); `src/sources/resolver.py` implementing the §10.3 pipeline (URL parse → slug probing → careers-page regex fallback → confirm-before-save); `src/core/search_service.py` (run search → upsert listings → record `searches`/`search_results`); dead-board flagging on search; CLI `search`, `listings`, `watch add/list/remove`; delete the search leg of `legacy_run.py`.
**Acceptance:** search against mocked Adzuna + mocked Greenhouse responses persists listings; same external job twice → one row, `last_seen_at` updated; cross-source duplicate fixture (same job via Adzuna and Lever) → single row, ATS fields win; resolver unit tests: pasted Greenhouse/Lever/Ashby URLs (incl. `job-boards.greenhouse.io`) → correct (ats, slug); company name whose slug variant hits a mocked probe → resolved with confirmation prompt; name with no probe hit → careers-page HTML fixture regex fallback resolves; nothing is written to `watched_companies` without confirmation; a watched board returning 404 during search surfaces a migration warning, not a silent skip; live smoke test against one real Greenhouse board behind a `--live` pytest marker.

### Phase 4 — Application Writer
**Changes:** `src/core/application_service.py` (`draft_application(listing_id)`); `get_profile_context()` seam (§5, full-injection implementation); cover-letter prompt (Opus 4.8) and resume prompt (Sonnet 5) that must return cited fact UIDs alongside the drafts; writes `applications`, `application_citations`, renders `outputs/applications/...` incl. `meta.md`; CLI `apply`, `app list`, `app status` with the §6 ergonomics (no-arg picker, fuzzy company match, ID-or-name flexibility, stale-draft flagging); delete the Career Advisor + Interview Coach legs of `legacy_run.py`, the old flat-file output path, and `main.py`.
**Acceptance:** `apply <id>` on a stored listing produces both drafts on disk and in DB; every citation row references an active fact; `meta.md` names listing URL + cited facts + their source documents; grep-level test asserting no code path performs an HTTP POST to any job board (the no-submit invariant is structural); `app status` with no args launches the picker, `app status stri submitted` fuzzy-matches "Stripe" and updates it, ambiguous matches list candidates instead of guessing; `app list` renders the history table with stale drafts flagged; style facts verifiably present in the prompt context (unit test on prompt assembly).

### Phase 5 — Interview prep & outcome capture
**Changes:** `src/core/interview_service.py`; prep prompt (Sonnet 5) conditioned on listing + application drafts + profile; `interview-outcome` editor-template flow per §7 ($EDITOR launch, heading-based parse, no-editor fallback); Haiku weak-spot tagging at log time; CLI `interview-prep`, `interview-outcome`.
**Acceptance:** `interview-prep <listing_id>` creates an `interviews` row + prep file scoped to that job (test asserts company/role names appear; no generic question bank); `interview-outcome` writes the template, and parsing tests cover: fully filled template, template with a missing section (tolerated, stored empty), invalid `Result` value (falls back to a picker), user saves the template unchanged (aborts without writing a row); stored reflection produces `weak_spots` JSON (tagger mocked); second `interview-prep --round 2` after a logged round-1 outcome includes round-1 reflection in its prompt context.

### Phase 6 — Stateful Skills Advisor + feedback loop
**Changes:** rewrite `src/core/skills_service.py` per §7 (queries + code-side tag aggregation + Sonnet analysis); persist to `skills_analyses`; render `outputs/skills/`; CLI `skills`; delete the last leg of `legacy_run.py` (the stateless Skills Advisor), which removes the file — and the `jobsearch run` shim — entirely.
**Acceptance:** with fixtures of 3 searches (overlapping skill demands) + 4 outcomes (repeated weak-spot tag), the assembled prompt contains the pre-aggregated recurrence counts (unit test on prompt assembly, LLM mocked); `skills_analyses.inputs` records exactly which search/outcome IDs were used; two consecutive runs with new outcomes in between produce different input sets.

### Phase 7 — Polish
Retire remaining dead code; README documenting the CLI; optional `jobsearch report` (single markdown dashboard of pipeline status); `--model` override flag. Acceptance: fresh-clone → `uv sync` → `jobsearch db migrate` → full workflow succeeds end-to-end per README.

---

## 12. Design Decisions Log (all resolved — no blockers remain)

| # | Question | Decision |
|---|---|---|
| 1 | CrewAI | **Dropped entirely, at Phase 0.** Plain Anthropic SDK via `src/llm.py`; existing flow mechanically ported to `legacy_run.py`, then dismantled piecewise through Phase 6. |
| 2 | Fact granularity | **One bullet = one fact.** Locked into the Phase 1 extraction prompt. Revisit only if fragmentation is demonstrably annoying in practice. |
| 3 | Superseded-fact retention | **Keep all history in DB; never in the default file/view.** `profile show --history` renders Haiku-summarized one-line lineages, cached in `history_summaries` (§3.3). |
| 4 | Status tracking | **User-maintained, engineered for zero friction:** no-arg picker, fuzzy company match, ID-or-name input, stale-draft flagging in `app list` (§6, Phase 4). |
| 5 | Outcome capture UX | **Editor-template route.** `$EDITOR` opens a pre-filled markdown template; heading-based parsing gives the model consistency, free-form editing gives the human flexibility (§7, Phase 5). |
| 6 | Watchlist bootstrapping | **Auto-resolver, per aggregator practice research:** URL parsing → candidate-slug probing across all three ATS endpoints → careers-page regex fallback, always with confirm-before-save and dead-board flagging (§10.3, Phase 3). |

Two residual micro-decisions, both deferrable to their phase without blocking anything:

- **Stale-draft threshold** (default 14 days in §6) — pick a number you'd actually act on; make it a config value.
- **No-`$EDITOR` fallback** for `interview-outcome` — the plan prints the temp-file path and waits; if you always work in a terminal with `$EDITOR` set, Claude Code can skip the fallback path.
