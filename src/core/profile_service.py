"""
Profile ingestion, export, and sync: documents -> extracted candidate
facts -> merged, provenance-tracked profile_facts -> human-editable
profile.md, and back.

Two ingestion entry points share one pipeline:
- ingest_document(): a markitdown-converted file (resume, cover letter, notes).
- seed_profile(): an interactive Q&A's answers, synthesized into one
  "document" so it flows through the exact same extract/merge/apply
  machinery (decided this session, for consistency over a simpler local
  text split).

Ingestion pipeline (per docs/redesign-plan.md §4): dirty-file guard ->
convert -> dedupe by sha256 -> extract candidate facts (Haiku,
quote-grounded) -> merge against current active facts (Sonnet) -> apply
decisions (insert/supersede/link provenance) -> auto-export.

Export/sync pipeline (per §3): export_profile() renders all active facts
to profile.md and records a snapshot in sync_state; sync_profile() reads
a hand-edited profile.md back, three-way-merges it against the last
snapshot and the current DB, applies the resulting insert/supersede
decisions (all origin='manual'), then re-exports. profile show --history
renders each active fact's superseded-ancestor lineage as one
Haiku-summarized line, cached in history_summaries.
"""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from markitdown import MarkItDown

from src import config, llm
from src.db import connection
from src.db import repos
from src.prompts import extraction, merge, history_summary


class IngestionError(Exception):
    """Base class for ingestion pipeline failures."""


class SuspiciousDocumentError(IngestionError):
    """Converted document text is too short to plausibly contain real content."""


class LLMResponseError(IngestionError):
    """An LLM response could not be parsed as the expected JSON shape."""


class DirtyProfileError(IngestionError):
    """profile.md has unsynced manual edits; run `profile sync` first."""


class ProfileNotExportedError(IngestionError):
    """No exported profile.md exists yet; run `profile export` first."""


@dataclass
class CandidateFact:
    section: str
    content: str
    quote: str


@dataclass
class MergeDecision:
    candidate_index: int
    decision: str  # "new" | "duplicate_of" | "updates" | "conflicts_with"
    uid: Optional[str] = None
    resolution: Optional[str] = None


@dataclass
class ApplyResult:
    new: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)
    conflicts: List[Tuple[str, str, str]] = field(default_factory=list)


@dataclass
class IngestResult:
    document_id: Optional[int]
    skipped: bool
    apply: Optional[ApplyResult]


@dataclass
class ExportResult:
    path: Path
    fact_count: int
    hash: str


@dataclass
class SyncConflict:
    new_uid: str
    original_uid: str
    descendant_uid: Optional[str]
    note: str


@dataclass
class SyncResult:
    new: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    conflicts: List[SyncConflict] = field(default_factory=list)
    no_op: bool = False


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def verify_quote(quote: str, source_text: str) -> bool:
    """
    Whitespace-normalized substring check. An empty/blank quote is always
    unverifiable (treated the same as a fabricated one).
    """
    if not quote or not quote.strip():
        return False
    return _normalize_whitespace(quote) in _normalize_whitespace(source_text)


