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


@pytest.mark.parametrize("fn_name", ["country_enum", "em_subtype_enum", "file_type_enum"])
def test_enum_lookups_fail_cleanly_on_non_string_input(fn_name, capsys):
    # Found by actually running em_deposit.py with a manifest where
    # country/em_subtype/file_type was a number: these used to crash with
    # a generic "AttributeError: 'int' object has no attribute 'strip'"
    # (safe, since run_cli catches it, but unhelpfully vague) instead of
    # pointing at which field was wrong.
    fn = getattr(common, fn_name)
    with pytest.raises(SystemExit) as exc:
        fn(123)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "must be a string" in out["error"]
    assert "123" in out["error"]


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


@pytest.mark.parametrize("loader", ["load_manifest", "load_optional_json"])
@pytest.mark.parametrize("non_object", ["[]", '"a string"', "42", "null"])
def test_loaders_reject_valid_json_that_is_not_an_object(loader, non_object, tmp_path, capsys):
    # json.loads happily returns lists/strings/numbers; callers all .get()
    # the result, so a non-object file must fail HERE (naming the file) not
    # later with a generic "AttributeError: 'list' object has no attribute
    # 'get'" that names nothing.
    p = tmp_path / "data.json"
    p.write_text(non_object)
    with pytest.raises(SystemExit) as exc:
        getattr(common, loader)(p, label="Manifest")
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "must contain a JSON object" in out["error"]


@pytest.mark.parametrize(
    "orcid",
    [
        "0000-0002-1825-0097",  # Josiah Carberry (well-known valid test ORCID)
        "0000-0002-5109-8728",  # valid checksum
        "https://orcid.org/0000-0002-1825-0097",  # URL form tolerated
        "0000-0001-5000-0007",  # valid checksum (X not needed here)
    ],
)
def test_orcid_problem_accepts_valid(orcid):
    assert common.orcid_problem(orcid) is None


@pytest.mark.parametrize(
    "orcid, needle",
    [
        ("0000-0002-1825-0098", "checksum"),  # last digit wrong
        ("0000-0002-1825", "not a valid ORCID"),  # too short
        ("000A-0002-1825-0097", "not a valid ORCID"),  # letter in body
        ("0000000218250097", "not a valid ORCID"),  # missing hyphens
        ("", "not a valid ORCID"),
    ],
)
def test_orcid_problem_rejects_invalid(orcid, needle):
    problem = common.orcid_problem(orcid)
    assert problem is not None
    assert needle in problem


def test_orcid_problem_accepts_trailing_x_checksum():
    # An ORCID whose checksum digit is X must be accepted. 0000-0003-1415-9265
    # is not necessarily valid; compute a known-X one instead: the checksum of
    # 0000-0001-5000-007 base resolves to X for this iD.
    assert common.orcid_problem("0000-0002-9079-593X") is None  # ORCID's own documented example


@pytest.mark.parametrize("email", ["a@b.org", "depositor@example.org", "x.y+z@sub.domain.co.uk"])
def test_email_problem_accepts_valid(email):
    assert common.email_problem(email) is None


@pytest.mark.parametrize("email", ["not-an-email", "no-at-sign.com", "missing@domain", "a b@c.org", ""])
def test_email_problem_rejects_invalid(email):
    assert common.email_problem(email) is not None


@pytest.mark.parametrize("name, expected", [("EM", "EM"), ("em", "EM"), ("xray", "XRAY"), ("NMR", "NMR")])
def test_experiment_type_enum_resolves_case_insensitively(name, expected):
    import onedep_lib as dsp

    assert common.experiment_type_enum(name) == dsp.ExperimentType[expected]


def test_experiment_type_enum_rejects_unknown(capsys):
    with pytest.raises(SystemExit) as exc:
        common.experiment_type_enum("cryoet")
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "Unknown experiment_type" in out["error"]


@pytest.mark.parametrize("acc", ["EMD-8000", "EMD-12345", "EMD-0001"])
def test_emdb_accession_problem_accepts_valid(acc):
    assert common.emdb_accession_problem(acc) is None


@pytest.mark.parametrize("acc", ["8000", "EMD-", "EMDB-8000", "EMD-abc", "emd-8000", 8000])
def test_emdb_accession_problem_rejects_invalid(acc):
    assert common.emdb_accession_problem(acc) is not None


def test_submitted_marker_path_replaces_suffix():
    # json_input.json -> json_input.submitted.json (suffix replaced, not
    # appended). Both the EMPIAR writer and the list_depositions reader
    # depend on this exact scheme.
    from pathlib import Path

    assert common.submitted_marker_path("a/json_input.json") == Path("a/json_input.submitted.json")
    assert common.submitted_marker_path(Path("x/data.json")).name == "data.submitted.json"


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
