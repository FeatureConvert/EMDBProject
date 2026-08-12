# EMDBProject

A Claude Skill that automates EMDB (cryo-EM density map) and EMPIAR (raw
image data) deposition to wwPDB, using wwPDB's own official Python
libraries (`onedep_lib`, `empiar-depositor`) instead of browser automation.

## Why

EMDB deposition normally goes through wwPDB's OneDep web wizard. wwPDB
publishes official programmatic clients for both OneDep and EMPIAR, so this
project drives those directly from Claude Code rather than scripting a
browser against a UI that can change at any time.

## What's here

`.claude/skills/emdb-deposition/` — the skill itself:

- `SKILL.md` — conversational orchestration (coming in Phase 5)
- `scripts/auth_setup.py` — check/bootstrap wwPDB OneDep authentication
- `scripts/em_deposit.py` — EMDB map deposition (prepare / dry-run / submit / status)
- `scripts/empiar_deposit.py` — EMPIAR raw-data deposition (validate / submit)
- `references/setup_checklist.md` — one-time account/token setup (ORCID, wwPDB API token, EMPIAR token, Aspera/Globus)
- `references/em_deposition_fields.md` — what the API does and doesn't cover, manifest field reference

## Scope and honesty check

`onedep_lib` covers deposition creation and core file upload (map,
half-maps, image, voxel/contour metadata, optional atomic coordinates) —
not the five detailed experimental sections (Specimen Prep, Microscopy,
Image Recording, Reconstruction, Fitting/Interpretation) that OneDep's web
wizard also collects. Those still need to be completed in the OneDep web UI
after this tool creates the deposition and uploads files. See
`references/em_deposition_fields.md` for the full breakdown.

Nothing in this skill calls a real submit/deposit/transfer action without
an explicit `--confirm` flag, and the skill is expected to get an explicit
"yes, submit" from the user in chat before ever passing it.

## Status

Rough first build. EMDB map-only and map+coordinates paths are built and
tested locally (dry-run/prepare, no real credentials used). EMPIAR JSON
validation is built and tested against wwPDB's own bundled example. Real
submission has not been exercised against production wwPDB/EMPIAR yet.
