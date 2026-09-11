"""Unit tests for empiar_deposit.py. No network access, no real subprocess calls
to empiar-depositor - subprocess.run is mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import load_script_module

empiar_deposit = load_script_module("empiar_deposit")


def _bundled_example() -> Path:
    import empiar_depositor

    return Path(empiar_depositor.__file__).parent / "tests" / "deposition_json" / "working_example.json"


def _submit(json_input, data_dir, **overrides):
    # Always operate on a tmp_path-local copy, never the original path
    # as-is: cmd_submit can write a sidecar `<json_input>.submitted.json`
    # marker on a returncode-0 result, and several tests pass the bundled
    # package fixture path directly - writing real files next to that
    # shared, non-tmp location would pollute later test runs (and the
    # installed package itself). This was a real bug caught by running the
    # suite: a mocked "success" test left a stray .submitted.json marker
    # next to empiar_depositor's own bundled example, which then made an
    # unrelated later test see a false "already submitted" state.
    isolated = Path(data_dir) / "json_input.json"
    isolated.write_text(Path(json_input).read_text())
    kwargs = dict(
        confirm=True, force=False, ascp="/fake/ascp", globus=None, thumbnail=None, resume=None,
    )
    kwargs.update(overrides)
    return empiar_deposit.cmd_submit(str(isolated), str(data_dir), **kwargs)


def test_validate_accepts_bundled_real_example(capsys):
    empiar_deposit.cmd_validate(str(_bundled_example()))
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "issues": []}


def test_validate_rejects_missing_required_fields(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"title": "Missing everything else"}))
    with pytest.raises(SystemExit) as exc:
        empiar_deposit.cmd_validate(str(bad))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert len(out["issues"]) > 0


def test_preview_writes_review_file_for_valid_input(tmp_path, capsys):
    isolated = tmp_path / "json_input.json"
    isolated.write_text(_bundled_example().read_text())

    empiar_deposit.cmd_preview(str(isolated), None)
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["schema_ok"] is True

    preview_path = tmp_path / "json_input.preview.md"
    assert preview_path.exists()
    text = preview_path.read_text()
    assert "Schema validation: PASSED" in text
    assert "Full JSON_INPUT payload" in text


def test_preview_reports_schema_failure_without_exiting(tmp_path, capsys):
    bad = tmp_path / "json_input.json"
    bad.write_text(json.dumps({"title": "missing the rest"}))

    # A schema-invalid input is still previewable - the depositor should see
    # WHAT is wrong, so preview reports schema_ok=false rather than fail()ing.
    empiar_deposit.cmd_preview(str(bad), None)
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["schema_ok"] is False
    assert "Schema validation: FAILED" in (tmp_path / "json_input.preview.md").read_text()


def test_preview_rejects_marker_named_input(tmp_path, capsys):
    collide = tmp_path / "dataset.submitted.json"
    collide.write_text(json.dumps({"title": "x"}))
    with pytest.raises(SystemExit):
        empiar_deposit.cmd_preview(str(collide), None)
    out = json.loads(capsys.readouterr().out)
    assert ".submitted.json" in out["error"]


def test_rejects_json_input_named_like_a_marker(tmp_path, capsys):
    # A JSON_INPUT deliberately named *.submitted.json collides with the
    # submission-marker sidecar scheme and would be misread as a marker by
    # list_depositions.py - refuse it up front, before any validation.
    collide = tmp_path / "dataset.submitted.json"
    collide.write_text(json.dumps({"title": "x"}))
    with pytest.raises(SystemExit) as exc:
        empiar_deposit.cmd_validate(str(collide))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert ".submitted.json" in out["error"]


def test_error_sort_key_handles_mixed_int_and_str_path_elements():
    # cmd_validate sorts jsonschema errors by path before reporting them.
    # jsonschema error paths mix str (object keys) and int (array indices),
    # and Python raises TypeError comparing across those types - e.g.
    # sorted([['a', 0], ['b']]) is fine (resolves on the strings at index 0),
    # but sorted([[0, 'a'], ['b']]) raises TypeError at index 0 directly.
    # We couldn't construct a real EMPIAR JSON_INPUT that produces two
    # errors whose paths actually diverge this way (every top-level key in
    # that schema is a distinct string, so comparisons always resolve
    # before reaching a mismatched type) - so this exercises the sort key
    # logic directly against a case shaped to trigger it, rather than via
    # a schema-validation round trip that can't reach it in practice.
    class FakeError:
        def __init__(self, path):
            self.path = path

    errors = [FakeError([0, "a"]), FakeError(["b"])]

    with pytest.raises(TypeError):
        sorted(errors, key=lambda e: list(e.path))  # the old, unfixed key

    # The fixed key stringifies path elements first, so it never crashes.
    sorted(errors, key=lambda e: [str(p) for p in e.path])


def test_submit_refuses_without_confirm(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _submit(_bundled_example(), tmp_path, confirm=False)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "confirm" in out["error"].lower()


def test_submit_refuses_without_api_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("EMPIAR_API_TOKEN", raising=False)
    monkeypatch.delenv("EMPIAR_TRANSFER_PASS", raising=False)
    with pytest.raises(SystemExit):
        _submit(_bundled_example(), tmp_path)
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "empiar_api_token" in out["error"].lower()


def test_submit_refuses_without_transfer_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.delenv("EMPIAR_TRANSFER_PASS", raising=False)
    with pytest.raises(SystemExit):
        _submit(_bundled_example(), tmp_path)
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "empiar_transfer_pass" in out["error"].lower()


def test_submit_refuses_without_ascp_or_globus(tmp_path, monkeypatch, capsys):
    # empiar-depositor creates the live entry via its API BEFORE attempting
    # any transfer, and silently skips the transfer entirely if neither
    # -a/ascp nor -g/globus resolves - this must be caught before ever
    # shelling out, not discovered after a dataless entry already exists.
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    with patch.object(empiar_deposit, "_default_ascp_path", return_value=None):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(SystemExit) as exc:
                _submit(_bundled_example(), tmp_path, ascp=None, globus=None)
            mock_run.assert_not_called()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "ascp" in out["error"].lower() and "globus" in out["error"].lower()


@patch("subprocess.run")
def test_submit_falls_back_to_default_ascp_path_when_installed(mock_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    with patch.object(empiar_deposit, "_default_ascp_path", return_value="/opt/aspera/ascp"):
        _submit(_bundled_example(), tmp_path, ascp=None, globus=None)

    called_argv = mock_run.call_args.args[0]
    assert "-a" in called_argv
    assert called_argv[called_argv.index("-a") + 1] == "/opt/aspera/ascp"


def test_submit_refuses_invalid_json_input_without_shelling_out(tmp_path, monkeypatch, capsys):
    # submit re-validates the JSON_INPUT itself right before shelling out,
    # in case it was edited since the last `validate` call - this must
    # fail before ever touching subprocess.
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"title": "Missing everything else"}))

    with patch("subprocess.run") as mock_run:
        with pytest.raises(SystemExit) as exc:
            _submit(bad, tmp_path)
        mock_run.assert_not_called()

    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "validation" in out["error"].lower()
    assert len(out["issues"]) > 0


@patch("subprocess.run")
def test_submit_reports_clean_error_when_binary_missing(mock_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    mock_run.side_effect = FileNotFoundError()

    with pytest.raises(SystemExit) as exc:
        _submit(_bundled_example(), tmp_path)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "empiar-depositor executable not found" in out["error"]
    # the reinstall command it suggests must use a path that actually works
    # from the project root, matching every other occurrence in the repo
    assert ".claude/skills/emdb-deposition-v2-beta/requirements.txt" in out["error"]


@patch("subprocess.run")
def test_submit_redacts_token_from_reported_command(mock_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "transfer-pass")
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    _submit(_bundled_example(), tmp_path)

    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert "super-secret-token" not in json.dumps(out)
    assert "***" in out["command"]

    called_argv = mock_run.call_args.args[0]
    assert "super-secret-token" in called_argv  # the real subprocess call still gets the real token

    # argv[0] must be resolved next to the current interpreter, not a bare
    # "empiar-depositor" that only works if the venv happens to be on PATH.
    assert called_argv[0] == str(Path(sys.executable).parent / "empiar-depositor")


@patch("subprocess.run")
def test_submit_parses_entry_id_and_writes_marker_on_success(mock_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="EMPIAR deposition was successfully created. Your entry ID is 12345 "
        "and unique data directory is abcde12345\nFinished uploading the data.\n",
        stderr="",
    )

    json_input = tmp_path / "json_input.json"
    json_input.write_text(_bundled_example().read_text())

    _submit(json_input, tmp_path)

    out = json.loads(capsys.readouterr().out)
    assert out["entry_id"] == "12345"
    assert out["entry_directory"] == "abcde12345"

    marker = json_input.with_suffix(".submitted.json")
    assert marker.exists()
    marker_data = json.loads(marker.read_text())
    assert marker_data["entry_id"] == "12345"


@patch("subprocess.run")
def test_submit_refuses_resubmission_without_force(mock_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")

    json_input = tmp_path / "json_input.json"
    json_input.write_text(_bundled_example().read_text())
    marker = json_input.with_suffix(".submitted.json")
    marker.write_text(json.dumps({"entry_id": "999", "entry_directory": "xyz"}))

    with pytest.raises(SystemExit) as exc:
        _submit(json_input, tmp_path, force=False)
    mock_run.assert_not_called()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "999" in out["error"]
    assert "force" in out["error"].lower()


@patch("subprocess.run")
def test_submit_allows_resubmission_with_force(mock_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    json_input = tmp_path / "json_input.json"
    json_input.write_text(_bundled_example().read_text())
    marker = json_input.with_suffix(".submitted.json")
    marker.write_text(json.dumps({"entry_id": "999", "entry_directory": "xyz"}))

    _submit(json_input, tmp_path, force=True)  # must not raise

    mock_run.assert_called_once()


@patch("subprocess.run")
def test_submit_with_resume_bypasses_guard_without_force(mock_run, tmp_path, monkeypatch, capsys):
    # --resume continues an interrupted transfer against the SAME entry a
    # prior marker already recorded - that's the documented recovery path,
    # not a duplicate submission, so it must proceed without --force (which
    # has the opposite documented meaning: deliberately create a separate
    # entry).
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    json_input = tmp_path / "json_input.json"
    json_input.write_text(_bundled_example().read_text())
    marker = json_input.with_suffix(".submitted.json")
    marker.write_text(json.dumps({"entry_id": "12345", "entry_directory": "abcde12345"}))

    _submit(json_input, tmp_path, force=False, resume=("12345", "abcde12345"))  # must not raise

    mock_run.assert_called_once()
    called_argv = mock_run.call_args.args[0]
    assert "-r" in called_argv


@patch("subprocess.run")
def test_submit_writes_placeholder_marker_when_entry_id_unparseable(mock_run, tmp_path, monkeypatch, capsys):
    # returncode 0 but stdout doesn't match the expected "Your entry ID is
    # ..." format (e.g. empiar-depositor changes its wording) - this used
    # to silently skip writing any marker at all, so a retry would sail
    # through guard_resubmission and could create a second live entry.
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "pass123")
    mock_run.return_value = MagicMock(returncode=0, stdout="unexpected new output format", stderr="")

    json_input = tmp_path / "json_input.json"
    json_input.write_text(_bundled_example().read_text())

    _submit(json_input, tmp_path)

    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["warning"] is not None
    assert "could not be parsed" in out["warning"]

    marker = json_input.with_suffix(".submitted.json")
    assert marker.exists()

    # a follow-up submit against the same file must now be blocked without --force
    with pytest.raises(SystemExit):
        _submit(json_input, tmp_path, force=False)


def test_main_converts_uncaught_exception_to_json(tmp_path, monkeypatch, capsys):
    # Reproduces the live-verified bug: a JSON_INPUT file with invalid UTF-8
    # bytes raises UnicodeDecodeError from inside _validate()/load_manifest,
    # which used to leak as a raw traceback since empiar_deposit.py's
    # main() had no exception handling at all, unlike em_deposit.py's.
    bad = tmp_path / "bad_bytes.json"
    bad.write_bytes(b"\xff\xfe not valid utf-8")

    monkeypatch.setattr(
        sys, "argv", ["empiar_deposit.py", "validate", "--json-input", str(bad)]
    )

    with pytest.raises(SystemExit) as exc:
        empiar_deposit.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "UnicodeDecodeError" in out["error"]
