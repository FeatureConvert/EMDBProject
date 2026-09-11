# Roadmap / expansion ideas

Researched while working autonomously (the user was away) — these are real
options, not vague brainstorming, but they involve scope/design decisions
that are the user's to make, not something to build unsupervised. See
[`list_depositions.py`](.claude/skills/emdb-deposition/scripts/list_depositions.py)
for one small, unambiguous addition, and the "considered and rejected" note
at the end for one that deliberately wasn't.

**Status update:** items **2** (other experiment types), **4** (local MRC
header sniff), and **5** (JSON Schema + ORCID/email checks) are
**implemented** — see the `DONE` markers. Item **1** (composite maps) is
**partially** done: the buildable part (a `related_emdb` field + web-UI
linking reminders) is in; the automated cross-referencing is blocked
upstream (onedep_lib has no such API). Item **3** (read-only lookups) is
**implemented** as `emdb_lookup.py`. Every roadmap item is now either done
or, for composite-map automation, blocked on an upstream API.

## 1. Composite map deposition support — PARTIAL (core blocked upstream)

**What it is:** EMDB has a community-recommended workflow for cryo-EM
structures assembled from multiple focused refinements plus an unfocused
consensus map. Each piece — composite, consensus, each focused refinement —
is deposited as its **own separate EMDB entry**, cross-referenced through a
"Related entries" mechanism.

**Spike result (the unknown, now resolved):** the installed `onedep_lib`
exposes **no** deposition-to-deposition cross-referencing API. The only
related-* symbols are two dormant fields on the internal `Experiment`
dataclass (`related_emdb`, `related_bmrb`) — no public setter, not persisted
in a `LocalSession`, never populated by `deposit_init`/`deposit()`. They
semantically link a *single* deposition to a *pre-existing* archive entry,
not compose a group. So automated cross-referencing **cannot** be built on
the current library; it would need an upstream API.

**What was built instead:** an optional `related_emdb` list in the manifest
(format-validated `EMD-XXXXX` accessions). `preview` and `submit` surface
these and instruct the depositor to link them by hand in the OneDep web UI's
"Related entries" section — the same "this part is done in the web UI" model
the project already uses for the five detailed experimental sections. Each
map is still its own deposition (its own manifest); this just records and
reminds about the relationships.

**Still not built (needs upstream or a design decision):** a single "group"
manifest that drives N related depositions in one command, and any automated
cross-referencing. Both await either an `onedep_lib` API or an explicit
decision to orchestrate multiple depositions locally.

**Recommendation:** the manual-linking support covers the realistic case
today; revisit full automation only if onedep_lib gains a cross-reference
API. The read-only `emdb` lookup (item 3) pairs well here for confirming a
related accession exists before citing it.

## 2. Other experiment types (X-ray, NMR, etc.) — DONE

**Status: implemented** (backward-compatibly). The manifest takes an optional
`experiment_type` (default `EM`; also `XRAY`, `NMR`, `SSNMR`, `NEUTRON`,
`FIBER`, `EC`). Verified against the installed `onedep_lib`: `deposit_init`
is experiment-type-agnostic and a non-EM deposition is driven entirely by the
file types registered, with `check_required_files()` enforcing the per-method
rules from onedep_lib's bundled `schemas/json/*.json`. `em_subtype`, voxel,
and the MRC sniff are gated to EM. An X-ray deposition (MMCIF_COORD +
CRYSTAL_STRUC_FACTORS) prepares and dry-runs `ok: true`. EM stays the default
and the skill's focus; the skill description remains EM-specific. Original
note below kept for context.

---
_(original entry)_

## 2b. Other experiment types — original assessment

**What it is:** `onedep_lib` isn't EM-specific — its own quickstart example
uses `ExperimentType.XRAY`, and the enum (`onedep_lib.ExperimentType`)
lists `XRAY`, `FIBER`, `NEUTRON`, `EM`, `EC`, `NMR`, `SSNMR`. This
project's scripts, manifest schema, and validation logic
(`_validate_files_section`, `MAP_LIKE_TYPES`, the required-files checks)
are all written EM-specifically.

