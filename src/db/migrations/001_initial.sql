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

CREATE TABLE history_summaries (
  fact_id     INTEGER PRIMARY KEY REFERENCES profile_facts(id), -- the active chain head
  summary     TEXT NOT NULL,
  computed_at TEXT NOT NULL DEFAULT (datetime('now'))
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
