"""Drive an EMDB (EM map) deposition through onedep_lib, keyed by a manifest.

IMPORTANT SCOPE NOTE: onedep_lib's Deposition object only covers experiment
type, EM subtype, file upload (map/half-maps/image/optional coordinates),
and per-map voxel spacing + contour level. It has no methods for the five
detailed experimental sections OneDep's web wizard normally collects
(Specimen Preparation, Microscopy, Image Recording, Reconstruction, Fitting
/Interpretation). After `submit`, the depositor will likely still need to
log into the OneDep web UI to complete those sections before the entry can
be validated/released. This script automates deposition creation and core
file upload only - not full end-to-end submission.

Manifest schema (JSON, created/updated in place by this script):
{
  "email": "depositor@example.org",
  "users": ["0000-0002-XXXX-XXXX"],
  "country": "UK",
  "em_subtype": "SPA",              # SPA | HELICAL | SUBTOMOGRAM | TOMOGRAPHY
  "coordinates": false,               # true if an MMCIF_COORD file is included
  "session_id": null,                 # filled in by `prepare`
  "remote_dep_id": null,              # filled in by `submit`
  "files": [
    {"path": "/abs/path/map.mrc", "file_type": "EM_MAP",
     "voxel": {"spacing_x": 1.05, "spacing_y": 1.05, "spacing_z": 1.05, "contour": 0.02}},
    {"path": "/abs/path/half_map_1.mrc", "file_type": "EM_HALF_MAP"},
    {"path": "/abs/path/half_map_2.mrc", "file_type": "EM_HALF_MAP"},
    {"path": "/abs/path/preview.png", "file_type": "ENTRY_IMAGE"}
  ]
}

Subcommands:
  prepare --manifest <path>            create/resume local session, register files (no network beyond schema fetch, which is bundled locally)
  dry-run --manifest <path>            check_required_files() only - never calls deposit()
  submit  --manifest <path> --confirm  calls deposit() - real, hard-to-undo submission to wwPDB
  status  --manifest <path>            poll get_status() for an already-submitted deposition
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    JsonArgumentParser,
    country_enum,
    em_subtype_enum,
    fail,
    file_type_enum,
    load_manifest,
    print_json,
    require_fields,
    require_submission_safety_gates,
    run_cli,
    save_manifest,
)

MAP_LIKE_TYPES = {"EM_MAP", "EM_HALF_MAP", "EM_ADDITIONAL_MAP"}
VOXEL_FIELDS = ("spacing_x", "spacing_y", "spacing_z", "contour")


def _build_config():
    """Load config without requiring auth - deposit_init/add_file/check_required_files
    are all local operations and don't need a token. Only submit/status do."""
    from onedep_lib.config import DepositConfig

    return DepositConfig.load()


def _require_auth(config) -> None:
    if config.refresh_token is None:
        fail(
            "Not authenticated. Run "
            "`.venv/bin/python3 .claude/skills/emdb-deposition/scripts/auth_setup.py check` "
            "for details."
        )


def _require_session(manifest: dict) -> None:
    if not manifest.get("session_id"):
        fail("No session_id in manifest yet. Run `prepare` first.")


def _issues_json(report) -> list[dict]:
    return [{"severity": i.severity.value, "code": i.code, "message": i.message} for i in report.issues]


def _coerce_voxel_floats(voxel: dict, path: str) -> dict[str, float]:
    """Convert each VOXEL_FIELDS value to float, rejecting non-finite
    results (NaN/Infinity). json.loads() accepts the bare tokens NaN/
    Infinity/-Infinity as a non-standard extension (Python's json module
    does this by default), so a manifest with "spacing_x": NaN parses
    successfully and would otherwise sail through as a normal-looking
    float with no complaint until wwPDB's own server-side validation
    rejects it at submit time - catch it locally instead, matching this
    project's general goal of catching what it can before that point."""
    out = {}
    for field in VOXEL_FIELDS:
        try:
            value = float(voxel[field])
        except (TypeError, ValueError) as exc:
            fail(f"voxel.{field} for {path!r} is not a valid number: {voxel[field]!r} ({exc})")
        if not math.isfinite(value):
            fail(f"voxel.{field} for {path!r} must be a finite number, got {value}")
        out[field] = value
    return out


def _close_quietly(dep) -> None:
    """Best-effort close, once we already have a real result to report.

    onedep_lib's current close() is a no-op that can't actually raise, but
    nothing guarantees that stays true in a future version, and relying on
    a third-party implementation detail for correctness is the wrong bet -
    a cleanup problem must never turn an already-completed, hard-to-undo
    action (or an already-computed, harmless read like check_required_files)
    into a reported failure. Call this ONLY after the real work for this
    command has already succeeded; a failure that happens before that point
    should still close-and-reraise so it propagates normally."""
    try:
        dep.close()
    except Exception:  # noqa: BLE001 - deliberately swallow, see docstring
        pass


