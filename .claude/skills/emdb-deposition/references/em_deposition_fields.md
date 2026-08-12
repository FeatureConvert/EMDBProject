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
into the OneDep web UI (the `site_url` returned isn't currently surfaced by
this script but is available on the `Deposition` object — worth wiring in
if this becomes a real requirement) to fill in those sections before the
entry can be validated and released. This tool automates deposition
creation and core file upload only.

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
`set_voxel_values()` for those types.

## `em_subtype` values

`SPA` (single particle), `HELICAL`, `SUBTOMOGRAM`, `TOMOGRAPHY`. Only
SPA/HELICAL/SUBTOMOGRAM require exactly 2 half-maps per the bundled
`required_files.json` schema — TOMOGRAPHY does not.

## `country`

Accepts either a `Country` enum name (`UK`, `USA`, `USA_ISLANDS`, ...) or
the exact wwPDB display string (`"United Kingdom"`, `"United States"`).
See `onedep_lib.Country` for the full list — it's every ISO country, so
just ask the depositor for their country and match loosely.

## What we deliberately did *not* validate locally

`check_required_files()` only checks the required-file-type schema (which
types and how many of each). It does **not** validate map file contents,
voxel/contour numeric sanity, or ORCID ID format. Bad numbers or corrupt
files will surface later, either from `deposit()`'s upload/processing step
or from wwPDB's own server-side validation — not from this script.
