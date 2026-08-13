# EMDBProject

A Claude Skill that automates EMDB (cryo-EM density map) and EMPIAR (raw
image data) deposition to wwPDB, using wwPDB's own official Python
libraries (`onedep_lib`, `empiar-depositor`) instead of browser automation.

- [Why](#why)
- [What's here](#whats-here)
- [Installing this skill in your Claude client](#installing-this-skill-in-your-claude-client)
- [Setup](#setup)
- [Two ways to use this](#two-ways-to-use-this)
- [Tutorial: depositing an EMDB map](#tutorial-depositing-an-emdb-map)
- [Tutorial: depositing to EMPIAR](#tutorial-depositing-to-empiar)
- [Running the tests](#running-the-tests)
- [Scope and honesty check](#scope-and-honesty-check)
- [Troubleshooting](#troubleshooting)
- [Status](#status)

## Why

EMDB deposition normally goes through wwPDB's OneDep web wizard. wwPDB
publishes official programmatic clients for both OneDep and EMPIAR, so this
project drives those directly instead of scripting a browser against a UI
that can change at any time — a Chrome extension turned out to be
unnecessary once we found these.

## What's here

```
.claude/skills/emdb-deposition/
├── SKILL.md                          # conversational orchestration for Claude
├── requirements.txt
├── scripts/
│   ├── auth_setup.py                 # check/bootstrap wwPDB OneDep authentication
│   ├── em_deposit.py                 # EMDB map deposition: prepare / dry-run / submit / status
│   ├── empiar_deposit.py             # EMPIAR raw-data deposition: validate / submit
│   ├── common.py                     # shared manifest/validation/safety-gate helpers
│   └── tests/                        # unit tests for all four scripts, mocking both libraries entirely
└── references/
    ├── setup_checklist.md            # one-time account/token setup
    ├── em_deposition_fields.md       # manifest schema, what the API does/doesn't cover
    └── troubleshooting.md            # real error messages and fixes

depositions/                          # created per deposition, gitignored — not part of the skill
└── <slug>/
    ├── manifest.json                 # EMDB deposition state
    └── empiar/json_input.json        # EMPIAR deposition state
```

## Installing this skill in your Claude client

This is a **Claude Code project skill** — a `SKILL.md` file plus scripts
living in `.claude/skills/emdb-deposition/` inside this repo. It's built to
run Python scripts against a local venv and read/write files in this
project, so it needs [Claude Code](https://claude.com/product/claude-code)
(or another Claude Agent SDK–based client with filesystem + Bash access) —
it will not work through the plain claude.ai web chat, which has no
persistent local filesystem to run these scripts against.

**To use it (the confirmed, working path):**

1. Clone this repo: `git clone https://github.com/FeatureConvert/EMDBProject.git`
2. Open that directory as your project in Claude Code (e.g. `cd EMDBProject && claude`).

That's it — nothing to "install" separately. Claude Code automatically
discovers project-scoped skills under `.claude/skills/<name>/SKILL.md` for
whatever directory it's running in, so having this repo open *is* having
the skill available. It'll trigger automatically on relevant requests (e.g.
"deposit this map to EMDB," "help me submit this EMPIAR dataset"), or you
can just describe what you want and Claude will read the skill's
instructions and follow the tutorials below.

**If you want it available in other projects too** (not just this repo):
Claude Code also supports user-level skills, typically at
`~/.claude/skills/<name>/`, available regardless of which project you have
open. Copy this repo's `.claude/skills/emdb-deposition/` folder there if
you want that — we haven't verified the exact path/behavior on every Claude
Code version/environment, so check `claude --help` or the Claude Code docs
if it doesn't pick it up. Note that copying it out of this repo means it
loses access to `requirements.txt`-relative paths and the `.venv/` this
README assumes lives next to it — you'd need to adjust paths or keep a venv
alongside the copied skill folder too.

## Setup

### 1. Install dependencies into a project-local virtual environment

Don't `pip install` globally — macOS's system Python is externally managed
and will refuse it anyway. Use the venv:

```bash
python3 -m venv .venv
.venv/bin/pip install -r .claude/skills/emdb-deposition/requirements.txt
```

Every command below is prefixed with `.venv/bin/python3` for this reason —
a bare `python3` won't have `onedep_lib`/`empiar_depositor` installed.

### 2. One-time account and token setup

You need, at minimum, a wwPDB OneDep API refresh token before any EMDB
command that touches the network will work. Full instructions (ORCID
account, generating the token, EMPIAR token if you need that too, Aspera/
Globus for EMPIAR transfers) are in
[`references/setup_checklist.md`](.claude/skills/emdb-deposition/references/setup_checklist.md)
— it's a ~5 minute, one-time step done in your own browser, not something
Claude can do on your behalf. Skim it now if you haven't already.

### 3. Verify

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/auth_setup.py check
```

Expect `{"authenticated": true, ...}` once setup is complete. Until then
you'll see `{"authenticated": false, "reason": "No refresh token stored...", ...}`
— that's normal before you've logged in, and `prepare`/`dry-run` still
work fine without it (only `submit`/`status` need real auth).

## Two ways to use this

**Conversationally, through Claude** — open this project in Claude Code
and just describe what you want ("I need to deposit this cryo-EM map to
EMDB" / "help me submit this EMPIAR dataset"). `SKILL.md` triggers
automatically and walks the conversation through gathering the right
information, writing the manifest, running dry-run/validate, and asking
for your explicit confirmation before ever submitting for real. This is
the intended everyday path.

**Directly via the CLI scripts** — every example below works exactly the
same typed straight into your own terminal, no Claude involved. Useful for
scripting, CI, or just understanding exactly what the skill is doing under
the hood, since Claude is calling these same commands.

## Tutorial: depositing an EMDB map

This walks through a single-particle (SPA) map-only deposition end to end.
The same flow covers helical/subtomogram/tomography by changing
`em_subtype`, and joint map+model depositions by adding a coordinate file
(see the note at the end).

### Step 1 — write a manifest

Pick a short slug for this deposition (anything descriptive) and create
`depositions/<slug>/manifest.json`:

```bash
mkdir -p "depositions/my-protein-2026-08"
```

```json
{
  "email": "you@example.org",
  "users": ["0000-0002-XXXX-XXXX"],
  "country": "UK",
  "em_subtype": "SPA",
  "coordinates": false,
  "files": [
    {
      "path": "/absolute/path/to/primary_map.mrc",
      "file_type": "EM_MAP",
      "voxel": {"spacing_x": 1.05, "spacing_y": 1.05, "spacing_z": 1.05, "contour": 0.02}
    },
    {"path": "/absolute/path/to/half_map_1.mrc", "file_type": "EM_HALF_MAP"},
    {"path": "/absolute/path/to/half_map_2.mrc", "file_type": "EM_HALF_MAP"},
    {"path": "/absolute/path/to/preview.png", "file_type": "ENTRY_IMAGE"}
  ]
}
```

Field meanings, valid `file_type`/`em_subtype`/`country` values, and which
files are required for which subtype are all documented in
[`references/em_deposition_fields.md`](.claude/skills/emdb-deposition/references/em_deposition_fields.md)
— read it if anything above is unclear, rather than guessing.

### Step 2 — prepare

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py prepare \
  --manifest depositions/my-protein-2026-08/manifest.json
```

This is entirely local — it doesn't touch the network or need you to be
authenticated. It creates a local staging session, registers every file
listed in the manifest, and writes the resulting `session_id` and per-file
`file_id`s back into the manifest. Expect:

```json
{
  "success": true,
  "session_id": "059570ef-3546-412b-bdb5-18f3c4f9dc9f",
  "files_registered": 4
}
```

Safe to re-run any time you change the manifest (add a file, fix a voxel
value) — it won't duplicate already-registered files.

### Step 3 — dry-run

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py dry-run \
  --manifest depositions/my-protein-2026-08/manifest.json
```

Also local-only. Checks that you have the right files and counts for your
`em_subtype` (via wwPDB's own bundled schema — the same rules the real
OneDep wizard enforces). A clean manifest returns:

```json
{"ok": true, "issues": []}
```

If something's missing, you'll see specific, actionable messages instead,
e.g.:

```json
{
  "ok": false,
  "issues": [
    {"severity": "fatal", "code": "REQ_FILES_MISSING", "message": "an image file is required for em"}
  ]
}
```

Fix the manifest and re-run `prepare` then `dry-run` until `"ok": true`.
Don't move on until it is — `submit` will refuse otherwise anyway.

### Step 4 — authenticate (first time only)

If `auth_setup.py check` reported `authenticated: false` earlier, do that
now — see [setup_checklist.md](.claude/skills/emdb-deposition/references/setup_checklist.md).
This is the only step in the whole flow that needs your own browser.

### Step 5 — submit

This is the one real, hard-to-undo step — it creates a live deposition on
wwPDB's **production** system (there's no sandbox to rehearse against).
Be sure about the dry-run report before running this:

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py submit \
  --manifest depositions/my-protein-2026-08/manifest.json --confirm
```

```json
{
  "success": true,
  "remote_dep_id": "D_8000000001",
  "site_url": "https://deposit-pdbe.wwpdb.org/deposition/D_8000000001/",
  "note": "Core files uploaded and processing triggered. The depositor still needs to complete the detailed experimental sections ... in the OneDep web UI at the site_url above before this entry can be validated and released."
}
```

Note the `--confirm` flag — `submit` refuses to do anything without it.
If you (or the skill, in a conversation) run `submit` again against a
manifest that already has a `remote_dep_id`, it'll refuse again unless you
add `--force`, since that would re-upload everything.

### Step 6 — go finish the entry in the OneDep web UI

Open the `site_url` from the previous step. This tool doesn't cover the
five detailed experimental sections (Specimen Preparation, Microscopy,
Image Recording, Reconstruction, Fitting/Interpretation) — see
[Scope and honesty check](#scope-and-honesty-check) below for why. You'll
fill those in there before the entry can be validated and released.

### Checking status later

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/em_deposit.py status \
  --manifest depositions/my-protein-2026-08/manifest.json
```

### Joint map + atomic coordinate depositions

Add a coordinate file entry and flip `coordinates` to `true` — nothing
else changes:

```json
{
  "...": "...",
  "coordinates": true,
  "files": [
    "...",
    {"path": "/absolute/path/to/model.cif", "file_type": "MMCIF_COORD"}
  ]
}
```

## Tutorial: depositing to EMPIAR

EMPIAR uses its own submission format directly — there's no manifest
translation layer, you build the actual EMPIAR JSON_INPUT file. The
authoritative schema is bundled in the installed package at
`.venv/lib/python*/site-packages/empiar_depositor/empiar_deposition.schema.json`,
and a full real example ships alongside it at
`.venv/lib/python*/site-packages/empiar_depositor/tests/deposition_json/working_example.json`
— copying and adapting that example is the fastest way to get a valid
starting point.

### Step 1 — build the JSON_INPUT

```bash
mkdir -p depositions/my-protein-2026-08/empiar
cp .venv/lib/python3.*/site-packages/empiar_depositor/tests/deposition_json/working_example.json \
   depositions/my-protein-2026-08/empiar/json_input.json
```

Edit it with your real title, authors, principal investigator, imagesets
(pointing at your actual micrograph/tilt-series directories), and
citation. Field meanings for the more cryptic bits (imageset `category`
codes, `release_date` codes, `experiment_type` codes) are documented as
`description` text directly in the schema file.

### Step 2 — validate

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/empiar_deposit.py validate \
  --json-input depositions/my-protein-2026-08/empiar/json_input.json
```

Local-only, no network. `{"ok": true, "issues": []}` means it's
structurally valid; otherwise you get a list of `{"path", "message"}`
pairs telling you exactly which field is wrong.

### Step 3 — set up transfer credentials

You need `EMPIAR_API_TOKEN` and `EMPIAR_TRANSFER_PASS` set as environment
variables in your own shell (see
[setup_checklist.md](.claude/skills/emdb-deposition/references/setup_checklist.md))
and either Aspera Connect (`ascp`) or `globus-cli` installed for the
actual data transfer — **this is required, not optional**: `submit`
refuses to run at all without one of them, because `empiar-depositor`
creates the live EMPIAR entry via its API *before* attempting any
transfer, and would otherwise silently create a real, empty entry with no
data uploaded. If Aspera Connect is installed at its OS-default location,
it's auto-detected — no `--ascp` flag needed.

### Step 4 — submit

Real upload, real EMPIAR entry creation — confirm you mean it. This is
even more irreversible than EMDB's `submit`: the entry gets created
server-side before any data transfer starts, so there's no equivalent of
"it failed cleanly, nothing happened."

```bash
.venv/bin/python3 .claude/skills/emdb-deposition/scripts/empiar_deposit.py submit \
  --json-input depositions/my-protein-2026-08/empiar/json_input.json \
  --data-dir /path/to/your/micrographs_root \
  --confirm
```

```json
{
  "success": true,
  "entry_id": "12345",
  "entry_directory": "abcde12345",
  "command": ["...", "***", "..."],
  "returncode": 0,
  "stdout_tail": "...",
  "stderr_tail": ""
}
```

`entry_id`/`entry_directory` are parsed from empiar-depositor's own output
— relay these to whoever needs the citable accession info. A sidecar
`depositions/my-protein-2026-08/empiar/json_input.submitted.json` file
records them; re-running `submit` against the same JSON_INPUT afterward
refuses unless you also pass `--force`, same pattern as EMDB's
`remote_dep_id` guard.

Useful optional flags: `--ascp /path/to/ascp` (non-default Aspera
location), `--globus <uuid>` (use Globus instead of/as a fallback to
Aspera), `--thumbnail /path/to/image.png` (defaults to the related EMDB
entry's image if omitted), `--resume <entry_id> <entry_dir>` (continue an
interrupted Aspera upload rather than starting over).

## Running the tests

```bash
.venv/bin/python3 -m pytest .claude/skills/emdb-deposition/scripts/tests/ -v
```

These mock both `onedep_lib` and `empiar_depositor` entirely — no network,
no real credentials needed. Run them after any change to the scripts.

## Scope and honesty check

`onedep_lib` covers deposition creation and core file upload (map,
half-maps, image, voxel/contour metadata, optional atomic coordinates) —
not the five detailed experimental sections (Specimen Prep, Microscopy,
Image Recording, Reconstruction, Fitting/Interpretation) that OneDep's web
wizard also collects. Those still need to be completed in the OneDep web UI
after this tool creates the deposition and uploads files — `submit` prints
the exact `site_url` to go finish that at. See
[`references/em_deposition_fields.md`](.claude/skills/emdb-deposition/references/em_deposition_fields.md)
for the full breakdown of what is and isn't automated.

Nothing in this project calls a real submit/deposit/transfer action
without an explicit `--confirm` flag, and the skill is expected to get an
explicit "yes, submit" from you in chat before ever passing it. Secrets
(`ONEDEP_REFRESH_TOKEN`, `EMPIAR_API_TOKEN`, `EMPIAR_TRANSFER_PASS`) are
read from your own shell's environment variables only — never typed into
chat, never written to a file by the skill, never passed as a CLI
argument.

## Troubleshooting

See [`references/troubleshooting.md`](.claude/skills/emdb-deposition/references/troubleshooting.md)
for real error messages (quoted from the actual libraries and scripts)
and their fixes — authentication issues, manifest/schema errors, EMPIAR
transfer problems, and where to look if something isn't covered there.

## Status

EMDB map-only, map+coordinates, and EMPIAR validation paths are built and
tested — both with mocked unit tests and manually end to end against the
real, installed libraries (no real credentials used). Two rounds of
multi-angle code review (line-by-line, removed-behavior audits, cross-file
consistency, Python-specific pitfalls, wrapper-correctness, reuse,
efficiency, and depth-of-fix checks) caught and fixed real issues along the
way, several confirmed by actually reproducing them against the installed
libraries rather than by static reasoning alone — a session-resume bug that
duplicated file registrations, a silent-failure mode where an EMPIAR entry
could be created with no data uploaded, session leaks on exception paths,
and two of the three scripts (`auth_setup.py`, `empiar_deposit.py`) having
no exception handling of their own despite being expected to always emit
JSON. All three scripts now route every uncaught exception through one
shared safety net (`common.run_cli()`). 56 tests currently pass. Real
`submit`/transfer has not yet been exercised against production
wwPDB/EMPIAR — that only happens when you're ready with an actual
deposition.
