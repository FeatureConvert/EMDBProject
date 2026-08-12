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
