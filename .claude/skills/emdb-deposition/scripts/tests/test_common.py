"""Unit tests for common.py's shared helpers."""

from __future__ import annotations

import json

import pytest
from conftest import load_script_module

common = load_script_module("common")


def test_require_fields_passes_when_all_present():
    common.require_fields({"a": 1, "b": 2}, ["a", "b"])  # must not raise


def test_require_fields_fails_on_missing(capsys):
    with pytest.raises(SystemExit) as exc:
        common.require_fields({"a": 1}, ["a", "b"])
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "b" in out["error"]


def test_require_fields_fails_cleanly_when_container_is_not_a_dict(capsys):
    # "voxel": null in a manifest used to crash with a raw TypeError from
    # `f not in None` - require_fields must catch the non-dict case itself.
    with pytest.raises(SystemExit) as exc:
        common.require_fields(None, ["a"])
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "object" in out["error"].lower()

    with pytest.raises(SystemExit):
        common.require_fields("not a dict", ["a"])


def test_require_confirm_passes_when_true():
    common.require_confirm(True, "dry-run")  # must not raise


def test_require_confirm_fails_when_false(capsys):
    with pytest.raises(SystemExit) as exc:
        common.require_confirm(False, "dry-run")
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "dry-run" in out["error"]
    assert "confirm" in out["error"].lower()


def test_guard_resubmission_passes_when_no_prior_id():
    common.guard_resubmission({}, "remote_dep_id", force=False, kind="deposition")  # must not raise


def test_guard_resubmission_passes_when_force_true():
    common.guard_resubmission(
        {"remote_dep_id": "D_1"}, "remote_dep_id", force=True, kind="deposition"
    )  # must not raise


def test_guard_resubmission_fails_when_id_present_and_not_forced(capsys):
    with pytest.raises(SystemExit) as exc:
        common.guard_resubmission({"remote_dep_id": "D_1"}, "remote_dep_id", force=False, kind="deposition")
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "D_1" in out["error"]
    assert "force" in out["error"].lower()


def test_run_cli_lets_success_through(capsys):
    common.run_cli(lambda: common.print_json({"success": True}))
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True


def test_run_cli_converts_arbitrary_exception_to_json(capsys):
    def boom():
        raise TypeError("argument of type 'NoneType' is not iterable")

    with pytest.raises(SystemExit) as exc:
        common.run_cli(boom)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "TypeError" in out["error"]


def test_run_cli_does_not_swallow_systemexit_from_fail(capsys):
    # fail() itself raises SystemExit via sys.exit() - run_cli must not
    # catch that and re-wrap it (SystemExit is a BaseException, not
    # Exception, so `except Exception` naturally excludes it - this locks
    # that behavior in).
    with pytest.raises(SystemExit) as exc:
        common.run_cli(lambda: common.fail("explicit failure"))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "explicit failure"


def test_load_manifest_uses_custom_label(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit):
        common.load_manifest(str(missing), label="JSON_INPUT")
    out = json.loads(capsys.readouterr().out)
    assert out["error"].startswith("JSON_INPUT not found")
