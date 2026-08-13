"""Unit tests for list_depositions.py. Purely local file-scanning - no
mocking needed, no network, no onedep_lib/empiar_depositor involved."""

from __future__ import annotations

import json

from conftest import load_script_module

list_depositions = load_script_module("list_depositions")


def test_reports_missing_depositions_dir_without_error(tmp_path, capsys):
    list_depositions.cmd_list(str(tmp_path / "nope"))
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] is False
    assert out["depositions"] == {}


def test_ignores_directories_that_are_not_depositions(tmp_path, capsys):
    (tmp_path / "not-a-deposition").mkdir()
    (tmp_path / "not-a-deposition" / "readme.txt").write_text("hello")

    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] is True
    assert out["depositions"] == {}


def test_reports_emdb_deposition_state_at_each_stage(tmp_path, capsys):
    slug = tmp_path / "protein-a"
    slug.mkdir()

    # manifest only, prepare not yet run
    (slug / "manifest.json").write_text(json.dumps({"email": "a@b.com"}))
    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert out["depositions"]["protein-a"]["emdb"]["state"] == "manifest only (prepare not yet run)"

    # prepared
    (slug / "manifest.json").write_text(json.dumps({"session_id": "sess-1"}))
    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert out["depositions"]["protein-a"]["emdb"]["state"] == "prepared"

    # submitted
    (slug / "manifest.json").write_text(
        json.dumps({"session_id": "sess-1", "remote_dep_id": "D_1", "site_url": "https://example.org/D_1"})
    )
    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    emdb = out["depositions"]["protein-a"]["emdb"]
    assert emdb["state"] == "submitted"
    assert emdb["remote_dep_id"] == "D_1"
    assert emdb["site_url"] == "https://example.org/D_1"


def test_reports_empiar_deposition_state(tmp_path, capsys):
    empiar_dir = tmp_path / "protein-b" / "empiar"
    empiar_dir.mkdir(parents=True)
    (empiar_dir / "json_input.json").write_text(json.dumps({"title": "test"}))

    # not yet submitted - no marker file
    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    entries = out["depositions"]["protein-b"]["empiar"]["json_inputs"]
    assert len(entries) == 1
    assert entries[0]["state"] == "not yet submitted"
    assert entries[0]["entry_id"] is None

    # submitted - marker file present
    (empiar_dir / "json_input.submitted.json").write_text(
        json.dumps({"entry_id": "999", "entry_directory": "xyz", "submitted_at": "2026-01-01T00:00:00Z"})
    )
    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    entry = out["depositions"]["protein-b"]["empiar"]["json_inputs"][0]
    assert entry["state"] == "submitted"
    assert entry["entry_id"] == "999"


def test_marker_files_are_not_listed_as_separate_json_inputs(tmp_path, capsys):
    empiar_dir = tmp_path / "protein-c" / "empiar"
    empiar_dir.mkdir(parents=True)
    (empiar_dir / "json_input.json").write_text(json.dumps({"title": "test"}))
    (empiar_dir / "json_input.submitted.json").write_text(json.dumps({"entry_id": "1"}))

    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    entries = out["depositions"]["protein-c"]["empiar"]["json_inputs"]
    assert len(entries) == 1  # not 2 - the marker file itself isn't a JSON_INPUT


def test_reports_both_emdb_and_empiar_for_same_slug(tmp_path, capsys):
    slug = tmp_path / "protein-d"
    slug.mkdir()
    (slug / "manifest.json").write_text(json.dumps({"session_id": "sess-1"}))
    (slug / "empiar").mkdir()
    (slug / "empiar" / "json_input.json").write_text(json.dumps({"title": "test"}))

    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    dep = out["depositions"]["protein-d"]
    assert dep["emdb"] is not None
    assert dep["empiar"] is not None
