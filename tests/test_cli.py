"""
Tests for the jobsearch typer CLI.
"""

from unittest.mock import MagicMock

from typer.testing import CliRunner

from src import cli
from src.db import connection as db_connection
from src.core.legacy_run import LegacyRunResult

runner = CliRunner()


def test_db_migrate_command(tmp_path, monkeypatch):
    monkeypatch.setattr(db_connection, "DB_PATH", tmp_path / "cli.db")

    result = runner.invoke(cli.app, ["db", "migrate"])

    assert result.exit_code == 0
    assert "001_initial" in result.output


def test_db_migrate_command_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(db_connection, "DB_PATH", tmp_path / "cli.db")

    runner.invoke(cli.app, ["db", "migrate"])
    result = runner.invoke(cli.app, ["db", "migrate"])

    assert result.exit_code == 0
    assert "already up to date" in result.output.lower()


def test_db_status_command_after_migrate(tmp_path, monkeypatch):
    monkeypatch.setattr(db_connection, "DB_PATH", tmp_path / "cli.db")

    runner.invoke(cli.app, ["db", "migrate"])
    result = runner.invoke(cli.app, ["db", "status"])

    assert result.exit_code == 0
    assert "documents: 0 rows" in result.output
    assert "listings: 0 rows" in result.output


def test_run_command_invokes_legacy_flow(monkeypatch):
    fake_result = LegacyRunResult(
        run_dir=MagicMock(),
        report_path="fake/report.md",
        step_outputs={},
        step_paths={},
    )
    mock_run_legacy_flow = MagicMock(return_value=fake_result)
    monkeypatch.setattr(cli, "run_legacy_flow", mock_run_legacy_flow)
    monkeypatch.setattr(cli.config, "validate_config", lambda: (True, []))

    result = runner.invoke(
        cli.app, ["run", "--role", "X", "--location", "Y", "--num-results", "3"]
    )

    assert result.exit_code == 0
    mock_run_legacy_flow.assert_called_once_with(
        role="X", location="Y", num_results=3, verbose=False
    )


def test_run_command_exits_nonzero_on_invalid_config(monkeypatch):
    mock_run_legacy_flow = MagicMock()
    monkeypatch.setattr(cli, "run_legacy_flow", mock_run_legacy_flow)
    monkeypatch.setattr(
        cli.config, "validate_config", lambda: (False, ["ANTHROPIC_API_KEY is not set."])
    )

    result = runner.invoke(cli.app, ["run"])

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
    mock_run_legacy_flow.assert_not_called()
