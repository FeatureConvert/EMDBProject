"""Unit tests for em_deposit.py, mocking onedep_lib entirely. No network access."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_em_deposit():
    spec = importlib.util.spec_from_file_location("em_deposit", SCRIPTS_DIR / "em_deposit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


em_deposit = _load_em_deposit()


def _manifest(tmp_path: Path, **overrides) -> Path:
    base = {
        "email": "depositor@example.org",
        "users": ["0000-0002-5109-8728"],
        "country": "UK",
        "em_subtype": "SPA",
        "coordinates": False,
        "files": [
            {
                "path": str(tmp_path / "map.mrc"),
                "file_type": "EM_MAP",
                "voxel": {"spacing_x": 1.0, "spacing_y": 1.0, "spacing_z": 1.0, "contour": 0.02},
            },
            {"path": str(tmp_path / "half1.mrc"), "file_type": "EM_HALF_MAP"},
            {"path": str(tmp_path / "half2.mrc"), "file_type": "EM_HALF_MAP"},
            {"path": str(tmp_path / "img.png"), "file_type": "ENTRY_IMAGE"},
        ],
    }
    base.update(overrides)
    for f in base["files"]:
        Path(f["path"]).write_bytes(b"placeholder")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(base))
    return manifest_path


def _fake_config(authenticated: bool = True):
    config = MagicMock()
    config.refresh_token = "rt" if authenticated else None
    return config


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_sets_em_params_and_registers_files(mock_deposit_init, mock_config_cls, tmp_path):
    mock_config_cls.load.return_value = _fake_config()
    dep = MagicMock()
    dep.session_id = "sess-1"
    dep.has_file.return_value = False
    dep.add_file.side_effect = ["fid-1", "fid-2", "fid-3", "fid-4"]
    mock_deposit_init.return_value = dep

    manifest_path = _manifest(tmp_path)
    em_deposit.cmd_prepare(str(manifest_path))

    import onedep_lib as dsp

    mock_deposit_init.assert_called_once()
    _, kwargs = mock_deposit_init.call_args
    assert kwargs["experiment_type"] == dsp.ExperimentType.EM
    assert kwargs["country"] == dsp.Country.UK

    dep.set_em_params.assert_called_once_with(em_subtype=dsp.EMSubType.SPA, coordinates=False)
    assert dep.add_file.call_count == 4

    # voxel values only set for the EM_MAP entry (map-like), not half-maps/image
    assert dep.set_voxel_values.call_count == 1
    call = dep.set_voxel_values.call_args
    assert call.args[0] == "fid-1"

    saved = json.loads(manifest_path.read_text())
    assert saved["session_id"] == "sess-1"
    assert all("file_id" in f for f in saved["files"])


def test_prepare_fails_cleanly_on_missing_required_field(tmp_path, capsys):
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    del manifest["email"]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1

    # Must still be valid JSON on stdout, not a raw KeyError traceback -
    # SKILL.md relies on every script's output being parseable.
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "email" in out["error"]


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_prepare_resumes_existing_session_without_reinit(mock_resume, mock_config_cls, tmp_path):
    mock_config_cls.load.return_value = _fake_config()
    dep = MagicMock()
    dep.session_id = "sess-existing"
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-existing")
    # Simulate files already registered in a prior prepare run: the
    # manifest's own file_id is what prepare treats as authoritative.
    manifest = json.loads(manifest_path.read_text())
    for i, f in enumerate(manifest["files"]):
        f["file_id"] = f"fid-{i}"
    manifest_path.write_text(json.dumps(manifest))

    em_deposit.cmd_prepare(str(manifest_path))

    mock_resume.assert_called_once()
    dep.add_file.assert_not_called()  # all entries already have file_id, nothing new to add
    # voxel values are still re-applied on every prepare run for map-like files
    dep.set_voxel_values.assert_called_once_with(
        "fid-0", spacing_x=1.0, spacing_y=1.0, spacing_z=1.0, contour=0.02
    )


def test_submit_refuses_without_confirm(tmp_path, capsys):
    manifest_path = _manifest(tmp_path, session_id="sess-1")
    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_submit(str(manifest_path), confirm=False, force=False)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "confirm" in out["error"].lower()


def test_submit_refuses_reused_dep_id_without_force(tmp_path, capsys):
    manifest_path = _manifest(tmp_path, session_id="sess-1", remote_dep_id="D_8000000001")
    with pytest.raises(SystemExit):
        em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "force" in out["error"].lower()


@patch("onedep_lib.config.DepositConfig")
def test_submit_refuses_without_auth(mock_config_cls, tmp_path, capsys):
    mock_config_cls.load.return_value = _fake_config(authenticated=False)
    manifest_path = _manifest(tmp_path, session_id="sess-1")
    with pytest.raises(SystemExit):
        em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "not authenticated" in out["error"].lower()


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_submit_calls_deposit_only_when_required_files_ok(mock_resume, mock_config_cls, tmp_path, capsys):
    mock_config_cls.load.return_value = _fake_config(authenticated=True)
    dep = MagicMock()
    report = MagicMock()
    report.ok = True
    dep.check_required_files.return_value = report
    dep.deposit.return_value = "D_8000000002"
    dep.site_url = "https://deposit-pdbe.wwpdb.org/deposition/D_8000000002/"
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1")
    em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)

    dep.deposit.assert_called_once()
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["remote_dep_id"] == "D_8000000002"
    assert out["site_url"] == "https://deposit-pdbe.wwpdb.org/deposition/D_8000000002/"

    saved = json.loads(manifest_path.read_text())
    assert saved["site_url"] == "https://deposit-pdbe.wwpdb.org/deposition/D_8000000002/"


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_submit_does_not_deposit_when_required_files_missing(mock_resume, mock_config_cls, tmp_path, capsys):
    mock_config_cls.load.return_value = _fake_config(authenticated=True)
    dep = MagicMock()
    issue = MagicMock()
    issue.severity.value = "fatal"
    issue.code = "REQ_FILES_MISSING"
    issue.message = "a map file is required for em"
    report = MagicMock()
    report.ok = False
    report.issues = [issue]
    dep.check_required_files.return_value = report
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1")
    with pytest.raises(SystemExit):
        em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)

    dep.deposit.assert_not_called()
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
