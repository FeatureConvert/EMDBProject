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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    country_enum,
    em_subtype_enum,
    fail,
    file_type_enum,
    load_manifest,
    print_json,
    require_fields,
    save_manifest,
)

MAP_LIKE_TYPES = {"EM_MAP", "EM_HALF_MAP", "EM_ADDITIONAL_MAP"}


def _build_config():
    """Load config without requiring auth - deposit_init/add_file/check_required_files
    are all local operations and don't need a token. Only submit/status do."""
    from onedep_lib.config import DepositConfig

    return DepositConfig.load()


def _require_auth(config) -> None:
    if config.refresh_token is None:
        fail("Not authenticated. Run `python3 auth_setup.py check` for details.")


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


def cmd_prepare(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    require_fields(manifest, ["email", "users", "country", "em_subtype", "files"])
    config = _build_config()

    dep, created = _open_deposition(manifest, config)
    if created:
        manifest["session_id"] = dep.session_id
        save_manifest(manifest_path, manifest)

    dep.set_em_params(
        em_subtype=em_subtype_enum(manifest["em_subtype"]),
        coordinates=bool(manifest.get("coordinates", False)),
    )

    files = manifest.get("files", [])
    if not files:
        fail("Manifest has no files listed under 'files'.")

    for entry in files:
        path = entry["path"]
        ftype = file_type_enum(entry["file_type"])
        # The manifest's own file_id is the source of truth for "already
        # registered" - onedep_lib's has_file() is a bool with no way to
        # recover an existing file_id by path, so it can't stand in here.
        if "file_id" not in entry:
            entry["file_id"] = dep.add_file(path, ftype)
        voxel = entry.get("voxel")
        if voxel and entry["file_type"] in MAP_LIKE_TYPES:
            dep.set_voxel_values(
                entry["file_id"],
                spacing_x=voxel["spacing_x"],
                spacing_y=voxel["spacing_y"],
                spacing_z=voxel["spacing_z"],
                contour=voxel["contour"],
            )

    dep.close()
    save_manifest(manifest_path, manifest)
    print_json(
        {
            "success": True,
            "session_id": manifest["session_id"],
            "files_registered": len(files),
        }
    )


def cmd_dry_run(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    if not manifest.get("session_id"):
        fail("No session_id in manifest yet. Run `prepare` first.")
    config = _build_config()
    dep, _ = _open_deposition(manifest, config)

    report = dep.check_required_files()
    dep.close()

    print_json(
        {
            "ok": report.ok,
            "issues": [
                {"severity": i.severity.value, "code": i.code, "message": i.message}
                for i in report.issues
            ],
        }
    )
    if not report.ok:
        sys.exit(1)


def cmd_submit(manifest_path: str, confirm: bool, force: bool) -> None:
    manifest = load_manifest(manifest_path)
    if not manifest.get("session_id"):
        fail("No session_id in manifest yet. Run `prepare` first.")
    if not confirm:
        fail("Refusing to submit without --confirm. Run `dry-run` first and review it with the user.")
    if manifest.get("remote_dep_id") and not force:
        fail(
            f"This manifest already has remote_dep_id={manifest['remote_dep_id']!r}. "
            "Re-running submit will re-upload files against the existing deposition. "
            "Pass --force if that's intentional."
        )

    config = _build_config()
    _require_auth(config)
    dep, _ = _open_deposition(manifest, config)

    report = dep.check_required_files()
    if not report.ok:
        dep.close()
        print_json(
            {
                "success": False,
                "error": "check_required_files failed - not submitting.",
                "issues": [
                    {"severity": i.severity.value, "code": i.code, "message": i.message}
                    for i in report.issues
                ],
            }
        )
        sys.exit(1)

    dep_id = dep.deposit()
    site_url = dep.site_url
    dep.close()

    manifest["remote_dep_id"] = dep_id
    if site_url:
        manifest["site_url"] = site_url
    save_manifest(manifest_path, manifest)
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
    if not manifest.get("session_id"):
        fail("No session_id in manifest yet. Run `prepare` first.")
    if not manifest.get("remote_dep_id"):
        fail("No remote_dep_id in manifest yet. Run `submit` first.")

    config = _build_config()
    _require_auth(config)
    dep, _ = _open_deposition(manifest, config)
    status = dep.get_status()
    dep.close()

    base = {"remote_dep_id": manifest["remote_dep_id"], "site_url": manifest.get("site_url")}
    if hasattr(status, "status"):
        print_json({**base, "status": status.status.value})
    else:
        print_json({**base, "error": str(status)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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

    if args.command == "prepare":
        cmd_prepare(args.manifest)
    elif args.command == "dry-run":
        cmd_dry_run(args.manifest)
    elif args.command == "submit":
        cmd_submit(args.manifest, args.confirm, args.force)
    elif args.command == "status":
        cmd_status(args.manifest)


if __name__ == "__main__":
    main()
