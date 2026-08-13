---
name: emdb-deposition
description: Deposit cryo-EM/electron microscopy structures to EMDB and raw image/movie data to EMPIAR through wwPDB, driven conversationally instead of through the OneDep web wizard. Use this whenever the user wants to deposit, submit, or upload a cryo-EM map, half-maps, an EMDB entry, or EMPIAR raw data - phrases like "deposit this map to EMDB", "submit my cryo-EM structure", "upload to EMPIAR", "push this to wwPDB", or "I need to release my density map" should all trigger this skill, even if they don't name EMDB/EMPIAR/OneDep/onedep_lib explicitly and just describe having a map, half-maps, or micrographs ready to submit. Also use it for checking wwPDB/OneDep login status or deposition status.
---

# EMDB / EMPIAR deposition

This skill drives two backing Python scripts that wrap wwPDB's own official
client libraries (`onedep_lib` for EMDB/OneDep, `empiar-depositor` for
EMPIAR) — it does not do any browser automation or hand-written API calls.
Always call the scripts via Bash and read their structured JSON output;
never write `onedep_lib` or `empiar_depositor` calls inline. The scripts
were written once, reviewed, and unit-tested — trust them and use them as
the interface, rather than re-deriving the API surface each conversation.
Every script's `main()` guarantees this: any unexpected error, not just
the ones each script explicitly checks for, still comes out as
`{"success": false, "error": "..."}` on stdout rather than a raw Python
traceback, so it's always safe to parse stdout as JSON.

## Scope: what this actually automates

Read `references/em_deposition_fields.md` before telling a user their
deposition is "done." The short version: `onedep_lib` covers deposition
creation and core file upload — the primary map, half-maps, preview image,
voxel/contour metadata, and optionally an atomic coordinate file. It does
**not** cover the five detailed experimental sections OneDep's web wizard
also collects (Specimen Preparation, Microscopy, Image Recording,
Reconstruction, Fitting/Interpretation). Say this up front when a user
asks to deposit a map — after `submit` succeeds, they'll still need to log
into the OneDep web UI to fill in those sections before the entry can be
validated and released. Don't oversell this as fully hands-off.

## Environment

All scripts run under the project's venv, not a global Python:

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/<script>.py <args>
```

If `.venv/` doesn't exist yet, set it up first: `python3 -m venv .venv &&
.venv/bin/pip install -r .claude/skills/emdb-deposition/requirements.txt`.

## Step 0: check authentication first

At the start of any deposition-related conversation, run:

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/auth_setup.py check
```

If it reports `"authenticated": false`, stop and point the user at
`references/setup_checklist.md` — they need to get a wwPDB OneDep API
refresh token themselves (a one-time, ~5 minute step in their own browser)
before anything else can work. Do not attempt to work around this or ask
the user to paste a token into chat; the checklist explains exactly why
(tokens must come from environment variables the user sets themselves, and
`onedep_lib` has no browser-login flow of its own — it's genuinely not
something this skill can do on the user's behalf).

For EMPIAR specifically, the equivalent check is just whether
`EMPIAR_API_TOKEN` and `EMPIAR_TRANSFER_PASS` are set — there's no
`empiar_deposit.py check` command since `empiar-depositor` has no
lightweight auth-check call; `submit` will fail fast and clearly if either
is missing.

## Where deposition state lives

Create a working directory per deposition at the project root:
`depositions/<descriptive-slug>/manifest.json` for EMDB, or
`depositions/<descriptive-slug>/empiar/json_input.json` for EMPIAR. This
directory is gitignored — it holds real sample metadata and dep_ids, not
skill code. Pick the slug from context (sample name, EMD accession if
known) rather than asking the user to name it if it's obvious.

## EMDB map deposition workflow

1. **Gather what's needed conversationally**: depositor email, ORCID
   iD(s), country, EM subtype (SPA / helical / subtomogram / tomography),
   paths to the primary map + two half-maps (not required for tomography)
   + a preview image, voxel spacing (x/y/z) and a recommended contour
   level for each map-type file, and — if applicable — a path to a fitted
   atomic coordinate (mmCIF) file. See `references/em_deposition_fields.md`
   for the exact manifest JSON schema and the full `file_type` list.

2. **Write the manifest** to `depositions/<slug>/manifest.json` following
   that schema.

3. **Prepare** (local only, no submission, safe to re-run):
   ```bash
   .venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py prepare --manifest depositions/<slug>/manifest.json
   ```
   This creates or resumes a local session and registers all files. Safe
   to run again any time the manifest changes (e.g. a new file added) —
   it won't duplicate already-registered files, and it refuses upfront if
   two entries in `files[]` accidentally point at the same physical path
   (a real mistake to catch before it reaches wwPDB, not just a cosmetic
   check).

4. **Dry-run** before ever proposing a real submission:
   ```bash
   .venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py dry-run --manifest depositions/<slug>/manifest.json
   ```
   Report the result to the user in plain language. If `"ok": false`, walk
   through the listed issues (missing files, wrong counts) and fix the
   manifest, then re-run prepare/dry-run — don't proceed to submit.

5. **Get explicit confirmation, then submit.** This is the one real,
   hard-to-undo action in this whole flow — it creates a live deposition
   on wwPDB's production system (there is no sandbox to test against, per
   `references/setup_checklist.md`). Show the user the dry-run report and
   get an explicit "yes, submit" in chat. Only then run:
   ```bash
   .venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py submit --manifest depositions/<slug>/manifest.json --confirm
   ```
   Never pass `--confirm` speculatively or infer consent from something
   like "looks good" about the dry-run alone — ask directly. If the
   manifest already has a `remote_dep_id` (a previous submit already ran),
   `submit` will refuse unless you also pass `--force`; treat that refusal
   as a signal to check with the user rather than adding `--force`
   automatically.

