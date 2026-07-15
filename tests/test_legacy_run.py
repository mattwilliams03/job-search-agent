"""
Tests for src/core/legacy_run.py.

Covers the "LLM calls mocked, snapshot test on prompt assembly"
acceptance criterion: prompt-building is tested directly against the
pure functions in src/prompts/, and run_legacy_flow's wiring is tested
with search_jobs and llm.complete mocked out - no real network or model
calls happen in this file.
"""

from unittest.mock import MagicMock

import pytest

from src import config
from src.core import legacy_run
from src.prompts import legacy_job_search


# ---------------------------------------------------------------------------
# Prompt assembly (pure functions, no mocking needed)
# ---------------------------------------------------------------------------

def test_job_search_prompt_assembly():
    prompt = legacy_job_search.build_user_prompt(
        role="Data Scientist",
        location="Los Angeles",
        num_results=3,
        search_results="<job>fake listing</job>",
    )

    assert "Data Scientist" in prompt
    assert "Los Angeles" in prompt
    assert "<instructions>" in prompt
    assert "<focus_areas>" in prompt
    assert "<search_results>\n<job>fake listing</job>\n</search_results>" in prompt


# ---------------------------------------------------------------------------
# run_legacy_flow wiring (LLM + search_jobs mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def mocked_flow(monkeypatch):
    mock_search_jobs = MagicMock(return_value="<job>mocked search results</job>")
    monkeypatch.setattr(legacy_run, "search_jobs", mock_search_jobs)

    mock_complete = MagicMock(
        side_effect=[
            "mocked job search report",
            "mocked skills analysis",
            "mocked interview prep",
            "mocked career advisory",
        ]
    )
    monkeypatch.setattr(legacy_run.llm, "complete", mock_complete)

    return mock_search_jobs, mock_complete


def test_run_legacy_flow_calls_search_jobs_directly(tmp_path, mocked_flow):
    mock_search_jobs, mock_complete = mocked_flow

    legacy_run.run_legacy_flow(
        role="Data Scientist", location="LA", num_results=5, output_dir=tmp_path
    )

    mock_search_jobs.assert_called_once_with("Data Scientist", "LA", 5)
    first_call_kwargs = mock_complete.call_args_list[0].kwargs
    assert "mocked search results" in first_call_kwargs["user"]


def test_run_legacy_flow_calls_llm_four_times_in_order(tmp_path, mocked_flow):
    _, mock_complete = mocked_flow

    legacy_run.run_legacy_flow(
        role="Data Scientist", location="LA", num_results=5, output_dir=tmp_path
    )

    tasks = [call.kwargs["task"] for call in mock_complete.call_args_list]
    assert tasks == [
        "legacy_job_search",
        "legacy_skills_analysis",
        "legacy_interview_prep",
        "legacy_career_advisory",
    ]


def test_run_legacy_flow_steps_2_to_4_receive_job_search_output(tmp_path, mocked_flow):
    _, mock_complete = mocked_flow

    legacy_run.run_legacy_flow(
        role="Data Scientist", location="LA", num_results=5, output_dir=tmp_path
    )

    for call in mock_complete.call_args_list[1:]:
        assert "mocked job search report" in call.kwargs["user"]


def test_run_legacy_flow_writes_per_step_files(tmp_path, mocked_flow):
    legacy_run.run_legacy_flow(
        role="Data Scientist", location="LA", num_results=5, output_dir=tmp_path, verbose=False
    )

    for step_name, display_name in legacy_run.TASK_DISPLAY_NAMES.items():
        matches = list(tmp_path.glob(f"{step_name}_[0-9]*.md"))
        assert len(matches) == 1
        content = matches[0].read_text()
        assert content.startswith(f"# {display_name}\n\n**Completed:**")


def test_run_legacy_flow_final_report_uses_only_career_advisory(tmp_path, mocked_flow):
    result = legacy_run.run_legacy_flow(
        role="Data Scientist", location="LA", num_results=5, output_dir=tmp_path
    )

    report_text = result.report_path.read_text()
    assert "mocked career advisory" in report_text
    assert "mocked skills analysis" not in report_text
    assert "mocked interview prep" not in report_text
    assert "mocked job search report" not in report_text


def test_run_legacy_flow_creates_output_dir_when_none_given(tmp_path, mocked_flow, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)

    result = legacy_run.run_legacy_flow(
        role="Data Scientist", location="LA", num_results=5, output_dir=None
    )

    assert result.run_dir.parent == tmp_path
    assert result.run_dir.name.startswith("data_scientist_")
    assert result.run_dir.exists()
