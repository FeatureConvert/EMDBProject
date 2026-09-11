---
name: emdb-deposition-v2-beta
description: BETA/v2 systematic, question-driven variant of the EMDB/EMPIAR deposition skill. Trigger ONLY on an explicit ask for this beta/v2/wizard flow - phrases like "use the EMDB v2 beta skill", "run the beta deposition wizard", "emdb-deposition-v2-beta", or the user naming this skill directly. Do NOT trigger on generic deposit requests ("deposit this map to EMDB", "submit my cryo-EM structure", etc.) - those belong to the standard emdb-deposition skill, which this skill must not replace or alter.
---

# EMDB / EMPIAR deposition — v2 beta (systematic, question-driven)

This is a beta redesign of the `emdb-deposition` skill. It drives the same
two backing Python scripts (`onedep_lib` for EMDB/OneDep, `empiar-depositor`
for EMPIAR) via Bash, reading their structured JSON output — nothing about
the underlying scripts, schemas, or wwPDB/EMPIAR APIs has changed. What's
different in this beta is the *conversation*: it's run as an explicit,
numbered wizard, and every decision point that has a finite set of answers
is presented as clickable options via `AskUserQuestion` instead of asked in
plain text.

## Core interaction rule — read this before doing anything else

**Use `AskUserQuestion` for every step in this workflow that has a finite or
enumerable answer set.** That covers: which deposition type, EM subtype,
yes/no decisions (include coordinates? include an FSC curve? any related
EMDB entries? ready to proceed?), country (see the Country step below), and
every confirmation gate. Never ask these in plain conversational text when
`AskUserQuestion` can present them as options — that plain-text fallback is
exactly what this beta exists to eliminate.

Reserve ordinary typed/text input for the handful of fields that are
genuinely free-form and have no natural finite option set: **email address,
ORCID iD(s), file paths, and numeric voxel-spacing/contour values.** There
is no GUI file-picker or numeric-slider tool available in this environment,
so those four categories are the only places this flow still asks the user
to type something in chat. Batch each of those into as few plain-text
prompts as reasonably possible (e.g. all file paths in one message) rather
than one typed prompt per field, so the "mostly clicking, not typing" feel
holds even for the free-text portions.

Ask one `AskUserQuestion` call per logical step (it supports up to 4
questions per call — group only questions that are genuinely independent
and both needed at the same point, don't force-fit unrelated steps together
just to save a round-trip). Mark the most likely/recommended choice first
in each option list per the tool's own convention.

## Step 0 — show setup requirements up front, always

Before checking auth, before asking anything else, **surface the one-time
setup requirements to the user first**, every time this skill starts —
don't wait for an auth failure to explain what's needed. Summarize (or
show) `references/setup_checklist.md`'s contents: an ORCID account, a wwPDB
OneDep API refresh token (with the `ONEDEP_HOSTNAME` gotcha called out
explicitly — see that file), and — only if they'll be depositing EMPIAR raw
data too — an EMPIAR API token, EMPIAR transfer password, and an installed
transfer tool (Aspera or Globus). This lets the user immediately recognize
"oh, I already have that" vs. "I need to go do that first" before investing
time answering deposition questions.

Then run the actual check:

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/auth_setup.py check
```

If `"authenticated": false`, stop here and point the user at
`references/setup_checklist.md` to complete login themselves in their own
browser/shell — this skill cannot do that step on their behalf (no
browser-login flow exists in `onedep_lib`, and refresh tokens must never be
pasted into chat). Do not proceed to Step 1 until `check` reports
`"authenticated": true`.

## Step 1 — what are you depositing?

```
AskUserQuestion:
  "What would you like to deposit?"
  - EMDB map (Recommended if you have a density map ready)
  - EMPIAR raw data (movies/micrographs)
  - Both an EMDB map and EMPIAR raw data
