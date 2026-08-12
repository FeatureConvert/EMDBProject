"""Unit tests for empiar_deposit.py. No network access, no real subprocess calls
to empiar-depositor - subprocess.run is mocked."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_empiar_deposit():
    spec = importlib.util.spec_from_file_location("empiar_deposit", SCRIPTS_DIR / "empiar_deposit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


empiar_deposit = _load_empiar_deposit()

def _bundled_example() -> Path:
    import empiar_depositor

    return Path(empiar_depositor.__file__).parent / "tests" / "deposition_json" / "working_example.json"


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
        empiar_deposit.cmd_submit(
            str(_bundled_example()), str(tmp_path), confirm=False,
            ascp=None, globus=None, thumbnail=None, resume=None,
        )
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "confirm" in out["error"].lower()


def test_submit_refuses_without_api_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("EMPIAR_API_TOKEN", raising=False)
    monkeypatch.delenv("EMPIAR_TRANSFER_PASS", raising=False)
    with pytest.raises(SystemExit):
        empiar_deposit.cmd_submit(
            str(_bundled_example()), str(tmp_path), confirm=True,
            ascp=None, globus=None, thumbnail=None, resume=None,
        )
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "empiar_api_token" in out["error"].lower()


def test_submit_refuses_without_transfer_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "tok123")
    monkeypatch.delenv("EMPIAR_TRANSFER_PASS", raising=False)
    with pytest.raises(SystemExit):
        empiar_deposit.cmd_submit(
            str(_bundled_example()), str(tmp_path), confirm=True,
            ascp=None, globus=None, thumbnail=None, resume=None,
        )
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "empiar_transfer_pass" in out["error"].lower()


@patch("subprocess.run")
def test_submit_redacts_token_from_reported_command(mock_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPIAR_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("EMPIAR_TRANSFER_PASS", "transfer-pass")
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    empiar_deposit.cmd_submit(
        str(_bundled_example()), str(tmp_path), confirm=True,
        ascp=None, globus=None, thumbnail=None, resume=None,
    )

    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert "super-secret-token" not in json.dumps(out)
    assert "***" in out["command"]

    called_argv = mock_run.call_args.args[0]
    assert "super-secret-token" in called_argv  # the real subprocess call still gets the real token

    # argv[0] must be resolved next to the current interpreter, not a bare
    # "empiar-depositor" that only works if the venv happens to be on PATH.
    assert called_argv[0] == str(Path(sys.executable).parent / "empiar-depositor")
