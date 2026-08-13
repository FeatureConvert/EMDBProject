"""Unit tests for common.py's shared helpers."""

from __future__ import annotations

import json

import pytest
from conftest import load_script_module

common = load_script_module("common")


def test_json_argument_parser_converts_usage_errors_to_json(capsys):
    # Found by actually running the scripts with bad CLI arguments:
    # argparse's default error() prints plain text to stderr and calls
    # sys.exit(2), entirely bypassing run_cli()'s JSON contract since
    # parser.parse_args() runs before dispatch(). JsonArgumentParser fixes
    # this for missing/invalid arguments AND unknown subcommands.
    parser = common.JsonArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--manifest", required=True)

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["prepare"])  # missing --manifest
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "--manifest" in out["error"]

    with pytest.raises(SystemExit):
        parser.parse_args(["bogus"])  # invalid subcommand
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "bogus" in out["error"]


def test_json_argument_parser_help_still_prints_plain_text_and_exits_zero(capsys):
    # --help is a different code path (parser.exit(), not parser.error())
    # and should stay human-readable, not converted to JSON.
    parser = common.JsonArgumentParser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_require_fields_passes_when_all_present():
    common.require_fields({"a": 1, "b": 2}, ["a", "b"])  # must not raise


def test_em_subtype_enum_normalizes_spaces_and_hyphens_like_country_enum():
    # country_enum() already normalized "united kingdom"/"united-kingdom";
    # em_subtype_enum() didn't, despite this project's own docs describing
    # subtypes as "single particle" in prose (SKILL.md: "SPA / helical /
    # subtomogram / tomography").
    import onedep_lib as dsp

    assert common.em_subtype_enum("single particle") == dsp.EMSubType.SPA
    assert common.em_subtype_enum("single-particle") == dsp.EMSubType.SPA
    assert common.em_subtype_enum("SPA") == dsp.EMSubType.SPA
    assert common.em_subtype_enum("helical") == dsp.EMSubType.HELICAL


def test_save_manifest_is_atomic(tmp_path):
    # Matches onedep_lib's own JsonSessionStore._save() pattern: write to a
    # temp file, then os.replace() - so a crash mid-write can't leave a
    # truncated file that fails to parse on the next read.
    path = tmp_path / "manifest.json"
    common.save_manifest(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    assert not (tmp_path / "manifest.json.tmp").exists()

    common.save_manifest(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 2}


def test_require_submission_safety_gates_checks_confirm_first():
    with pytest.raises(SystemExit) as exc:
        common.require_submission_safety_gates(
            False, "dry-run", {"remote_dep_id": "D_1"}, "remote_dep_id", force=False, kind="deposition"
        )
    assert exc.value.code == 1


def test_require_submission_safety_gates_checks_resubmission_second():
    with pytest.raises(SystemExit) as exc:
        common.require_submission_safety_gates(
            True, "dry-run", {"remote_dep_id": "D_1"}, "remote_dep_id", force=False, kind="deposition"
        )
    assert exc.value.code == 1


def test_require_submission_safety_gates_passes_when_both_satisfied():
    common.require_submission_safety_gates(
        True, "dry-run", {}, "remote_dep_id", force=False, kind="deposition"
    )  # must not raise


def test_load_optional_json_returns_empty_dict_when_missing(tmp_path):
    assert common.load_optional_json(tmp_path / "nope.json") == {}


def test_load_optional_json_reads_existing_file(tmp_path):
    p = tmp_path / "marker.json"
    p.write_text(json.dumps({"entry_id": "123"}))
    assert common.load_optional_json(p) == {"entry_id": "123"}


def test_load_optional_json_fails_cleanly_on_corrupt_file(tmp_path, capsys):
    p = tmp_path / "marker.json"
    p.write_text("{not valid json")
    with pytest.raises(SystemExit):
        common.load_optional_json(p, label="Marker")
    out = json.loads(capsys.readouterr().out)
    assert "Marker" in out["error"]


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