**Why it's not built:** This was a deliberate scope decision from the
start (the user asked for an EMDB/EMPIAR deposition tool specifically).
Generalizing to X-ray/NMR would mean genuinely different manifest fields
(structure factors instead of half-maps, different required-file rules
per `onedep_lib`'s own bundled schemas for `xray.json`/`nmr.json`/etc.,
already visible in the installed package's `schemas/json/` directory) -
not a small tweak.

**Effort:** Medium (the underlying API already generalizes; the wrapper
scripts would need real rework, not just parameterization). **Value:**
depends entirely on whether the user works with other experiment types.
**Recommendation:** only pursue if asked - this project's identity is
EMDB/EMPIAR-specific by design.

## 3. Read-only EMDB/EMPIAR lookups (re-add the `emdb` package) — DONE

**Status: implemented** as `emdb_lookup.py` (and `emdb` re-added to
requirements). Two subcommands, both read-only/anonymous against the public
EMDB API, network only at call time:
- `lookup --accession EMD-XXXX` → compact JSON summary (title, authors,
  sample, method, resolution, map format/dimensions/pixel spacing/contour,
  related PDB/EMDB ids, and linked EMPIAR ids via the entry's annotations).
- `exists --accession EMD-XXXX` → yes/no, robust to the installed `emdb`
  0.1.12 gotcha where a genuine 404 surfaces as `EMDBAPIError` (not
  `EMDBNotFoundError`); a real network/server error is surfaced, not
  misreported as "absent".

Verified live against EMD-8000 (real metadata) and EMD-99999999 (correctly
"not found"). Note: `emdb` is EMDB-only — there is no standalone EMPIAR
lookup; EMPIAR appears only as a cross-reference on an EMDB entry.

Pairs with item 1: use `exists` to confirm a `related_emdb` accession before
citing it.

## 4. Local sanity-check map file contents before registering — DONE

**Status: implemented.** `em_deposit.py` now sniffs each map-like file's
fixed 1024-byte MRC2014/CCP4 header (stdlib `struct` only, no full-file
parse, no new dependency) during `prepare`/`preview` and before any session
is opened. It rejects a file smaller than the header, a file missing the
`MAP ` stamp at byte 208, or one whose header reports non-positive
dimensions — catching a wrong extension, a truncated download, or a
mixed-up path locally. `preview` shows the sniffed header (dimensions, data
mode, stamp) for each map file. See `_sniff_mrc()` in
[`em_deposit.py`](.claude/skills/emdb-deposition/scripts/em_deposit.py).

**Not done (still the header only):** the sniff does not read the density
body, so it can't detect a valid-header-but-corrupt-data file — that still
surfaces at upload / wwPDB server-side validation.

## 5. Richer manifest-level validation — DONE

**Status: fully implemented** (JSON Schema + ORCID/email format checks). `manifest.json` is now validated
against a Draft-7 schema
([`scripts/manifest.schema.json`](.claude/skills/emdb-deposition/scripts/manifest.schema.json))
via `jsonschema` — the same mechanism the EMPIAR side already used for
JSON_INPUT — replacing the ad-hoc `require_fields()`/isinstance checks that
were scattered through `em_deposit.py`. The schema doubles as precise,
readable documentation of the manifest format. It enforces required fields,
types, non-empty `users`/`files`, and `coordinates` being a real boolean;
enum resolution, voxel finiteness, and the MRC sniff remain as semantic
checks the schema can't express.

**Now also done:**
- ORCID iD *format* + ISO 7064 MOD 11-2 checksum validation
  (`common.orcid_problem`), tolerating an `https://orcid.org/` prefix.
- Email *format* sanity check (`common.email_problem`), deliberately
  permissive.

Only thing consciously left out: matching an ORCID against the live ORCID
registry (a network call, out of scope for these local pre-checks).

## Considered and rejected: `status --wait`

A polling `--wait` flag for `em_deposit.py status` (block until the
deposition reaches a terminal state) seemed like an obvious UX win, so it
was investigated properly before writing any code - checked
`onedep_lib.apis.deposit.enums.Status`'s actual values first:
`DEP, PROC, AUTH, REPL, AUCO, AUXS, AUXU, HOLD, HPUB, OBS, POLC, REL,
REUP, WAIT, WDRN` - these are standard wwPDB biocuration workflow states,
not a short automated pipeline. `REPL`/`AUCO` specifically mean a human
curator is waiting on *the depositor* to respond (not something a script
can wait through), and the real terminal state, `REL` (released), can
take weeks to months depending on curator review and often the
associated paper's own publication timeline. A polling wait-loop is the
wrong design for this process - the existing one-shot `status` check
(run it, get an answer, check back later) is actually correct, not
missing a feature. Documented here so this doesn't get proposed again
without this context.
