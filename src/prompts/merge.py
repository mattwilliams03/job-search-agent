"""
Prompt content for the fact-merge step of profile ingestion.

Second LLM call in the ingestion pipeline (Sonnet - see config.MODEL_MERGE),
one batched call per ingestion event. Reconciles newly extracted candidate
facts against the person's current active facts in the same sections,
deciding per candidate whether it's new, a duplicate, an update, or a
conflict.
"""

from typing import Any, List

SYSTEM = (
    "You reconcile newly extracted candidate profile facts against a "
    "person's existing active profile facts.\n\n"

    "For each candidate, referenced by its 0-based `candidate_index`, "
    "decide exactly one of:\n"
    "- \"new\": this is genuinely new information, not covered by any "
    "existing fact.\n"
    "- \"duplicate_of\": this candidate restates an existing fact with no "
    "material new information. Include the existing fact's `uid`.\n"
    "- \"updates\": this candidate supersedes an existing fact (e.g. a "
    "changed job title, a refined description of the same achievement). "
    "Include the existing fact's `uid` and a one-sentence `resolution` "
    "explaining the relationship.\n"
    "- \"conflicts_with\": this candidate contradicts an existing fact "
    "in a way that isn't a simple update (e.g. differing claims about the "
    "same thing that can't both be true). Include the existing fact's "
    "`uid` and a one-sentence `resolution` describing the contradiction.\n\n"

    "A `uid` you reference MUST be one of the uids given to you in the "
    "existing facts list below - never invent one.\n\n"

    "You must return exactly one decision per candidate index, covering "
    "every index from 0 to the last candidate, with no gaps and no "
    "duplicate indices.\n\n"

    "Respond with ONLY a single JSON object matching the schema you're "
    "given. No prose, no explanation, no markdown code fences."
)


def build_user_prompt(candidates: List[Any], existing_facts: List[Any]) -> str:
    """
    Assemble the user-turn prompt for the merge step.

    Args:
        candidates: CandidateFact-like objects (accessed via .section /
            .content), in the order their candidate_index refers to.
        existing_facts: sqlite3.Row-like objects (accessed via
            ["uid"] / ["section"] / ["content"]), already filtered by the
            caller to the sections present among the candidates.

    Returns:
        The full user-turn prompt string.
    """
    candidate_lines = "\n".join(
        f'{i}. [{c.section}] {c.content}' for i, c in enumerate(candidates)
    )
    existing_lines = "\n".join(
        f'{f["uid"]}. [{f["section"]}] {f["content"]}' for f in existing_facts
    ) or "(none)"

    return (
        f"<candidates>\n{candidate_lines}\n</candidates>\n\n"
        f"<existing_facts>\n{existing_lines}\n</existing_facts>\n\n"
        "Decide how each candidate relates to the existing facts above.\n\n"
        "Respond with exactly this JSON shape:\n"
        "{\n"
        '  "decisions": [\n'
        '    {"candidate_index": 0, "decision": "new"},\n'
        '    {"candidate_index": 1, "decision": "duplicate_of", "uid": "f_a3k9"},\n'
        '    {"candidate_index": 2, "decision": "updates", "uid": "f_b7x2", '
        '"resolution": "job title changed from X to Y"}\n'
        "  ]\n"
        "}"
    )
