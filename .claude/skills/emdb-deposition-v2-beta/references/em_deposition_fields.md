# EM deposition: what the API covers vs. what it doesn't

## Scope of `em_deposit.py` / `onedep_lib`

`onedep_lib`'s `Deposition` object only exposes: experiment type, EM subtype,
file registration, per-map voxel spacing + contour level, required-file
checking, and submit/status. That's it. It does **not** cover the five
detailed experimental sections OneDep's web wizard normally collects:

1. Specimen Preparation (vitrification, staining, grid, embedding, shadowing)
2. Microscopy (microscope model, electron source, holder, energy filters,
   magnification, defocus)
3. Image Recording (detector, frame count, collection rate)
4. Reconstruction (particle selection, classification, alignment, angular
   assignment)
5. Fitting/Interpretation (only relevant if coordinates are included)

After `em_deposit.py submit` succeeds, the depositor still needs to log
into the OneDep web UI to fill in those sections before the entry can be
validated and released — `submit` and `status` both print a `site_url`
field with the exact link. This tool automates deposition creation and
core file upload only.

## Manifest `file_type` values

Only the ones relevant to EM depositions (see `onedep_lib.FileType` for the
full list, which also covers X-ray/NMR/etc.):

| Manifest `file_type`        | Meaning                              | Required? |
|------------------------------|---------------------------------------|-----------|
| `EM_MAP`                     | Primary map (vo-map)                  | Always, exactly 1 |
| `EM_HALF_MAP`                | Half map                              | Exactly 2, for SPA/HELICAL/SUBTOMOGRAM (not TOMOGRAPHY) |
| `ENTRY_IMAGE`                | Map preview image (500x500)           | Always, exactly 1 |
| `MMCIF_COORD`                | Fitted atomic model                   | Optional — set `"coordinates": true` in the manifest when included |
| `FSC_XML`                    | FSC curve                             | Encouraged, not enforced by the local schema check |
| `EM_ADDITIONAL_MAP`          | Extra maps (varying b-factor, etc.)   | Optional |

`voxel` (spacing_x/y/z + contour) is only meaningful on map-like files
(`EM_MAP`, `EM_HALF_MAP`, `EM_ADDITIONAL_MAP`) — `em_deposit.py` only calls
`set_voxel_values()` for those types, and `prepare` only *requires* a
complete voxel block (all four sub-fields present) on entries of those
types; a `voxel` block on e.g. an `ENTRY_IMAGE` entry is never read, so an
incomplete one there won't block `prepare`. Values are coerced to `float`
before being sent to `onedep_lib` — `1` and `1.0` in the manifest both work
the same. Non-numeric values, and non-finite ones (`NaN`/`Infinity` —
Python's `json` module accepts these as a non-standard extension, so
they're a real possibility, not just a theoretical one) are rejected with
a clear error rather than silently passed through.

## `em_subtype` values

`SPA` (single particle), `HELICAL`, `SUBTOMOGRAM`, `TOMOGRAPHY`. Only
SPA/HELICAL/SUBTOMOGRAM require exactly 2 half-maps per the bundled
`required_files.json` schema — TOMOGRAPHY does not. Accepts spaces or
hyphens in place of underscores (`"single particle"`, `"single-particle"`,
`"SPA"` all resolve the same way), matching `country`'s matching below.

## `country`

Accepts either a `Country` enum name (`UK`, `USA`, `USA_ISLANDS`, ...) or
the exact wwPDB display string (`"United Kingdom"`, `"United States"`).
See `onedep_lib.Country` for the full list — it's every ISO country, so
just ask the depositor for their country and match loosely.

## Local manifest validation

Before any onedep_lib session is opened, `prepare` (and `preview`) validate
the manifest locally so a mistake fails cleanly instead of leaving an
orphaned session behind:

- **Structure** is checked against a JSON Schema
  (`scripts/manifest.schema.json`, Draft-7, enforced with `jsonschema` — the
  same mechanism the EMPIAR side uses for JSON_INPUT): required fields,
  types, non-empty `users`/`files` arrays, and `coordinates` being a real
  JSON boolean (so the string `"false"` is rejected, not coerced to `True`).
- **Enums** (`country`, `em_subtype`, per-file `file_type`) are resolved
  case-insensitively to the onedep_lib enums; an unknown value fails here.
- **Voxel/contour** values are checked to be real, finite numbers (booleans,
  `NaN`/`Infinity`, and non-numeric values are rejected) for map-like files.
- **Map files** get a lightweight MRC2014/CCP4 header sniff (the fixed
  1024-byte header only — the `MAP ` stamp at byte 208 and positive
  dimensions), catching a truncated download, a wrong extension, or a
  mixed-up path before upload. `preview` shows the sniffed header
  (dimensions, data mode, stamp) for each map file.

## What we deliberately did *not* validate locally

The MRC sniff reads only the header, not the full map, so it does **not**
verify the density data itself, and it does **not** validate ORCID iD
*format* (any non-empty string is accepted). A corrupt map body, or a
syntactically-wrong ORCID, will surface later — either from `deposit()`'s
upload/processing step or from wwPDB's own server-side validation — not from
this script.
