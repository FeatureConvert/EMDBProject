"""Unit tests for em_deposit.py, mocking onedep_lib entirely. No network access."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import load_script_module

em_deposit = load_script_module("em_deposit")


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


def test_preview_writes_review_file_without_touching_the_network(tmp_path, capsys):
    # preview is read-only and local: it must not import a session or reach
    # onedep_lib's network path. It renders coerced values and flags a
    # missing file rather than erroring on it.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"]["spacing_x"] = 1  # int -> shown as 1.0
    manifest["country"] = "United Kingdom"  # display string -> resolved to UK
    manifest_path.write_text(json.dumps(manifest))

    em_deposit.cmd_preview(str(manifest_path))
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True

    preview_path = tmp_path / "submission_preview.md"
    assert preview_path.exists()
    text = preview_path.read_text()
    assert "United Kingdom (`UK`)" in text
    assert "x=1.0" in text  # coerced int -> float
    assert "**EM subtype:** SPA" in text
    # no session key was written to the manifest
    assert "session_id" not in json.loads(manifest_path.read_text())


def test_preview_reports_missing_file(tmp_path, capsys):
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = str(tmp_path / "gone.mrc")  # never created
    manifest_path.write_text(json.dumps(manifest))

    em_deposit.cmd_preview(str(manifest_path))
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert "MISSING" in (tmp_path / "submission_preview.md").read_text()


def test_preview_validates_manifest_first(tmp_path, fails_json):
    # A preview must never render a payload submit would reject - it runs
    # the same validation as prepare.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"]["spacing_x"] = float("inf")
    manifest_path.write_text(json.dumps(manifest))
    fails_json(lambda: em_deposit.cmd_preview(str(manifest_path)), "finite number")


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


@pytest.mark.parametrize("bad_files", ["not-a-list", {"a": 1}, 42, None])
def test_prepare_fails_cleanly_when_files_is_not_a_list(bad_files, tmp_path, capsys):
    # Found by actually running prepare with files set to a string/dict:
    # both are truthy and iterable, so the old code iterated CHARACTERS
    # (for a string) or DICT KEYS (for a dict) as if they were file
    # entries, producing a deeply confusing error like "got str: 'n'"
    # (the first character) instead of pointing at the real mistake.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = bad_files
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "must be a list" in out["error"]


@pytest.mark.parametrize("field, bad_value", [("file_type", 123), ("path", 42)])
def test_prepare_fails_cleanly_when_files_entry_field_is_not_a_string(field, bad_value, tmp_path, fails_json):
    # require_fields only checked presence; a numeric/null path or file_type
    # would otherwise crash later with a generic AttributeError instead of
    # naming which field and file is wrong.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][1][field] = bad_value
    manifest_path.write_text(json.dumps(manifest))

    fails_json(lambda: em_deposit.cmd_prepare(str(manifest_path)), "must be a string")


@pytest.mark.parametrize("bad_value", [True, False])
def test_prepare_rejects_boolean_voxel_values(bad_value, tmp_path, fails_json):
    # bool is an int subclass, so float(True)==1.0 - without an explicit
    # guard a JSON true/false voxel value silently becomes a plausible-
    # looking spacing/contour of 1.0/0.0 instead of being rejected.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"]["spacing_x"] = bad_value
    manifest_path.write_text(json.dumps(manifest))

    fails_json(lambda: em_deposit.cmd_prepare(str(manifest_path)), "not a valid number")


def test_prepare_rejects_overflowing_integer_voxel_value(tmp_path, fails_json):
    # A huge integer is valid JSON but float() raises OverflowError, which
    # must be caught with the same clean per-field message, not escape to
    # run_cli's generic handler.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"]["spacing_x"] = int("1" + "0" * 400)
    manifest_path.write_text(json.dumps(manifest))

    fails_json(lambda: em_deposit.cmd_prepare(str(manifest_path)), "not a valid number")


@pytest.mark.parametrize("bad_value", ["false", "no", 1, 0])
def test_prepare_rejects_non_boolean_coordinates(bad_value, tmp_path, fails_json):
    # bool("false") is True - a bool() coercion would silently invert the
    # field, registering an atomic model the user never had. Must error.
    manifest_path = _manifest(tmp_path, coordinates=bad_value)
    fails_json(lambda: em_deposit.cmd_prepare(str(manifest_path)), "coordinates")


@pytest.mark.parametrize("bad_users", ["0000-0002-5109-8728", [], [123], None])
def test_prepare_rejects_malformed_users(bad_users, tmp_path, fails_json):
    # onedep_lib passes users through with no runtime validation, so one
    # ORCID as a bare string (instead of a list) would otherwise reach real
    # wwPDB submit unchecked.
    manifest_path = _manifest(tmp_path, users=bad_users)
    fails_json(lambda: em_deposit.cmd_prepare(str(manifest_path)), "users")


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_validates_before_opening_a_session(mock_deposit_init, mock_config_cls, tmp_path, fails_json):
    # A local validation failure (here, a NaN voxel value) must fail BEFORE
    # deposit_init is ever called - otherwise a bad manifest leaves an
    # orphaned onedep_lib session behind, the exact thing the pre-session
    # validation pass exists to prevent.
    mock_config_cls.load.return_value = _fake_config()
    mock_deposit_init.return_value = MagicMock(session_id="sess-1")

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"]["spacing_x"] = float("nan")
    manifest_path.write_text(json.dumps(manifest))  # json.dumps emits bare NaN by default

    fails_json(lambda: em_deposit.cmd_prepare(str(manifest_path)), "finite number")
    mock_deposit_init.assert_not_called()


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_rejects_unknown_file_type_before_opening_a_session(
    mock_deposit_init, mock_config_cls, tmp_path, fails_json
):
    # Enum resolution is a pure local lookup - an unknown file_type must
    # fail before deposit_init, not from inside the registration loop.
    mock_config_cls.load.return_value = _fake_config()
    mock_deposit_init.return_value = MagicMock(session_id="sess-1")

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][1]["file_type"] = "EM_MPA"  # typo for EM_MAP
    manifest_path.write_text(json.dumps(manifest))

    fails_json(lambda: em_deposit.cmd_prepare(str(manifest_path)), "Unknown file type")
    mock_deposit_init.assert_not_called()


def test_prepare_rejects_duplicate_file_paths(tmp_path, capsys):
    # Two entries pointing at the same physical file used to silently
    # register as two distinct files in the onedep_lib session (since
    # add_file() itself does no path-uniqueness check), which could pass
    # the required-file *count* check while actually uploading the same
    # bytes twice under two different roles.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    dup_path = manifest["files"][0]["path"]
    manifest["files"][1]["path"] = dup_path  # half1 now points at the map file
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "same file path twice" in out["error"]


def test_prepare_fails_cleanly_on_file_entry_missing_path(tmp_path, capsys):
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    del manifest["files"][1]["path"]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "path" in out["error"]


def test_prepare_fails_cleanly_on_voxel_missing_contour(tmp_path, capsys):
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    del manifest["files"][0]["voxel"]["contour"]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "contour" in out["error"]


def test_prepare_fails_cleanly_on_voxel_that_is_not_an_object(tmp_path, capsys):
    # A manifest with "voxel": null (a plausible authoring mistake - e.g. a
    # template field left unfilled) used to crash with a raw TypeError from
    # `f not in None` inside require_fields, since only key *presence* was
    # checked, never that the container was actually a dict.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"] = None
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "object" in out["error"].lower()


def test_prepare_does_not_require_voxel_fields_on_non_map_like_entries(tmp_path, capsys):
    # _validate_files_section used to require complete voxel sub-fields on
    # ANY entry carrying a "voxel" key, even though only map-like file types
    # ever have their voxel data read - a stray/incomplete voxel block on an
    # ENTRY_IMAGE entry shouldn't block prepare for data that's never used.
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][3]["voxel"] = {"spacing_x": 1.0}  # ENTRY_IMAGE entry, incomplete voxel
    manifest_path.write_text(json.dumps(manifest))

    with patch("onedep_lib.config.DepositConfig") as mock_config_cls, \
         patch("onedep_lib.deposit_init") as mock_deposit_init:
        mock_config_cls.load.return_value = _fake_config()
        dep = MagicMock()
        dep.session_id = "sess-1"
        dep.add_file.side_effect = ["fid-1", "fid-2", "fid-3", "fid-4"]
        mock_deposit_init.return_value = dep

        em_deposit.cmd_prepare(str(manifest_path))  # must not raise

    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_coerces_voxel_values_to_float(mock_deposit_init, mock_config_cls, tmp_path):
    # A manifest author writing a whole-number spacing as `1` instead of
    # `1.0` is valid JSON and valid per require_fields (presence-only), but
    # json.loads() yields an int, not a float - onedep_lib expects floats.
    mock_config_cls.load.return_value = _fake_config()
    dep = MagicMock()
    dep.session_id = "sess-1"
    dep.add_file.side_effect = ["fid-1", "fid-2", "fid-3", "fid-4"]
    mock_deposit_init.return_value = dep

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"] = {"spacing_x": 1, "spacing_y": 1, "spacing_z": 1, "contour": 0}
    manifest_path.write_text(json.dumps(manifest))

    em_deposit.cmd_prepare(str(manifest_path))

    call = dep.set_voxel_values.call_args
    assert all(isinstance(v, float) for v in call.kwargs.values())


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_rejects_non_finite_voxel_values(mock_deposit_init, mock_config_cls, bad_value, tmp_path, capsys):
    # Found by hands-on testing: json.loads() accepts the bare tokens
    # NaN/Infinity/-Infinity as a non-standard extension (Python's json
    # module does this by default), so a manifest with "spacing_x": NaN
    # parses successfully and used to sail through prepare as a normal-
    # looking float with no complaint, only to fail later (if at all)
    # against wwPDB's own server-side validation at submit time.
    mock_config_cls.load.return_value = _fake_config()
    mock_deposit_init.return_value = MagicMock(session_id="sess-1")

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"]["spacing_x"] = bad_value
    manifest_path.write_text(json.dumps(manifest))  # json.dumps also emits bare NaN/Infinity by default

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "finite number" in out["error"]


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_rejects_non_numeric_voxel_value(mock_deposit_init, mock_config_cls, tmp_path, capsys):
    mock_config_cls.load.return_value = _fake_config()
    mock_deposit_init.return_value = MagicMock(session_id="sess-1")

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["voxel"]["spacing_x"] = "not-a-number"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_prepare(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "not a valid number" in out["error"]


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_persists_progress_incrementally_so_retry_does_not_reregister(
    mock_deposit_init, mock_config_cls, tmp_path
):
    # Reproduces the live-verified bug: if add_file() fails partway through
    # the loop, files registered earlier in that same run must already be
    # saved to the manifest - otherwise a retry re-adds them, registering
    # the same physical file twice in the onedep_lib session store.
    mock_config_cls.load.return_value = _fake_config()
    dep = MagicMock()
    dep.session_id = "sess-1"
    dep.add_file.side_effect = ["fid-1", "fid-2", RuntimeError("boom on 3rd file")]
    mock_deposit_init.return_value = dep

    manifest_path = _manifest(tmp_path)

    with pytest.raises(RuntimeError):
        em_deposit.cmd_prepare(str(manifest_path))

    # dep.close() must still have run despite the exception
    dep.close.assert_called_once()

    # the two files that succeeded before the failure must be persisted
    saved = json.loads(manifest_path.read_text())
    assert saved["files"][0]["file_id"] == "fid-1"
    assert saved["files"][1]["file_id"] == "fid-2"
    assert "file_id" not in saved["files"][2]


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_applies_voxel_regardless_of_file_type_case(mock_deposit_init, mock_config_cls, tmp_path):
    # file_type_enum() normalizes case (e.g. "em_map" resolves fine), so the
    # voxel-application check must key off the resolved enum, not a raw
    # string comparison against the manifest's exact spelling.
    mock_config_cls.load.return_value = _fake_config()
    dep = MagicMock()
    dep.session_id = "sess-1"
    dep.add_file.side_effect = ["fid-1", "fid-2", "fid-3", "fid-4"]
    mock_deposit_init.return_value = dep

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["file_type"] = "em_map"  # lowercase
    manifest_path.write_text(json.dumps(manifest))

    em_deposit.cmd_prepare(str(manifest_path))

    dep.set_voxel_values.assert_called_once()
    assert dep.set_voxel_values.call_args.args[0] == "fid-1"


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


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_init")
def test_prepare_clears_stale_file_ids_when_session_is_recreated(mock_deposit_init, mock_config_cls, tmp_path):
    # Reproduces the documented "session_id no longer exists locally"
    # recovery path: a manifest can have file_id values left over from a
    # session that no longer exists (session_id was deleted per
    # troubleshooting.md's own advice). Since deposit_init() always starts
    # a genuinely fresh session, those stale ids must not be trusted -
    # otherwise the loop skips add_file() (id "already there") and then
    # set_voxel_values() raises a KeyError against a session that has
    # never actually seen that file.
    mock_config_cls.load.return_value = _fake_config()
    dep = MagicMock()
    dep.session_id = "sess-new"
    dep.add_file.side_effect = ["fid-new-1", "fid-new-2", "fid-new-3", "fid-new-4"]
    mock_deposit_init.return_value = dep

    manifest_path = _manifest(tmp_path)  # no session_id -> _open_deposition creates a fresh one
    manifest = json.loads(manifest_path.read_text())
    for i, f in enumerate(manifest["files"]):
        f["file_id"] = f"stale-fid-{i}"  # left over from a since-deleted session
    manifest_path.write_text(json.dumps(manifest))

    em_deposit.cmd_prepare(str(manifest_path))

    # every file must have been re-added against the new session, not
    # skipped because a stale id was still present
    assert dep.add_file.call_count == 4
    saved = json.loads(manifest_path.read_text())
    assert [f["file_id"] for f in saved["files"]] == ["fid-new-1", "fid-new-2", "fid-new-3", "fid-new-4"]


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_submit_prints_single_json_object_when_close_fails_after_failed_check(
    mock_resume, mock_config_cls, tmp_path, capsys
):
    # Live-reproduced bug: dep.close() used to run in a `finally` shared
    # with the check_required_files-failed branch's print_json()+sys.exit(1).
    # If close() itself raised while that SystemExit was propagating,
    # Python replaces the pending SystemExit with the close() exception -
    # an Exception subclass, which run_cli then also converts to JSON,
    # producing TWO JSON objects on one stdout stream. Fixed by closing
    # (best-effort) before printing on this path, not after.
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
    dep.close.side_effect = RuntimeError("session store lock error")
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1")
    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)
    assert exc.value.code == 1

    stdout = capsys.readouterr().out
    # must be exactly one JSON object, not two concatenated together
    obj = json.loads(stdout)
    assert obj["success"] is False
    assert "issues" in obj


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_submit_reports_dep_id_even_if_save_manifest_fails(mock_resume, mock_config_cls, tmp_path, capsys):
    # deposit() has already happened for real - a failure persisting it to
    # disk (permissions, disk full, concurrent writer) must not suppress
    # reporting the real dep_id back to the caller.
    mock_config_cls.load.return_value = _fake_config(authenticated=True)
    dep = MagicMock()
    report = MagicMock()
    report.ok = True
    dep.check_required_files.return_value = report
    dep.deposit.return_value = "D_8000000004"
    dep.site_url = "https://deposit-pdbe.wwpdb.org/deposition/D_8000000004/"
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1")
    with patch("em_deposit.save_manifest", side_effect=OSError("disk full")) as mock_save:
        em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)

    # Assert the patch actually reached the module under test - otherwise
    # (as happened before conftest registered modules in sys.modules) this
    # test passes vacuously: the real save_manifest succeeds against tmp_path,
    # no OSError is ever raised, and the try/except being tested is untested.
    assert mock_save.called, "patched save_manifest was never called - test is vacuous"

    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["remote_dep_id"] == "D_8000000004"


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
def test_submit_persists_remote_dep_id_even_if_site_url_access_fails(mock_resume, mock_config_cls, tmp_path, capsys):
    # deposit() has already happened for real by the time site_url is read.
    # If reading it raises, that must not prevent remote_dep_id from being
    # saved (it's already true), and must not turn a real success into a
    # reported failure - the guard_resubmission() gate depends on
    # remote_dep_id actually being persisted.
    mock_config_cls.load.return_value = _fake_config(authenticated=True)
    dep = MagicMock()
    report = MagicMock()
    report.ok = True
    dep.check_required_files.return_value = report
    dep.deposit.return_value = "D_8000000003"
    type(dep).site_url = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1")
    em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)

    dep.close.assert_called_once()
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["remote_dep_id"] == "D_8000000003"
    assert out["site_url"] is None

    saved = json.loads(manifest_path.read_text())
    assert saved["remote_dep_id"] == "D_8000000003"


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_submit_closes_session_even_when_deposit_raises(mock_resume, mock_config_cls, tmp_path, capsys):
    mock_config_cls.load.return_value = _fake_config(authenticated=True)
    dep = MagicMock()
    report = MagicMock()
    report.ok = True
    dep.check_required_files.return_value = report
    dep.deposit.side_effect = RuntimeError("network blip")
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1")
    with pytest.raises(RuntimeError):
        em_deposit.cmd_submit(str(manifest_path), confirm=True, force=False)

    dep.close.assert_called_once()
    # deposit() never returned an id, so nothing should have been saved
    saved = json.loads(manifest_path.read_text())
    assert saved.get("remote_dep_id") is None


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_dry_run_closes_session_even_when_check_required_files_raises(mock_resume, mock_config_cls, tmp_path, capsys):
    mock_config_cls.load.return_value = _fake_config()
    dep = MagicMock()
    dep.check_required_files.side_effect = RuntimeError("transient failure")
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1")
    with pytest.raises(RuntimeError):
        em_deposit.cmd_dry_run(str(manifest_path))

    dep.close.assert_called_once()


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_status_closes_session_even_when_get_status_raises(mock_resume, mock_config_cls, tmp_path, capsys):
    mock_config_cls.load.return_value = _fake_config(authenticated=True)
    dep = MagicMock()
    dep.get_status.side_effect = RuntimeError("transient failure")
    mock_resume.return_value = dep

    manifest_path = _manifest(tmp_path, session_id="sess-1", remote_dep_id="D_1")
    with pytest.raises(RuntimeError):
        em_deposit.cmd_status(str(manifest_path))

    dep.close.assert_called_once()


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


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_status_exits_nonzero_on_deposit_error(mock_resume, mock_config_cls, tmp_path, capsys):
    # get_status() can return a DepositError instead of raising - the exit
    # code must reflect failure too, not just the printed "error" key, so a
    # caller checking only the exit code doesn't mistake this for success.
    mock_config_cls.load.return_value = _fake_config(authenticated=True)

    class FakeDepositError:  # no .status attribute, unlike a real status result
        def __str__(self) -> str:
            return "processing failed: bad map header"

    dep = MagicMock()
    dep.get_status.return_value = FakeDepositError()
    mock_resume.return_value = dep

    manifest_path = _manifest(
        tmp_path, session_id="sess-1", remote_dep_id="D_8000000001"
    )
    with pytest.raises(SystemExit) as exc:
        em_deposit.cmd_status(str(manifest_path))
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out


@patch("onedep_lib.config.DepositConfig")
@patch("onedep_lib.deposit_resume")
def test_main_converts_onedep_lib_exception_to_json(mock_resume, mock_config_cls, tmp_path, capsys, monkeypatch):
    # Any uncaught FileNotFoundError/RuntimeError/OneDepError from inside a
    # cmd_* function must come out as JSON on stdout via main()'s dispatch
    # try/except, not a raw traceback - this is the "every script always
    # prints JSON" contract SKILL.md relies on to parse results.
    mock_config_cls.load.return_value = _fake_config(authenticated=True)
    mock_resume.side_effect = FileNotFoundError("File not found: /nope.mrc")

    manifest_path = _manifest(tmp_path, session_id="sess-1", remote_dep_id="D_1")
    monkeypatch.setattr(
        sys, "argv", ["em_deposit.py", "status", "--manifest", str(manifest_path)]
    )

    with pytest.raises(SystemExit) as exc:
        em_deposit.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "FileNotFoundError" in out["error"]
