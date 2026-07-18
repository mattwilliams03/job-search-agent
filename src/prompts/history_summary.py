"""
Prompt content for the fact-history-summarization step (profile show --history).

One Haiku call per fact chain (see config.MODEL_HISTORY_SUMMARY), only
made for chains with at least one superseded ancestor (empty chains are
never summarized - see profile_service.get_fact_history_summary).

Unlike extraction.py/merge.py, this step's response is bare text, not
JSON - there's nothing to parse beyond a strip(). Don't "fix" this to
match the other two steps' JSON contract.
"""

SYSTEM = (
    "You compress the evolution of one profile fact - from its oldest "
    "recorded form to its current form - into a single, concise line.\n\n"

    "You will be given the fact's history in chronological order (oldest "
    "first), each version with its content, where it came from (a "
    "document filename, or a manual edit), and a date.\n\n"

    "Respond with exactly one line of plain text, starting with "
    "\"evolved:\", using → between stages. Quote the oldest version's "
    "content once; describe later transitions concisely rather than "
    "quoting them in full. Do not use JSON, markdown code fences, or any "
    "text before or after the single line.\n\n"

    "Example: evolved: \"Worked on billing infrastructure\" (resume_2023.pdf) "
    "→ reworded with metrics (manual edit, 2026-03)"
)


def build_user_prompt(chain: list) -> str:
    """
    Assemble the user-turn prompt for the history-summary step.

    Args:
        chain: Oldest-first list of dicts, each shaped
            {"content": str, "origin": str, "document_filename": Optional[str], "date": str},
            ending with the fact's current active version.

    Returns:
        The full user-turn prompt string.
    """
    lines = []
    for entry in chain:
        source = entry["document_filename"] or f"{entry['origin']} edit"
        lines.append(f"- [{entry['date']}] ({source}) {entry['content']}")

    history_block = "\n".join(lines)

    return (
        f"<fact_history>\n{history_block}\n</fact_history>\n\n"
        "Summarize this fact's evolution into one line, per the format "
        "and example in your instructions."
    )
