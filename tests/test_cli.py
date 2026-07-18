"""
Tests for the jobsearch typer CLI.
"""

from unittest.mock import MagicMock

from typer.testing import CliRunner

from src import cli, config
from src.db import connection as db_connection
from src.core.legacy_run import LegacyRunResult
from src.core import profile_service

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


def test_profile_ingest_command_invokes_service_and_prints_summary(tmp_path, monkeypatch):
    dummy_file = tmp_path / "resume.pdf"
    dummy_file.write_bytes(b"not a real pdf, service is mocked")

    fake_result = profile_service.IngestResult(
        document_id=1,
        skipped=False,
        apply=profile_service.ApplyResult(new=["f_aaaa", "f_bbbb"], updated=["f_cccc"], duplicates=[], conflicts=[]),
    )
    mock_ingest = MagicMock(return_value=fake_result)
    monkeypatch.setattr(cli.profile_service, "ingest_document", mock_ingest)

    result = runner.invoke(cli.app, ["profile", "ingest", str(dummy_file), "--type", "resume"])

    assert result.exit_code == 0
    assert "2 new" in result.output
    assert "1 updated" in result.output
    mock_ingest.assert_called_once_with(str(dummy_file), doc_type="resume")


def test_profile_ingest_command_reports_noop_on_skip(tmp_path, monkeypatch):
    dummy_file = tmp_path / "resume.pdf"
    dummy_file.write_bytes(b"content")

    fake_result = profile_service.IngestResult(document_id=1, skipped=True, apply=None)
    monkeypatch.setattr(cli.profile_service, "ingest_document", MagicMock(return_value=fake_result))

    result = runner.invoke(cli.app, ["profile", "ingest", str(dummy_file), "--type", "resume"])

    assert result.exit_code == 0
    assert "no-op" in result.output.lower()


def test_profile_ingest_command_exits_nonzero_on_ingestion_error(tmp_path, monkeypatch):
    dummy_file = tmp_path / "resume.pdf"
    dummy_file.write_bytes(b"content")

    mock_ingest = MagicMock(side_effect=profile_service.SuspiciousDocumentError("too short"))
    monkeypatch.setattr(cli.profile_service, "ingest_document", mock_ingest)

    result = runner.invoke(cli.app, ["profile", "ingest", str(dummy_file), "--type", "resume"])

    assert result.exit_code == 1
    assert "too short" in result.output


def test_profile_ingest_command_rejects_invalid_type(tmp_path, monkeypatch):
    dummy_file = tmp_path / "resume.pdf"
    dummy_file.write_bytes(b"content")

    mock_ingest = MagicMock()
    monkeypatch.setattr(cli.profile_service, "ingest_document", mock_ingest)

    result = runner.invoke(cli.app, ["profile", "ingest", str(dummy_file), "--type", "bogus"])

    assert result.exit_code == 1
    mock_ingest.assert_not_called()


def test_profile_show_command_groups_facts_by_section(monkeypatch):
    facts = [
        {"section": "skills", "uid": "f_aaaa", "content": "Knows Python"},
        {"section": "summary", "uid": "f_bbbb", "content": "Backend engineer"},
    ]
    monkeypatch.setattr(cli.profile_service, "get_profile_facts", MagicMock(return_value=facts))

    result = runner.invoke(cli.app, ["profile", "show"])

    assert result.exit_code == 0
    assert result.output.index("## summary") < result.output.index("## skills")
    assert "[f_bbbb] Backend engineer" in result.output
    assert "[f_aaaa] Knows Python" in result.output


def test_profile_show_command_passes_section_filter(monkeypatch):
    mock_get = MagicMock(return_value=[])
    monkeypatch.setattr(cli.profile_service, "get_profile_facts", mock_get)

    result = runner.invoke(cli.app, ["profile", "show", "--section", "skills"])

    assert result.exit_code == 0
    mock_get.assert_called_once_with(sections=["skills"])


def test_profile_seed_command_collects_nonempty_answers_and_invokes_service(monkeypatch):
    fake_result = profile_service.IngestResult(
        document_id=1, skipped=False, apply=profile_service.ApplyResult(new=["f_aaaa"])
    )
    mock_seed = MagicMock(return_value=fake_result)
    monkeypatch.setattr(cli.profile_service, "seed_profile", mock_seed)

    # One answer for the first prompt (summary), blank for the rest.
    prompt_input = "Summary text\n" + "\n" * (len(config.PROFILE_SECTIONS) - 1)
    result = runner.invoke(cli.app, ["profile", "seed"], input=prompt_input)

    assert result.exit_code == 0
    mock_seed.assert_called_once_with({"summary": "Summary text"})