def _open_deposition(manifest: dict, config):
    """Resume the manifest's onedep_lib session, or create one if none exists yet."""
    import onedep_lib as dsp

    session_id = manifest.get("session_id")
    if session_id:
        try:
            return dsp.deposit_resume(session_id, config=config), False
        except KeyError:
            fail(
                f"session_id {session_id!r} from the manifest no longer exists "
                "locally (~/.onedep/sessions). Remove session_id from the "
                "manifest and re-run prepare to start a new session."
            )

    dep = dsp.deposit_init(
        email=manifest["email"],
        users=manifest["users"],
        country=country_enum(manifest["country"]),
        experiment_type=dsp.ExperimentType.EM,
        config=config,
    )
    return dep, True


def _validate_files_section(files: list[dict]) -> None:
    """Validate manifest['files'] shape before any session is opened, so a bad
    manifest fails clean instead of leaving an orphaned local onedep_lib
    session behind (or crashing with a raw KeyError/TypeError from deep
    inside the registration loop)."""
    seen_paths: dict[str, str] = {}
    for entry in files:
        require_fields(entry, ["path", "file_type"], label="Each files[] entry")
        path = entry["path"]
        ftype_raw = entry["file_type"]
        # require_fields only checked presence, not type - a numeric or
        # null file_type/path would otherwise crash on the first .strip()
        # call below with a generic AttributeError instead of pointing at
        # which field and file is actually wrong.
        if not isinstance(path, str):
            fail(f"files[] entry 'path' must be a string, got {type(path).__name__}: {path!r}")
        if not isinstance(ftype_raw, str):
            fail(f"files[] entry 'file_type' must be a string, got {type(ftype_raw).__name__}: {ftype_raw!r}")
        if path in seen_paths:
            fail(
                f"Manifest lists the same file path twice: {path!r} is used for both "
                f"{seen_paths[path]!r} and {entry['file_type']!r}. Each physical file "
                "must be registered under exactly one file_type - onedep_lib doesn't "
                "detect duplicate paths itself, so two entries pointing at the same "
                "file would silently register as two distinct files."
            )
        seen_paths[path] = entry["file_type"]
        # Only map-like files ever have their voxel data read (see the
        # MAP_LIKE_TYPES check in cmd_prepare's registration loop) - a
        # "voxel" block on e.g. an ENTRY_IMAGE entry is never used, so don't
        # demand it be complete; that would only produce a confusing error
        # for data that was never going to matter.
        if "voxel" in entry and entry["file_type"].strip().upper() in MAP_LIKE_TYPES:
            require_fields(entry["voxel"], list(VOXEL_FIELDS), label=f"voxel block for {path!r}")


