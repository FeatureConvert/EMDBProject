"""Read-only lookups of released EMDB entries (via the `emdb` REST client).

Unlike the deposition scripts, this one talks to the PUBLIC EMDB API for
already-released entries - it is anonymous (no token), read-only, and never
writes or submits anything. Two uses:

  - Confirm a related accession actually exists before you cite it in a
    manifest's `related_emdb` (composite-map cross-referencing).
  - Pull an existing entry's metadata (title, authors, method, resolution,
    voxel spacing, contour, related PDB/EMDB/EMPIAR ids) to help pre-fill or
    sanity-check a new deposition.

Usage:
  .venv/bin/python3 .claude/skills/emdb-deposition/scripts/emdb_lookup.py lookup --accession EMD-8000
  .venv/bin/python3 .claude/skills/emdb-deposition/scripts/emdb_lookup.py exists --accession EMD-8000

Notes:
  - Network is hit only when a subcommand runs (not at import), matching the
    other scripts. The `emdb` client's timeout is fixed at 10s upstream.
  - `lookup` also reports linked EMPIAR accessions (from the entry's
    annotations) when present - the `emdb` package has no standalone EMPIAR
    lookup, only this EMDB->EMPIAR cross-reference.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent))
from common import JsonArgumentParser, emdb_accession_problem, fail, print_json, run_cli  # noqa: E402


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    """The emdb models expose deeply-nested, sparsely-populated dicts whose
    shape varies entry to entry; pull each field behind a guard so one
    missing key never sinks the whole summary."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - best-effort field extraction, see docstring
        return default


def _authors(entry) -> Any:
    """Depositor author names. The live API returns admin['authors_list']
    either as a plain list or as a nested {'author': [{'valueOf_': 'Name'}]}
    (seen on real entries) - flatten the latter to a list of name strings."""
    raw = _safe(lambda: entry.admin["authors_list"])
    if isinstance(raw, dict):
        return _safe(
            lambda: [a["valueOf_"] if isinstance(a, dict) else a for a in raw["author"]],
            raw,
        )
    return raw


def _summarize(entry) -> dict:
    """Flatten an emdb EMDBEntry into a compact JSON-friendly summary. Every
    field is best-effort (None if absent)."""
    pm = _safe(lambda: entry.primary_map)
    pixel = _safe(lambda: pm.pixel_spacing) or {}

    def _spacing(axis: str):
        # pixel_spacing[axis]["valueOf_"] is a string like "1.385"; keep it
        # as-is (don't fabricate precision by float-rounding for display).
        return _safe(lambda: pixel[axis]["valueOf_"])

    return {
        "id": _safe(lambda: entry.id),
        "title": _safe(lambda: entry.admin["title"]),
        "authors": _authors(entry),
        "sample_name": _safe(lambda: entry.sample["name"]["valueOf_"]),
        "method": _safe(lambda: entry.method),
        "resolution": _safe(lambda: entry.resolution),
        "map": {
            "format": _safe(lambda: pm.format),
            "dimensions": _safe(lambda: pm.dimensions),
            "pixel_spacing": {"x": _spacing("x"), "y": _spacing("y"), "z": _spacing("z")},
            "contour_level": _safe(lambda: pm.contour_list["contour"][0]["level"]),
        },
        "related_pdb_ids": _safe(lambda: [r.get("pdb_id") for r in entry.related_pdb_ids], []),
        "related_emdb_ids": _safe(lambda: [r.get("emdb_id") for r in entry.related_emdb_ids], []),
    }


def _empiar_cross_refs(client, accession: str) -> list[str]:
    """Linked EMPIAR accessions for an EMDB entry, from its annotations.
    Best-effort - annotations may be unavailable for some entries."""
    def _pull():
        ann = client.get_annotations(accession)
        return [a.id for a in (ann.empiar or [])]

    return _safe(_pull, [])


def cmd_lookup(accession: str) -> None:
    problem = emdb_accession_problem(accession)
    if problem:
        fail(problem)

    from emdb.client import EMDB

    client = EMDB()
    entry = client.get_entry(accession)  # raises on missing/network error -> run_cli JSON
    summary = _summarize(entry)
    summary["related_empiar_ids"] = _empiar_cross_refs(client, accession)
    print_json({"success": True, "entry": summary})


def cmd_exists(accession: str) -> None:
    # Format check first (offline). A malformed accession is "does not exist"
    # rather than an error - the caller asked a yes/no question.
    if emdb_accession_problem(accession):
        print_json({"success": True, "accession": accession, "exists": False, "reason": "malformed accession"})
        return

    from emdb.client import EMDB
    from emdb.exceptions import EMDBAPIError, EMDBInvalidIDError

    client = EMDB()
    try:
        client.get_entry(accession)
    except EMDBInvalidIDError:
        print_json({"success": True, "accession": accession, "exists": False, "reason": "malformed accession"})
        return
    except EMDBAPIError as exc:
        # Gotcha (verified against the installed emdb 0.1.12): a genuine 404
        # is NOT surfaced as EMDBNotFoundError - make_request's broad except
        # re-wraps it as EMDBAPIError. So distinguish "not found" from a real
        # server/network error by the message, and re-raise the latter (run_cli
        # turns it into a JSON error) rather than reporting a false "absent".
        text = str(exc).lower()
        if "not found" in text or "404" in text:
            print_json({"success": True, "accession": accession, "exists": False, "reason": "not found in EMDB"})
            return
        raise
    print_json({"success": True, "accession": accession, "exists": True})


def main() -> None:
    parser = JsonArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lookup")
    p.add_argument("--accession", required=True)
    p.set_defaults(func=lambda args: cmd_lookup(args.accession))

    p = sub.add_parser("exists")
    p.add_argument("--accession", required=True)
    p.set_defaults(func=lambda args: cmd_exists(args.accession))

    args = parser.parse_args()
    run_cli(lambda: args.func(args))


if __name__ == "__main__":
    main()