def _parse_json_response(raw: str) -> dict:
    """Parse an LLM response as JSON, tolerating a ```json ... ``` fence."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMResponseError(f"could not parse LLM JSON response: {e}") from e


def _convert_to_markdown(path: Path) -> str:
    """Thin markitdown wrapper, isolated so tests can monkeypatch it directly."""
    return MarkItDown().convert(str(path)).markdown


def extract_facts(source_text: str) -> List[CandidateFact]:
    """
    Run the extraction step: one Haiku call, then drop any candidate
    whose quote can't be verified against source_text.
    """
    raw = llm.complete(
        task="extract_facts",
        system=extraction.SYSTEM,
        user=extraction.build_user_prompt(source_text),
    )
    data = _parse_json_response(raw)

    candidates = [
        CandidateFact(section=f["section"], content=f["content"], quote=f["quote"])
        for f in data.get("facts", [])
    ]
    candidates += [
        CandidateFact(section="style", content=s["content"], quote=s["quote"])
        for s in data.get("style_observations", [])
    ]

    verified = []
    for c in candidates:
        if verify_quote(c.quote, source_text):
            verified.append(c)
        else:
            print(f"dropped fact with unverifiable quote: {c.content!r}", file=sys.stderr)

    return verified


def merge_facts(conn, candidates: List[CandidateFact]) -> List[MergeDecision]:
    """
    Run the merge step: one batched Sonnet call reconciling all
    candidates against current active facts in the same sections.
    """
    if not candidates:
        return []

    sections = sorted({c.section for c in candidates})
    existing = repos.get_active_facts(conn, sections=sections)
    existing_uids = {f["uid"] for f in existing}

    raw = llm.complete(
        task="merge_facts",
        system=merge.SYSTEM,
        user=merge.build_user_prompt(candidates, existing),
    )
    data = _parse_json_response(raw)

    by_index: Dict[int, MergeDecision] = {}
    for d in data.get("decisions", []):
        idx = d.get("candidate_index")
        if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
            print(f"ignoring merge decision with invalid candidate_index: {d!r}", file=sys.stderr)
            continue

        decision = d.get("decision", "new")
        uid = d.get("uid")
        resolution = d.get("resolution")

        if decision in ("duplicate_of", "updates", "conflicts_with") and uid not in existing_uids:
            print(
                f"merge decision for candidate {idx} referenced unknown uid {uid!r}; "
                "treating as new",
                file=sys.stderr,
            )
            decision, uid, resolution = "new", None, None

        by_index[idx] = MergeDecision(candidate_index=idx, decision=decision, uid=uid, resolution=resolution)

    for idx in range(len(candidates)):
        if idx not in by_index:
            print(f"merge response missing candidate {idx}; treating as new", file=sys.stderr)
            by_index[idx] = MergeDecision(candidate_index=idx, decision="new")

    return [by_index[i] for i in range(len(candidates))]


def apply_merge_decisions(
    conn,
    *,
    document_id: int,
    candidates: List[CandidateFact],
    decisions: List[MergeDecision],
    origin: str,
) -> ApplyResult:
    """Apply each merge decision: insert/supersede rows and record provenance."""
    result = ApplyResult()

    for c, d in zip(candidates, decisions):
        if d.decision == "duplicate_of" and d.uid:
            existing = repos.get_fact_by_uid(conn, d.uid)
            if existing is not None and existing["status"] == "active":
                repos.insert_fact_source(conn, fact_id=existing["id"], document_id=document_id, quote=c.quote)
                result.duplicates.append(existing["uid"])
                continue
            # Fall through to "new" if the uid vanished or was already
            # superseded (e.g. an earlier decision in this same batch
            # already superseded it) between merge and apply.

        if d.decision == "updates" and d.uid:
            existing = repos.get_fact_by_uid(conn, d.uid)
            if existing is not None and existing["status"] == "active":
                new_fact = repos.insert_profile_fact(conn, section=c.section, content=c.content, origin=origin)
                repos.insert_fact_source(conn, fact_id=new_fact["id"], document_id=document_id, quote=c.quote)
                repos.supersede_fact(conn, old_fact_id=existing["id"], new_fact_id=new_fact["id"])
                result.updated.append(new_fact["uid"])
                continue

        if d.decision == "conflicts_with" and d.uid:
            existing = repos.get_fact_by_uid(conn, d.uid)
            if existing is not None and existing["status"] == "active":
                # Phase 1 has no manual-resolution concept (that's Phase
                # 2's sync_profile) - keep both facts active rather than
                # dropping either, and surface the conflict to the caller.
                new_fact = repos.insert_profile_fact(conn, section=c.section, content=c.content, origin=origin)
                repos.insert_fact_source(conn, fact_id=new_fact["id"], document_id=document_id, quote=c.quote)
                result.conflicts.append((new_fact["uid"], existing["uid"], d.resolution or ""))
                continue

        # "new", or a fallback for any decision whose referenced uid
        # couldn't be resolved (vanished or already superseded).
        new_fact = repos.insert_profile_fact(conn, section=c.section, content=c.content, origin=origin)
        repos.insert_fact_source(conn, fact_id=new_fact["id"], document_id=document_id, quote=c.quote)
        result.new.append(new_fact["uid"])

    return result


# =============================================================================
# EXPORT / SYNC (§3)
# =============================================================================

_ANCHOR_RE = re.compile(r"^-\s+(.*?)(?:\s*<!--\s*(f_[a-z0-9]{4})\s*-->)?\s*$")
_SECTION_HEADER_RE = re.compile(r"^##\s+(.+)$")


def _export_timestamp() -> str:
    """Isolated for testability, mirroring _convert_to_markdown."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _render_profile_markdown(conn) -> str:
    """Render all active facts as profile.md content (§3.1's format)."""
    facts = repos.get_active_facts(conn)
    by_section: Dict[str, List] = {}
    for f in facts:
        by_section.setdefault(f["section"], []).append(f)

    lines = [
        "# Professional Profile",
        f"_Exported {_export_timestamp()} — edit freely, then run `jobsearch profile sync`_",
        "",
    ]
    for section in config.PROFILE_SECTIONS:
        if section not in by_section:
            continue
        lines.append(f"## {section.title()}")
        for f in by_section[section]:
            lines.append(f"- {f['content']} <!-- {f['uid']} -->")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _parse_profile_markdown(text: str) -> List[Tuple[str, str, Optional[str]]]:
    """
    Parse profile.md content into (section, content, uid_or_None) tuples,
    in file order. Used for both the stored snapshot and the live file.
    """
    bullets = []
    current_section: Optional[str] = None

    for line in text.splitlines():
        header_match = _SECTION_HEADER_RE.match(line)
        if header_match:
            candidate = header_match.group(1).strip().lower()
            if candidate in config.PROFILE_SECTIONS:
                current_section = candidate
            else:
                print(
                    f"unrecognized section header {line!r}; ignoring bullets "
                    "until the next valid header",
                    file=sys.stderr,
                )
                current_section = None
            continue

        if not line.startswith("- "):
            continue

        bullet_match = _ANCHOR_RE.match(line)
        if not bullet_match or current_section is None:
            continue

        content, uid = bullet_match.groups()
        bullets.append((current_section, content.strip(), uid))

    return bullets