6. **Status checks**, any time after submit:
   ```bash
   .venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py status --manifest depositions/<slug>/manifest.json
   ```

7. After a successful submit, remind the user about the OneDep web UI step
   for the detailed experimental sections (see Scope, above), and mention
   that their eventual publication should cite EMDB itself alongside the
   `EMD-` accession — see [README.md's Citing section](../../../README.md#citing)
   for the exact reference. This tool doesn't verify citations; it's the
   depositor's own responsibility, same as any deposition route.

## EMPIAR raw-data deposition workflow

EMPIAR's submission format (JSON_INPUT) is used directly — there's no
separate manifest translation layer, so build
`depositions/<slug>/empiar/json_input.json` in the actual EMPIAR schema
shape. Ask the user for the fields conversationally (title, release date
policy, experiment type, authors, corresponding author, principal
investigator, imagesets, citation) — see
`references/empiar_json_schema.md` if present, or the schema bundled in
the installed `empiar_depositor` package
(`.venv/lib/python*/site-packages/empiar_depositor/empiar_deposition.schema.json`)
for the authoritative field list and enum meanings (e.g. `release_date`
codes, `experiment_type` codes, imageset `category` codes).

1. **Validate** (local only, no network):
   ```bash
   .venv/bin/python3 .claude/skills/emdb-deposition/scripts/empiar_deposit.py validate --json-input depositions/<slug>/empiar/json_input.json
   ```
   Fix and re-run until `"ok": true`.

2. **Confirm, then submit.** Same rule as EMDB: show the validation
   result, get an explicit "yes" before adding `--confirm`. This step is
   even more irreversible than it looks: `empiar-depositor` creates the
   live EMPIAR entry via its API **before** attempting any data transfer,
   so a failed/skipped transfer after this point still leaves a real,
   citable-ID entry behind — there's no dry-run equivalent that avoids
   that.
   ```bash
   .venv/bin/python3 .claude/skills/emdb-deposition/scripts/empiar_deposit.py submit --json-input depositions/<slug>/empiar/json_input.json --data-dir <path-to-data> --confirm
   ```
   `submit` refuses to run at all unless it can resolve a transfer method —
   either `--ascp <path>` (or an auto-detected Aspera Connect install at
   its OS-default location) or `--globus <uuid>`. `empiar-depositor`'s own
   CLI would also refuse without one of these (confirmed by reading its
   source — it does **not**, despite this, auto-detect an installed
   Aspera Connect the way its `--help` text might suggest), so this check
   just fails faster with a clearer message. If `submit` refuses for this
   reason, help the user find/install Aspera Connect or set up
   `globus-cli` rather than working around it.

   Other flags: `--thumbnail <path>` (no fallback if omitted — despite
   what empiar-depositor's own `--help` text claims about defaulting to
   the related EMDB entry's image, no code anywhere actually implements
   that; if the user wants a thumbnail, get an explicit path), `--resume
   <entry_id> <entry_dir>` (resume an interrupted Aspera upload against
   the *same* entry — passing `--resume` bypasses the resubmission guard
   below entirely, since resuming isn't a duplicate submission; don't also
   pass `--force`, which has the opposite meaning). On success, the JSON
   output includes `entry_id`/`entry_directory` parsed from
   empiar-depositor's own output — relay these to the user, they're the
   citable accession info. If a `warning` field is present, the entry ID
   couldn't be parsed cleanly - surface that to the user rather than
   treating the run as a normal success. A sidecar
   `<json_input>.submitted.json` marker is written next to the JSON_INPUT
   file recording them; re-running `submit` against the same JSON_INPUT
   after a successful run refuses unless you also pass `--force` (or
   `--resume`, above), same pattern as EMDB's `remote_dep_id` guard.
   Mention that their publication should also cite EMPIAR itself alongside
   the `EMPIAR-` accession — see
   [README.md's Citing section](../../../README.md#citing).

3. If `submit` fails because `EMPIAR_API_TOKEN` or `EMPIAR_TRANSFER_PASS`
   isn't set, point the user at `references/setup_checklist.md` — don't
   ask them to give you the value directly.

## Secrets — hard rule, not a preference

`ONEDEP_REFRESH_TOKEN`, `EMPIAR_API_TOKEN`, and `EMPIAR_TRANSFER_PASS` are
read from environment variables the user sets in their own shell, by
design (see the scripts' docstrings for why). Never ask the user to paste
one of these into chat, never write one into a file yourself, and never
pass one as a Bash command argument — even if the user offers. If a script
reports a missing credential, the fix is always "the user runs a command
in their own terminal," not something to route around.