def test_profile_seed_command_all_blank_skips_service_call(monkeypatch):
    mock_seed = MagicMock()
    monkeypatch.setattr(cli.profile_service, "seed_profile", mock_seed)

    prompt_input = "\n" * len(config.PROFILE_SECTIONS)
    result = runner.invoke(cli.app, ["profile", "seed"], input=prompt_input)

    assert result.exit_code == 0
    assert "nothing to seed" in result.output.lower()
    mock_seed.assert_not_called()


def test_profile_seed_command_exits_nonzero_on_ingestion_error(monkeypatch):
    mock_seed = MagicMock(side_effect=profile_service.DirtyProfileError("profile.md has unsynced edits"))
    monkeypatch.setattr(cli.profile_service, "seed_profile", mock_seed)

    prompt_input = "Summary text\n" + "\n" * (len(config.PROFILE_SECTIONS) - 1)
    result = runner.invoke(cli.app, ["profile", "seed"], input=prompt_input)

    assert result.exit_code == 1
    assert "unsynced edits" in result.output


def test_profile_export_command_invokes_service_and_prints_path(monkeypatch, tmp_path):
    fake_result = profile_service.ExportResult(path=tmp_path / "profile.md", fact_count=3, hash="abc123")
    mock_export = MagicMock(return_value=fake_result)
    monkeypatch.setattr(cli.profile_service, "export_profile", mock_export)

    result = runner.invoke(cli.app, ["profile", "export"])

    assert result.exit_code == 0
    assert "3" in result.output
    assert str(tmp_path / "profile.md") in result.output
    mock_export.assert_called_once_with()


def test_profile_sync_command_prints_summary_and_conflicts(monkeypatch):
    fake_result = profile_service.SyncResult(
        new=["f_aaaa"],
        updated=["f_bbbb"],
        deleted=["f_cccc"],
        conflicts=[profile_service.SyncConflict(
            new_uid="f_dddd", original_uid="f_eeee", descendant_uid="f_ffff", note="manual edit won"
        )],
    )
    monkeypatch.setattr(cli.profile_service, "sync_profile", MagicMock(return_value=fake_result))

    result = runner.invoke(cli.app, ["profile", "sync"])

    assert result.exit_code == 0
    assert "1 new" in result.output
    assert "1 updated" in result.output
    assert "1 deleted" in result.output
    assert "1 conflict" in result.output
    assert "manual edit won" in result.output


def test_profile_sync_command_reports_noop(monkeypatch):
    monkeypatch.setattr(cli.profile_service, "sync_profile", MagicMock(return_value=profile_service.SyncResult(no_op=True)))

    result = runner.invoke(cli.app, ["profile", "sync"])

    assert result.exit_code == 0
    assert "nothing to sync" in result.output.lower()


def test_profile_sync_command_exits_nonzero_on_not_exported_error(monkeypatch):
    mock_sync = MagicMock(side_effect=profile_service.ProfileNotExportedError("no exported profile"))
    monkeypatch.setattr(cli.profile_service, "sync_profile", mock_sync)

    result = runner.invoke(cli.app, ["profile", "sync"])

    assert result.exit_code == 1
    assert "no exported profile" in result.output


def test_profile_show_history_flag_renders_lineage_line(monkeypatch):
    entries = [
        {"fact": {"section": "experience", "uid": "f_aaaa", "content": "Led migration"}, "history_summary": "evolved: from a smaller role"},
        {"fact": {"section": "skills", "uid": "f_bbbb", "content": "Python"}, "history_summary": None},
    ]
    mock_get = MagicMock(return_value=entries)
    monkeypatch.setattr(cli.profile_service, "get_profile_facts_with_history", mock_get)

    result = runner.invoke(cli.app, ["profile", "show", "--history"])

    assert result.exit_code == 0
    assert "[f_aaaa] Led migration" in result.output
    assert "↳ evolved: from a smaller role" in result.output
    assert "[f_bbbb] Python" in result.output
    mock_get.assert_called_once_with(sections=None)
