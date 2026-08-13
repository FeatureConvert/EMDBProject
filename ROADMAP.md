# Roadmap / expansion ideas

Researched while working autonomously (the user was away) — these are real
options, not vague brainstorming, but they involve scope/design decisions
that are the user's to make, not something to build unsupervised. Nothing
below has been implemented. See [`list_depositions.py`](.claude/skills/emdb-deposition/scripts/list_depositions.py)
for one small, unambiguous addition that *was* built this pass, and the
"considered and rejected" note at the end for one that deliberately wasn't.

## 1. Composite map deposition support

**What it is:** EMDB has a community-recommended workflow (flagged in this
project's very first research pass, before any code existed) for cryo-EM
structures assembled from multiple focused refinements of different
regions plus an unfocused consensus map. Each piece — the composite map,
the consensus map, and each focused refinement — must be deposited as its
**own separate EMDB entry**, cross-referenced through a "Related entries"
mechanism.

**Why it's not built:** This project currently assumes one manifest = one
map = one entry. Composite support means:
- A new manifest concept for a *group* of related depositions (composite +
  consensus + N focused refinements), not just a single map.
- Cross-referencing logic — does `onedep_lib` expose a "related entries"
  API? Not yet verified against the installed package; this needs its own
  Phase-1-style spike before any code gets written, the same way this
  project's original `onedep_lib` auth/session behavior needed verifying
  against source rather than assumed from docs.
- A real question for the user: do they actually do composite-map
  refinements, or is this solving a problem they don't have? Ask before
  building.

**Effort:** Medium-large. **Value:** High if the user's actual workflow
uses composite maps; zero otherwise. **Recommendation:** ask first.

## 2. Other experiment types (X-ray, NMR, etc.)

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

## 3. Read-only EMDB/EMPIAR lookups (re-add the `emdb` package)

**What it is:** Earlier this project depended on `emdb` (a read-only EMDB
REST API client) but it was removed in a review pass for being genuinely
unused - confirmed via grep, zero imports anywhere. A legitimate future
use: checking whether a related EMD-/EMPIAR- accession actually exists
before referencing it (relevant for composite-map cross-referencing,
item 1 above, or for a depositor double-checking a citation), or pulling
metadata from an existing entry to pre-fill a new deposition's manifest
fields.

**Why it's not built:** No current workflow in this project needs it -
adding it back without a concrete use ready to consume it would just be
restoring dead weight, the exact thing that got it removed.

**Effort:** Small (the package already exists and was proven to work).
**Value:** Real but currently speculative - becomes valuable specifically
alongside item 1 (composite maps) or a "look up related entry" feature no
one has asked for yet. **Recommendation:** revisit only if item 1 happens,
or if the user has a concrete lookup need.

## 4. Local sanity-check map file contents before registering

**What it is:** `em_deposit.py prepare` currently checks that a file
*exists* at the given path (via `onedep_lib`'s own `add_file()`), but
never checks it's actually a valid MRC/CCP4 map file - a wrong file
extension, a truncated download, or a mixed-up path would only surface
much later, either at `deposit()`/upload time or at wwPDB's own
server-side validation.

**What a real fix would look like:** MRC files have a fixed, well-known
1024-byte header (dimensions, mode, cell size, and a `MAP `/`MAP\0` magic
string at byte offset 208 in the modern MRC2014 format) - a lightweight
header sniff (no full-file parse, no new heavy dependency) could catch
"this isn't actually an MRC file" or "the header claims 0 dimensions"
locally, matching this project's existing philosophy of catching what it
can before wwPDB does (see `references/em_deposition_fields.md`'s "what we
deliberately did *not* validate locally" section, which already flags
this exact gap).

**Effort:** Small-medium (the MRC2014 header format is simple and
well-documented; no new dependency needed, just `struct` from the
standard library). **Value:** Real, moderate - catches a genuine class of
mistake (wrong file, corrupted download) that currently isn't caught
until much later in the workflow. **Recommendation:** worth doing; lowest
risk/effort-to-value ratio of everything on this list. Good candidate for
a focused follow-up session.

## 5. Richer manifest-level validation

Smaller polish items noticed while building/testing this project, none
individually urgent:
- ORCID iD format validation (`0000-0002-XXXX-XXXX` with a checksum digit)
  before it reaches `onedep_lib` - currently any string is accepted and
  passed through as-is.
- Email format sanity check (currently unvalidated, same reasoning as
  ORCID above - would surface late otherwise).
- A JSON Schema file for `manifest.json` itself (this project has a
  emdb-empiar upstream JSON schema for EMPIAR's JSON_INPUT already
  bundled and used; the EMDB-side manifest format is currently documented
  only in prose in `references/em_deposition_fields.md` and enforced
  ad hoc via `require_fields()` calls scattered through `em_deposit.py`).
  A real schema would make validation more uniform and would double as
  precise, generated documentation.

**Effort:** Small each. **Value:** Moderate, mostly UX polish rather than
closing a real gap. **Recommendation:** low priority; fine to defer
indefinitely.

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
