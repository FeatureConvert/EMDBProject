"""Unit tests for auth_setup.py, mocking onedep_lib entirely. No network access.

This file didn't exist before a review pass flagged that auth_setup.py had
zero test coverage of any kind, unlike its two sibling scripts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import load_script_module

auth_setup = load_script_module("auth_setup")


def _fake_config(refresh_token=None):
    config = MagicMock()
    config.refresh_token = refresh_token
    config.hostname = "https://deposit.wwpdb.org/deposition"
    config.config_path = Path("/fake/config.toml")
    return config


@patch("onedep_lib.config.DepositConfig")
def test_check_reports_unauthenticated_when_no_refresh_token(mock_config_cls, capsys):
    mock_config_cls.load.return_value = _fake_config(refresh_token=None)
    auth_setup.cmd_check()
    out = json.loads(capsys.readouterr().out)
    assert out["authenticated"] is False
    assert "No refresh token" in out["reason"]


@patch("onedep_lib.check_auth_key")
@patch("onedep_lib.config.DepositConfig")
def test_check_reports_authenticated_when_key_valid(mock_config_cls, mock_check_key, capsys):
    mock_config_cls.load.return_value = _fake_config(refresh_token="rt")
    mock_check_key.return_value = True
    auth_setup.cmd_check()
    out = json.loads(capsys.readouterr().out)
    assert out["authenticated"] is True


@patch("onedep_lib.check_auth_key")
@patch("onedep_lib.config.DepositConfig")
def test_check_exits_nonzero_when_key_invalid(mock_config_cls, mock_check_key, capsys):
    mock_config_cls.load.return_value = _fake_config(refresh_token="rt")
    mock_check_key.return_value = False
    with pytest.raises(SystemExit) as exc:
        auth_setup.cmd_check()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["authenticated"] is False


def test_login_fails_cleanly_without_env_var(monkeypatch, capsys):
    monkeypatch.delenv("ONEDEP_REFRESH_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        auth_setup.cmd_login()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "ONEDEP_REFRESH_TOKEN" in out["error"]
    # the suggested re-run command must use the venv-prefixed convention,
    # not a bare `python3` that would fail with ModuleNotFoundError
    assert ".venv/bin/python3" in out["error"]


@patch("onedep_lib.auths.token.TokenStore")
@patch("onedep_lib.config.DepositConfig")
def test_login_succeeds_and_reports_next_step(mock_config_cls, mock_token_store_cls, monkeypatch, capsys):
    monkeypatch.setenv("ONEDEP_REFRESH_TOKEN", "pasted-token")
    mock_config_cls.load.return_value = _fake_config(refresh_token="pasted-token")
    store = MagicMock()
    mock_token_store_cls.return_value = store

    auth_setup.cmd_login()

    store.refresh.assert_called_once()
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert "unset ONEDEP_REFRESH_TOKEN" in out["next_step"]


@patch("onedep_lib.auths.token.TokenStore")
@patch("onedep_lib.config.DepositConfig")
def test_login_reports_clean_error_on_refresh_failure(mock_config_cls, mock_token_store_cls, monkeypatch, capsys):
    from onedep_lib.exceptions import AuthError

    monkeypatch.setenv("ONEDEP_REFRESH_TOKEN", "bad-token")
    mock_config_cls.load.return_value = _fake_config(refresh_token="bad-token")
    store = MagicMock()
    store.refresh.side_effect = AuthError("Refresh token is expired, revoked, or invalid")
    mock_token_store_cls.return_value = store

    with pytest.raises(SystemExit) as exc:
        auth_setup.cmd_login()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "Login failed" in out["error"]


@patch("onedep_lib.config.DepositConfig")
def test_main_check_converts_uncaught_exception_to_json(mock_config_cls, monkeypatch, capsys):
    # Reproduces the live-verified bug: a malformed ~/.config/onedep/config.toml
    # makes DepositConfig.load() raise ConfigError, which used to leak as a
    # raw traceback since auth_setup.py's main() had no exception handling
    # at all - unlike em_deposit.py's main(), which was fixed first.
    from onedep_lib.exceptions import ConfigError

    mock_config_cls.load.side_effect = ConfigError("Failed to parse config.toml: bad TOML")
    monkeypatch.setattr(sys, "argv", ["auth_setup.py", "check"])

    with pytest.raises(SystemExit) as exc:
        auth_setup.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "ConfigError" in out["error"]
