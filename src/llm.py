"""
Thin wrapper over the Anthropic SDK.

Every LLM interaction in this codebase goes through complete() below,
which resolves a per-task model from src.config.MODEL_FOR_TASK. This is
the seam described in the redesign plan: as later phases add real
per-task models (extraction, merge, cover letter drafting, etc.), only
src/config.py changes - this module stays the same.
"""

from typing import Optional

from anthropic import Anthropic

from src import config

_client: Optional[Anthropic] = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def complete(
    *,
    task: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
    model: Optional[str] = None,
) -> str:
    """
    Send a single-turn completion request to Claude.

    Args:
        task: Key into config.MODEL_FOR_TASK selecting which model to
            use. Unknown keys fall back to config.CLAUDE_MODEL rather
            than raising, so new callers don't need a config change
            before their first run.
        system: System prompt.
        user: User-turn content.
        max_tokens: Max tokens to generate.
        model: Explicit model override; bypasses the task lookup.

    Returns:
        The concatenated text of the response's text blocks.
    """
    resolved_model = model or config.MODEL_FOR_TASK.get(task, config.CLAUDE_MODEL)

    response = _get_client().messages.create(
        model=resolved_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    return "".join(block.text for block in response.content if block.type == "text")
