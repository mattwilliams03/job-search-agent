"""
Prompt content for the fact-extraction step of profile ingestion.

First LLM call in the ingestion pipeline (Haiku - see config.MODEL_EXTRACT).
Extracts atomic candidate facts from a document's converted markdown text,
each grounded by a verbatim quote so profile_service.verify_quote() can
mechanically drop any fabricated ones before they're ever merged in.
"""

from src.config import PROFILE_SECTIONS

_FACT_SECTIONS = [s for s in PROFILE_SECTIONS if s != "style"]

SYSTEM = (
    "You extract atomic profile facts from a candidate's document (resume, "
    "cover letter, or notes) to build a structured professional profile.\n\n"

    "Core rule: one bullet or sentence = one fact. Never combine multiple "
    "distinct claims into a single fact, and never split one claim across "
    "multiple facts.\n\n"

    "Every fact you extract must include a `quote`: an exact, verbatim "
    "substring copied character-for-character from the document text "
    "(whitespace differences are fine, but do not paraphrase, summarize, or "
    "invent it). A fact whose quote cannot be found verbatim in the source "
    "document will be mechanically discarded, so never fabricate a quote to "
    "support a claim you're inferring rather than reading directly.\n\n"

    "Separately, extract `style_observations`: observations about the "
    "candidate's voice, tone, or writing style (e.g. \"prefers concise, "
    "action-verb-led bullets\" or \"uses formal, third-person phrasing\"), "
    "each also grounded by a verbatim quote.\n\n"

    "Respond with ONLY a single JSON object matching the schema you're "
    "given. No prose, no explanation, no markdown code fences."
)


def build_user_prompt(source_text: str) -> str:
    """
    Assemble the user-turn prompt for the extraction step.

    Args:
        source_text: The document's converted markdown text (or, for
            profile seed, the synthesized Q&A blob).

    Returns:
        The full user-turn prompt string.
    """
    sections_list = ", ".join(_FACT_SECTIONS)

    return (
        f"<document>\n{source_text}\n</document>\n\n"
        f"Extract candidate facts from the document above.\n\n"
        f"Valid `section` values for facts: {sections_list}\n\n"
        "Respond with exactly this JSON shape:\n"
        "{\n"
        '  "facts": [\n'
        '    {"section": "<one of the valid sections above>", '
        '"content": "<one atomic fact, markdown-safe>", '
        '"quote": "<verbatim span from the document>"}\n'
        "  ],\n"
        '  "style_observations": [\n'
        '    {"content": "<one atomic style observation>", '
        '"quote": "<verbatim span from the document>"}\n'
        "  ]\n"
        "}\n\n"
        "Both arrays may be empty if nothing applies. Include every atomic "
        "fact you can find - err on the side of more, smaller facts rather "
        "than fewer, larger ones."
    )