```

Branch into the matching workflow below based on the answer. "Both" means
run the EMDB workflow through to a successful submit, then the EMPIAR
workflow (EMPIAR entries can reference the resulting EMD- accession as a
`related_emdb` cross-reference — see Step 2 of the EMDB workflow).

## Scope reminder — say this before Step 1, not after submit

Read `references/em_deposition_fields.md`. The short version:
`onedep_lib` covers deposition creation and core file upload only — the
primary map, half-maps, preview image, voxel/contour metadata, and
optionally a coordinate file. It does **not** cover the five detailed
experimental sections OneDep's web wizard also collects (Specimen
Preparation, Microscopy, Image Recording, Reconstruction,
Fitting/Interpretation). Tell the user this up front, before they start
answering questions, not as a surprise after `submit` succeeds: they'll
still need to log into the OneDep web UI afterward to fill those in before
the entry can be validated and released.

## EMDB map deposition workflow

Work through these as separate, numbered `AskUserQuestion` steps — don't
collapse them into one big free-text ask.

### 2a. EM subtype

```
AskUserQuestion:
  "What kind of EM reconstruction is this?"
  - Single particle (SPA) (Recommended — most common)
  - Helical
  - Subtomogram averaging
  - Tomography (no half-maps required)
```

### 2b. Coordinates

```
AskUserQuestion:
  "Does this deposition include a fitted atomic coordinate model (mmCIF)?"
  - No (Recommended if this is a map-only deposition)
  - Yes
```

### 2c. FSC curve

```
AskUserQuestion:
  "Do you have an FSC curve (XML) to include?"
  - Yes (Recommended — wwPDB encourages this, though it isn't enforced)
  - No
```

### 2d. Related EMDB entries

```
AskUserQuestion:
  "Is this entry related to any existing EMDB accession (composite,
  consensus, or focused-refinement workflow)?"
  - No
  - Yes
```

If yes, ask for the accession(s) as text (`EMD-XXXXX`), then verify each one
actually exists before writing it into the manifest:

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/emdb_lookup.py exists --accession EMD-XXXXX
```

If it doesn't, tell the user rather than silently writing an unverified
accession into the manifest.

### 2e. Country

Full ISO country lists don't fit in 4 options, so offer the two most common
depositor countries as one-click choices and let `AskUserQuestion`'s
built-in "Other" cover everything else (typing a country name there is
still far less typing than the full plain-text ask this replaces):

```
AskUserQuestion:
  "What country is the depositor in?"
  - United States
  - Canada
  (user can select "Other" and type any other country)
```

Accepts either a `Country` enum name or the exact wwPDB display string —
see `references/em_deposition_fields.md`. Match loosely/case-insensitively
regardless of how the user typed a non-listed country.

### 2f. Free-text fields — batch into one prompt

Everything else has no natural finite option set. Ask for all of it
together, in one message, rather than one field at a time:

- Depositor email
- ORCID iD(s) — at least one (format `0000-0002-XXXX-XXXX`)
- File paths: primary map, two half-maps (skip for Tomography), preview
  image (500×500), the mmCIF file (if step 2b was Yes), the FSC XML (if
  step 2c was Yes)
- Voxel spacing (x/y/z, in Å) and a recommended contour level for the
  primary map and each half-map

### 2g. Write the manifest

Write `depositions/<slug>/manifest.json` per the schema in
`references/em_deposition_fields.md`. Pick `<slug>` from context (sample
name, EMD accession if known) rather than asking — that's not a decision
point worth a question.

### 2h. Prepare

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/em_deposit.py prepare --manifest depositions/<slug>/manifest.json
```

Safe to re-run any time the manifest changes.

### 2i. Preview

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/em_deposit.py preview --manifest depositions/<slug>/manifest.json
```

Show the resulting `submission_preview.md` content to the user.

### 2j. Dry-run

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/em_deposit.py dry-run --manifest depositions/<slug>/manifest.json
```

If `"ok": false`, fix the manifest and repeat 2h–2j — don't proceed.

### 2k. Confirm and submit

Once dry-run passes, gate the actual submission behind an explicit choice,
not an inferred "looks good":

```
AskUserQuestion:
  "Preview and dry-run both look good. This creates a live deposition on
  wwPDB's production system — there is no sandbox, and it can't be undone.
  Submit now?"
  - Yes, submit
  - No, not yet