def cmd_prepare(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    require_fields(manifest, ["email", "users", "country", "em_subtype", "files"])

    files = manifest.get("files", [])
    # Check the type explicitly before iterating: a "files" value that's a
    # string or dict is truthy (so a bare `if not files` wouldn't catch it)
    # and iterable, but iterating it yields characters or dict keys - each
    # then fails the "must be an object" check with a confusing message
    # (e.g. "got str: 'n'", the first character of the string) instead of
    # a clear "files must be a list" error pointing at the actual mistake.
    if not isinstance(files, list):
        fail(f"Manifest's 'files' field must be a list, got {type(files).__name__}: {files!r}")
    if not files:
        fail("Manifest has no files listed under 'files'.")
    _validate_files_section(files)

    config = _build_config()

    dep, created = _open_deposition(manifest, config)
    if created:
        manifest["session_id"] = dep.session_id
        # A fresh session means any file_id values already recorded in the
        # manifest belong to a previous, now-gone session (e.g. a user
        # followed the "session_id no longer exists locally" recovery
        # advice and deleted just session_id). Those ids don't exist in
        # THIS session's store - clear them so the loop below re-registers
        # every file, instead of skipping add_file() and then crashing in
        # set_voxel_values() with a KeyError for an id the new session has
        # never seen.
        for entry in files:
            entry.pop("file_id", None)
        save_manifest(manifest_path, manifest)

    try:
        dep.set_em_params(
            em_subtype=em_subtype_enum(manifest["em_subtype"]),
            coordinates=bool(manifest.get("coordinates", False)),
        )

        for entry in files:
            ftype = file_type_enum(entry["file_type"])
            # The manifest's own file_id is the source of truth for "already
            # registered" - onedep_lib's has_file() is a bool with no way to
            # recover an existing file_id by path, so it can't stand in here.
            if "file_id" not in entry:
                entry["file_id"] = dep.add_file(entry["path"], ftype)
                # Persist after EVERY successful add_file, not once at the
                # end of the loop: if a later file in the list fails (e.g.
                # FileNotFoundError), the files that already succeeded must
                # stay recorded, or a retry will re-add them - registering
                # the same physical file twice in the onedep_lib session
                # store, which nothing else here would catch.
                save_manifest(manifest_path, manifest)
            voxel = entry.get("voxel")
            # Compare against the resolved enum's canonical name, not the raw
            # manifest string - file_type_enum() normalizes case (e.g.
            # "em_map" resolves fine), so a raw-string membership check here
            # would silently skip set_voxel_values() for anything not spelled
            # exactly like the MAP_LIKE_TYPES entries.
            if voxel and ftype.name in MAP_LIKE_TYPES:
                dep.set_voxel_values(entry["file_id"], **_coerce_voxel_floats(voxel, entry["path"]))
    except BaseException:
        _close_quietly(dep)
        raise

    # Everything succeeded and is already durably saved (incrementally,
    # above) - a cleanup problem here must not turn a real success into a
    # reported failure.
    _close_quietly(dep)
    print_json(
        {
            "success": True,
            "session_id": manifest["session_id"],
            "files_registered": len(files),
        }
    )


def cmd_dry_run(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    _require_session(manifest)
    config = _build_config()
    dep, _ = _open_deposition(manifest, config)

    try:
        report = dep.check_required_files()
    except BaseException:
        _close_quietly(dep)
        raise
    _close_quietly(dep)

    print_json({"ok": report.ok, "issues": _issues_json(report)})
    if not report.ok:
        sys.exit(1)


def cmd_submit(manifest_path: str, confirm: bool, force: bool) -> None:
    manifest = load_manifest(manifest_path)
    _require_session(manifest)
    require_submission_safety_gates(confirm, "dry-run", manifest, "remote_dep_id", force, kind="deposition")

    config = _build_config()
    _require_auth(config)
    dep, _ = _open_deposition(manifest, config)

    try:
        report = dep.check_required_files()
    except BaseException:
        _close_quietly(dep)
        raise

    if not report.ok:
        # Close BEFORE printing, not in a shared finally after: closing
        # inside the same try/finally as this print used to risk close()
        # raising while the pending sys.exit(1) below was propagating,
        # which replaces that SystemExit with the close() exception -
        # an Exception subclass, so run_cli's handler would catch it and
        # print a SECOND, unrelated JSON object after this one. Verified
        # this was reproducible before the fix (mocked dep.close() to
        # raise on this exact path).
        _close_quietly(dep)
        print_json(
            {
                "success": False,
                "error": "check_required_files failed - not submitting.",
                "issues": _issues_json(report),
            }
        )
        sys.exit(1)

    try:
        dep_id = dep.deposit()
    except BaseException:
        _close_quietly(dep)
        raise

    # deposit() has already happened for real on wwPDB at this point -
    # nothing from here on may suppress reporting dep_id. Each remaining
    # step (persisting it, reading site_url, closing) is independently
    # best-effort so a failure in one doesn't hide that the submission
    # itself succeeded.
    manifest["remote_dep_id"] = dep_id
    try:
        save_manifest(manifest_path, manifest)
    except Exception:  # noqa: BLE001 - dep_id is still reported below even if this fails
        pass

    site_url = None
    try:
        site_url = dep.site_url
    except Exception:  # noqa: BLE001
        pass

    _close_quietly(dep)

    if site_url:
        manifest["site_url"] = site_url
        try:
            save_manifest(manifest_path, manifest)
        except Exception:  # noqa: BLE001
            pass

    print_json(
        {
            "success": True,
            "remote_dep_id": dep_id,
            "site_url": site_url,
            "note": (
                "Core files uploaded and processing triggered. The depositor "
                "still needs to complete the detailed experimental sections "
                "(Specimen Preparation, Microscopy, Image Recording, "
                "Reconstruction, Fitting/Interpretation) in the OneDep web UI "
                "at the site_url above before this entry can be validated and released."
            ),
        }
    )


def cmd_status(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    _require_session(manifest)
    if not manifest.get("remote_dep_id"):
        fail("No remote_dep_id in manifest yet. Run `submit` first.")

    config = _build_config()
    _require_auth(config)
    dep, _ = _open_deposition(manifest, config)
    try:
        status = dep.get_status()
    except BaseException:
        _close_quietly(dep)
        raise
    _close_quietly(dep)

    base = {"remote_dep_id": manifest["remote_dep_id"], "site_url": manifest.get("site_url")}
    if hasattr(status, "status"):
        print_json({**base, "status": status.status.value})
    else:
        # get_status() can return a DepositError instead of raising - exit
        # non-zero so callers checking the exit code (not just scanning for
        # an "error" key) don't mistake this for a successful status check.
        print_json({**base, "error": str(status)})
        sys.exit(1)


def main() -> None:
    parser = JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--manifest", required=True)

    p = sub.add_parser("dry-run")
    p.add_argument("--manifest", required=True)

    p = sub.add_parser("submit")
    p.add_argument("--manifest", required=True)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("status")
    p.add_argument("--manifest", required=True)

    args = parser.parse_args()

    def dispatch() -> None:
        if args.command == "prepare":
            cmd_prepare(args.manifest)
        elif args.command == "dry-run":
            cmd_dry_run(args.manifest)
        elif args.command == "submit":
            cmd_submit(args.manifest, args.confirm, args.force)
        elif args.command == "status":
            cmd_status(args.manifest)

    run_cli(dispatch)


if __name__ == "__main__":
    main()
