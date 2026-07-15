"""
Tests for the thin Anthropic SDK wrapper in src/llm.py.
"""

from unittest.mock import MagicMock, patch

from src import config, llm


def _mock_response(text: str = "response text"):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def test_complete_uses_model_for_task_lookup():
    with patch("src.llm._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response()
        mock_get_client.return_value = mock_client

        llm.complete(task="legacy_job_search", system="sys", user="usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == config.MODEL_FOR_TASK["legacy_job_search"]
        assert kwargs["system"] == "sys"
        assert kwargs["messages"] == [{"role": "user", "content": "usr"}]


def test_complete_falls_back_for_unknown_task():
    with patch("src.llm._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response()
        mock_get_client.return_value = mock_client

        llm.complete(task="some_unknown_task", system="sys", user="usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == config.CLAUDE_MODEL


def test_complete_explicit_model_overrides_task_lookup():
    with patch("src.llm._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response()
        mock_get_client.return_value = mock_client

        llm.complete(task="legacy_job_search", system="sys", user="usr", model="claude-opus-4-8")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == "claude-opus-4-8"


def test_complete_concatenates_text_blocks():
    block1 = MagicMock(type="text", text="Hello ")
    block2 = MagicMock(type="text", text="world")
    response = MagicMock()
    response.content = [block1, block2]

    with patch("src.llm._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        mock_get_client.return_value = mock_client

        result = llm.complete(task="legacy_job_search", system="sys", user="usr")

    assert result == "Hello world"