```

Only on "Yes, submit":

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/em_deposit.py submit --manifest depositions/<slug>/manifest.json --confirm
```

If the manifest already has a `remote_dep_id`, `submit` refuses without
`--force` — treat that refusal as a fresh question for the user
(`AskUserQuestion`: "This deposition was already submitted once. Force a
re-run against the existing deposition?" Yes/No), not something to route
around automatically.

### 2l. Status checks

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/em_deposit.py status --manifest depositions/<slug>/manifest.json
```

### 2m. After submit

Remind the user about the OneDep web UI step for the detailed experimental
sections (see the Scope reminder above), and that their eventual
publication should cite EMDB itself alongside the `EMD-` accession — see
[README.md's Citing section](../../../README.md#citing).

## EMPIAR raw-data deposition workflow

EMPIAR's JSON_INPUT format is used directly. Ask the enumerable fields
(experiment type, release-date policy, imageset category codes) via
`AskUserQuestion` using the enum values from
`empiar_deposition.schema.json` (bundled in the installed
`empiar_depositor` package) as the options. Ask the free-form fields
(title, authors, corresponding author, principal investigator, imageset
directories, citation) in one batched prompt, same principle as 2f above.

### 3a. Validate

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/empiar_deposit.py validate --json-input depositions/<slug>/empiar/json_input.json
```

### 3b. Preview

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/empiar_deposit.py preview --json-input depositions/<slug>/empiar/json_input.json [--data-dir <path-to-data>]
```

Show `json_input.preview.md` to the user.

### 3c. Confirm and submit

```
AskUserQuestion:
  "Validation and preview both look good. Submitting creates a live,
  citable EMPIAR entry via the API immediately, even before any data
  transfer completes — there's no dry-run that avoids this. Submit now?"
  - Yes, submit
  - No, not yet
```

Only on "Yes, submit":

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/empiar_deposit.py submit --json-input depositions/<slug>/empiar/json_input.json --data-dir <path-to-data> --confirm
```

`submit` refuses without a resolvable transfer method (`--ascp` /
auto-detected Aspera Connect, or `--globus <uuid>`). If it refuses, help
the user install/configure one rather than working around it — see
`references/setup_checklist.md`.

If a thumbnail is wanted, ask for its path explicitly (no automatic
fallback exists despite what `empiar-depositor --help` claims). If
resuming an interrupted transfer against the same entry, use `--resume
<entry_id> <entry_dir>` instead of `--force`.

On success, relay `entry_id`/`entry_directory` to the user — the citable
accession info — and mention citing EMPIAR itself alongside the
`EMPIAR-` accession (see
[README.md's Citing section](../../../README.md#citing)).

If `submit` fails on a missing `EMPIAR_API_TOKEN`/`EMPIAR_TRANSFER_PASS`,
point to `references/setup_checklist.md` — never ask the user to paste
either into chat.

## Where deposition state lives

Same as the standard skill: `depositions/<slug>/manifest.json` (EMDB) or
`depositions/<slug>/empiar/json_input.json` (EMPIAR), gitignored. Use
`list_depositions.py` for a multi-deposition overview:

```bash
.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/list_depositions.py
```

## Secrets — hard rule, not a preference

`ONEDEP_REFRESH_TOKEN`, `EMPIAR_API_TOKEN`, and `EMPIAR_TRANSFER_PASS` are
read from environment variables the user sets in their own shell. Never ask
the user to paste one of these into chat, never write one into a file
yourself, and never pass one as a Bash command argument — even if the user
offers. If a script reports a missing credential, the fix is always "the
user runs a command in their own terminal," not something to route around.

## Divergence from the standard `emdb-deposition` skill

This file is deliberately more prescriptive about *how* to hold the
conversation (numbered steps, explicit `AskUserQuestion` call shapes) than
the standard skill, which leaves that judgment to the model. The backing
scripts, manifest schema, and file-type rules are identical — see
`references/em_deposition_fields.md`, `references/setup_checklist.md`, and
`references/troubleshooting.md` in this folder (copied from the standard
skill; update both copies if the underlying scripts change, since this is
a beta fork, not a symlink).