def _check_not_dirty(conn) -> None:
    """
    Refuse to proceed if profile.md has unsynced manual edits. A no-op
    if nothing has ever been exported (Phase 1 status quo, unchanged) -
    there's nothing to be dirty relative to.
    """
    if not config.PROFILE_MD_PATH.exists():
        return

    current_text = config.PROFILE_MD_PATH.read_text(encoding="utf-8")
    current_hash = _hash_text(current_text)
    last_hash = repos.get_sync_state(conn, "last_export_hash")

    if last_hash is not None and current_hash != last_hash:
        raise DirtyProfileError(
            "profile.md has unsynced edits - run `jobsearch profile sync` first"
        )


def _export_profile(conn) -> ExportResult:
    """Render, persist snapshot bookkeeping, and write profile.md."""
    text = _render_profile_markdown(conn)
    text_hash = _hash_text(text)
    fact_count = len(repos.get_active_facts(conn))

    repos.set_sync_state(conn, "last_export_hash", text_hash)
    repos.set_sync_state(conn, "last_export_at", datetime.now().isoformat())
    repos.set_sync_state(conn, "last_export_snapshot", text)

    # The DB commit and this file write are two different storage
    # engines, so true cross-system atomicity isn't achievable here - a
    # rare disk-full/permissions failure mid-write still rolls back the
    # whole transaction (facts included), an acceptable risk for a
    # personal, single-user tool.
    config.PROFILE_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.PROFILE_MD_PATH.with_suffix(".md.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, config.PROFILE_MD_PATH)

    return ExportResult(path=config.PROFILE_MD_PATH, fact_count=fact_count, hash=text_hash)


def _resolve_ancestor_context(conn, active_fact, ancestors) -> List[dict]:
    """Build the oldest-first chain list for history_summary.build_user_prompt."""
    chain = []
    for fact in list(ancestors) + [active_fact]:
        doc = repos.get_fact_source_document(conn, fact["id"])
        chain.append({
            "content": fact["content"],
            "origin": fact["origin"],
            "document_filename": doc["filename"] if doc else None,
            "date": fact["created_at"][:7],  # "YYYY-MM-DD HH:MM:SS" -> "YYYY-MM"
        })
    return chain


def export_profile(*, db_path: Optional[Union[Path, str]] = None) -> ExportResult:
    """Render the active profile to profile.md and update sync bookkeeping."""
    with connection.connect(db_path) as conn:
        return _export_profile(conn)


def _process_deletions(conn, result: SyncResult, snapshot_uids: set, file_uids: set) -> None:
    for uid in snapshot_uids - file_uids:
        fact = repos.get_fact_by_uid(conn, uid)
        if fact is not None and fact["status"] == "active":
            repos.supersede_fact(conn, old_fact_id=fact["id"], new_fact_id=None)
            result.deleted.append(uid)


def _process_anchored_bullet(conn, result: SyncResult, *, section: str, content: str, uid: str) -> None:
    fact = repos.get_fact_by_uid(conn, uid)

    if fact is None:
        print(f"unknown anchor {uid!r} in profile.md; treating as a new fact", file=sys.stderr)
        new_fact = repos.insert_profile_fact(conn, section=section, content=content, origin="manual")
        result.new.append(new_fact["uid"])
        return

    if fact["status"] == "active":
        if _normalize_whitespace(content) == _normalize_whitespace(fact["content"]):
            return  # unchanged
        new_fact = repos.insert_profile_fact(conn, section=section, content=content, origin="manual")
        repos.supersede_fact(conn, old_fact_id=fact["id"], new_fact_id=new_fact["id"])
        result.updated.append(new_fact["uid"])
        return

    # fact is already superseded: the DB moved on since this anchor's
    # version was current. Compare against the fact's OWN stored content
    # (not the snapshot, which never had this uid - it wasn't active at
    # export time).
    if _normalize_whitespace(content) == _normalize_whitespace(fact["content"]):
        return  # stale-but-untouched carryover; safe to drop silently

    descendant = repos.resolve_live_descendant(conn, fact["id"])
    new_fact = repos.insert_profile_fact(conn, section=section, content=content, origin="manual")
    if descendant is not None:
        repos.supersede_fact(conn, old_fact_id=descendant["id"], new_fact_id=new_fact["id"])
    result.conflicts.append(SyncConflict(
        new_uid=new_fact["uid"],
        original_uid=uid,
        descendant_uid=descendant["uid"] if descendant is not None else None,
        note="profile.md was edited after the underlying fact changed via ingestion; the manual edit won",
    ))


def _process_unanchored_bullet(conn, result: SyncResult, *, section: str, content: str) -> None:
    new_fact = repos.insert_profile_fact(conn, section=section, content=content, origin="manual")
    result.new.append(new_fact["uid"])


def sync_profile(*, db_path: Optional[Union[Path, str]] = None) -> SyncResult:
    """
    Three-way merge profile.md's manual edits back into the DB (§3.2):
    snapshot vs. current file vs. current DB, resolved bullet by bullet,
    then re-exported so file and snapshot are aligned again. Makes no
    LLM calls.
    """
    if not config.PROFILE_MD_PATH.exists():
        raise ProfileNotExportedError("no exported profile - run `jobsearch profile export` first")

    file_text = config.PROFILE_MD_PATH.read_text(encoding="utf-8")

    with connection.connect(db_path) as conn:
        last_hash = repos.get_sync_state(conn, "last_export_hash")
        if last_hash is not None and _hash_text(file_text) == last_hash:
            return SyncResult(no_op=True)

        snapshot_text = repos.get_sync_state(conn, "last_export_snapshot") or ""
        snapshot_bullets = _parse_profile_markdown(snapshot_text)
        snapshot_uids = {uid for _, _, uid in snapshot_bullets if uid}

        file_bullets = _parse_profile_markdown(file_text)
        file_anchored_uids = {uid for _, _, uid in file_bullets if uid}

        result = SyncResult()

        _process_deletions(conn, result, snapshot_uids, file_anchored_uids)

        # Anchored bullets are processed one at a time, each via a fresh
        # DB lookup (never a batch pre-fetch) - this is what makes a
        # duplicated/self-conflicting anchor within one file naturally
        # route into the same conflict-resolution branch as a genuine
        # cross-run DB race, with no special-case code.
        for section, content, uid in file_bullets:
            if uid is not None:
                _process_anchored_bullet(conn, result, section=section, content=content, uid=uid)

        for section, content, uid in file_bullets:
            if uid is None:
                _process_unanchored_bullet(conn, result, section=section, content=content)

        # Unconditional: even a pass that only dropped a stale carryover
        # bullet (zero DB mutations) still needs the file re-aligned.
        _export_profile(conn)

        return result


def get_fact_history_summary(conn, active_fact) -> Optional[str]:
    """
    One-line summarized lineage for a fact's superseded ancestors,
    cached in history_summaries. Chains with zero ancestors get no LLM
    call and no cache entry (§3.3).

    The cache needs no explicit staleness tracking: an active fact's id
    never changes, and a chain can only grow by superseding *that
    specific active fact* into a new one - which changes which fact_id
    you'd query, producing a natural cache-miss on the new id.
    """
    ancestors = repos.get_superseded_ancestors(conn, active_fact["id"])
    if not ancestors:
        return None

    cached = repos.get_history_summary(conn, active_fact["id"])
    if cached is not None:
        return cached["summary"]

    chain = _resolve_ancestor_context(conn, active_fact, ancestors)
    raw = llm.complete(
        task="summarize_history",
        system=history_summary.SYSTEM,
        user=history_summary.build_user_prompt(chain),
    )
    summary = raw.strip()
    repos.set_history_summary(conn, fact_id=active_fact["id"], summary=summary)
    return summary


def get_profile_facts_with_history(
    sections: Optional[List[str]] = None, *, db_path: Optional[Union[Path, str]] = None
) -> List[dict]:
    """
    Active facts, each paired with its cached/computed history summary.

    Uses connection.connect() (commit-on-success), not get_connection()
    + bare close - this writes history_summaries cache rows as a side
    effect, which a non-committing close would silently discard.
    """
    with connection.connect(db_path) as conn:
        facts = repos.get_active_facts(conn, sections=sections)
        return [{"fact": f, "history_summary": get_fact_history_summary(conn, f)} for f in facts]


# =============================================================================
# INGESTION ENTRY POINTS
# =============================================================================

def _ingest_markdown(conn, *, filename: str, doc_type: str, markdown_text: str, origin: str) -> IngestResult:
    """Shared pipeline body for ingest_document and seed_profile."""
    _check_not_dirty(conn)

    sha256 = _hash_text(markdown_text)

    existing_doc = repos.get_document_by_sha256(conn, sha256)
    if existing_doc is not None:
        return IngestResult(document_id=existing_doc["id"], skipped=True, apply=None)

    doc = repos.insert_document(conn, filename=filename, doc_type=doc_type, sha256=sha256, markdown_text=markdown_text)

    candidates = extract_facts(markdown_text)
    decisions = merge_facts(conn, candidates)
    apply_result = apply_merge_decisions(
        conn, document_id=doc["id"], candidates=candidates, decisions=decisions, origin=origin
    )

    # Closes the loop deferred in Phase 1 (§4 step 5) - export_profile
    # didn't exist yet then. Unconditional on any successful,
    # non-deduped run.
    _export_profile(conn)

    return IngestResult(document_id=doc["id"], skipped=False, apply=apply_result)


def ingest_document(file_path: str, doc_type: str, *, db_path: Optional[Union[Path, str]] = None) -> IngestResult:
    """
    Ingest a document file (resume, cover letter, or notes) into the profile.

    Args:
        file_path: Path to the file to convert and ingest.
        doc_type: One of 'resume', 'cover_letter', 'notes' (or 'other').
        db_path: Database path override, for tests.

    Returns:
        IngestResult summarizing what was created/updated, or skipped=True
        if this exact document (by content hash) was already ingested.
    """
    path = Path(file_path)
    markdown_text = _convert_to_markdown(path)

    # This guard only applies to converted files, not to seed_profile's
    # synthesized blob - a short interactive answer is not a signal of a
    # failed conversion (e.g. a scanned-image PDF with no text layer).
    if len(markdown_text.strip()) < config.MIN_INGEST_TEXT_LENGTH:
        raise SuspiciousDocumentError(
            f"converted text is only {len(markdown_text.strip())} chars - "
            "possibly a scanned/image-only document with no text layer"
        )

    with connection.connect(db_path) as conn:
        return _ingest_markdown(conn, filename=path.name, doc_type=doc_type, markdown_text=markdown_text, origin="ingestion")


def seed_profile(answers: Dict[str, str], *, db_path: Optional[Union[Path, str]] = None) -> IngestResult:
    """
    Ingest interactive Q&A answers into the profile via the same
    extract/merge/apply pipeline as document ingestion.

    Args:
        answers: Mapping of section name (from config.PROFILE_SECTIONS)
            to the user's free-text answer for that section. Blank
            answers and unrecognized keys are ignored.
        db_path: Database path override, for tests.

    Returns:
        IngestResult, same shape as ingest_document's.
    """
    if not answers:
        raise ValueError("no answers provided")

    blob_parts = []
    for section in config.PROFILE_SECTIONS:
        answer = answers.get(section, "").strip()
        if answer:
            blob_parts.append(f"## {section}\n\n{answer}\n\n")

    blob = "".join(blob_parts)
    if not blob.strip():
        raise ValueError("no non-empty answers provided")

    with connection.connect(db_path) as conn:
        return _ingest_markdown(conn, filename="seed_answers.md", doc_type="notes", markdown_text=blob, origin="seed")


def get_profile_facts(sections: Optional[List[str]] = None, *, db_path: Optional[Union[Path, str]] = None):
    """Active profile facts, optionally filtered to the given sections."""
    conn = connection.get_connection(db_path)
    try:
        return repos.get_active_facts(conn, sections=sections)
    finally:
        conn.close()
