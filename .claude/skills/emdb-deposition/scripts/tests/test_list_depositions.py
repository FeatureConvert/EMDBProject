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


def test_one_corrupt_manifest_does_not_hide_healthy_depositions(tmp_path, capsys):
    # The overview must degrade per-slug: a single truncated manifest is
    # reported as that slug's own error, while every healthy slug still
    # shows - not a whole-listing abort.
    good = tmp_path / "good"
    good.mkdir()
    (good / "manifest.json").write_text(json.dumps({"session_id": "sess-1"}))
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{truncated")

    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert out["depositions"]["good"]["emdb"]["state"] == "prepared"
    assert out["depositions"]["bad"]["emdb"]["state"] == "error"
    assert "not valid JSON" in out["depositions"]["bad"]["emdb"]["error"]


def test_non_object_manifest_is_reported_as_slug_error(tmp_path, capsys):
    slug = tmp_path / "weird"
    slug.mkdir()
    (slug / "manifest.json").write_text("[]")  # valid JSON, wrong shape

    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert out["depositions"]["weird"]["emdb"]["state"] == "error"
    assert "JSON object" in out["depositions"]["weird"]["emdb"]["error"]


def test_submitted_empiar_entry_survives_deleted_input_file(tmp_path, capsys):
    # A submitted deposition whose JSON_INPUT was later deleted/renamed still
    # has its marker - the entry must not vanish from the overview.
    empiar_dir = tmp_path / "orphaned" / "empiar"
    empiar_dir.mkdir(parents=True)
    (empiar_dir / "json_input.submitted.json").write_text(
        json.dumps({"entry_id": "555", "entry_directory": "abc"})
    )

    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    entries = out["depositions"]["orphaned"]["empiar"]["json_inputs"]
    assert len(entries) == 1
    assert entries[0]["state"] == "submitted"
    assert entries[0]["entry_id"] == "555"
    assert entries[0]["json_input"] is None  # input gone, state came from the marker


def test_dotfiles_are_not_treated_as_json_inputs(tmp_path, capsys):
    # pathlib's glob matches dotfiles; a macOS AppleDouble sidecar
    # (._json_input.json) must not appear as a phantom JSON_INPUT.
    empiar_dir = tmp_path / "protein-e" / "empiar"
    empiar_dir.mkdir(parents=True)
    (empiar_dir / "json_input.json").write_text(json.dumps({"title": "test"}))
    (empiar_dir / "._json_input.json").write_bytes(b"\x00\x01binary applesingle")

    list_depositions.cmd_list(str(tmp_path))
    out = json.loads(capsys.readouterr().out)
    entries = out["depositions"]["protein-e"]["empiar"]["json_inputs"]
    assert len(entries) == 1
    assert entries[0]["json_input"] == "json_input.json"


def test_depositions_dir_that_is_a_file_is_an_error(tmp_path, capsys):
    not_a_dir = tmp_path / "depositions"
    not_a_dir.write_text("oops, a file")

    import pytest

    with pytest.raises(SystemExit) as exc:
        list_depositions.cmd_list(str(not_a_dir))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "not a directory" in out["error"]
