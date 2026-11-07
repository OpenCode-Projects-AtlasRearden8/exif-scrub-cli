# STATUS.md — exif-scrub

Date: 2026-11-07
Owner: Asdale Dynamics, LLC

## What was built

`exif-scrub` is a before-you-share photo metadata cleaner. Point it at a photo
(or a folder of photos) and it:

- strips **location** tags (all GPSInfo entries: latitude/longitude/refs,
  timestamps, altitude, etc.),
- strips **device** tags (Make, Model, Software, camera owner name, body
  serial, lens make/model/serial, image unique id) and never carries maker
  notes into the copy,
- strips **capture** tags (DateTime, DateTimeOriginal/Digitized, Offset times,
  ExposureTime, FNumber, ISO, flash, metering, focal length, ...),
- strips **note** tags (ImageDescription, UserComment, XP comment/keywords),
- writes **cleaned copies** to a fresh output folder (default
  `cleaned_<input>`; pre-existing folders get `_1`, `_2`, ... suffixes), never
  touching the originals,
- prints and saves a **before/after tag report** (`scrub_report.txt`, optional
  `--json` for `scrub_report.json`) listing every removed and kept tag with
  per-category counts,
- supports an optional **keep-list** (`--keep Orientation,DateTimeOriginal,0x010E`;
  names or hex ids; `--keep-none` strips everything). Default keep-list is
  `Orientation, ColorSpace` so photos don't come out sideways or with broken
  color, and ICC color profiles survive recompression,
- supports `--dry-run` (report preview, zero files written), `-q/--quality`
  for JPEG/WebP recompression quality (default 95), and batch mode
  (recursive directory input, collision-safe output names).

Formats: JPEG, PNG, TIFF, WebP (PNG/TIFF are re-saved losslessly; JPEG is
recompressed at the chosen quality).

**Fully offline**: the only dependency is Pillow; no network calls anywhere.

Distinct from photo-dedupe-lite-cli (duplicate finding) and
safe-share-redactor-cli (document redaction).

## How to run it

```bash
# single file
python exif_scrub.py IMG_1234.jpg

# whole folder, custom output, JSON report
python exif_scrub.py ~/Pictures/vacation -o ~/Desktop/to-post --json

# keep extra tags
python exif_scrub.py . --keep DateTimeOriginal,0x010E

# strip everything / preview only
python exif_scrub.py . --keep-none
python exif_scrub.py IMG_1234.jpg --dry-run
```

Requirements: Python 3.10+ and `pip install Pillow`.

## Files

- `exif_scrub.py` — the entire CLI (stdlib + Pillow only)
- `tests/test_exif_scrub.py` — pytest suite: tag stripping, keep-list,
  report text/JSON output, originals-untouched, dry-run, defaults,
  failure paths
- `README.md`, `LICENSE` (MIT), `.gitignore`, `STATUS.md`

## Verification results

- `python -m pytest tests -q` → **17 passed** (0 warnings, 0 failures)
- End-to-end CLI run on two JPEGs with GPS (39°46'N, 86°9'W), device make,
  and capture timestamps:
  - report showed 12 tags before → 0 after; all location/device/capture tags
    removed and listed;
  - cleaned copies re-opened: base/Exif/GPS IFDs all empty;
  - input files byte-identical after the run (verified by md5 check earlier
    in development and by `test_original_never_modified`);
  - no new files appeared next to the inputs; only the fresh
    `cleaned_receipts/` folder got new files;
  - `--dry-run` produced a full report and wrote nothing;
  - `-o`, default fresh-folder suffixing (`cleaned_shot_1`), and `--keep` /
    `--keep-none` all exercised by tests and manual runs.

## Known gaps

- **JPEG/PNG/TIFF/WebP only.** Other formats (HEIC, RAW, ...) are rejected
  with a clear error; HEIC support would need an extra library (and would
  break the "only Pillow" promise).
- **Maker notes are always stripped** (never copied). That's the safe
  direction, but the report shows them only if Pillow could parse them.
- **XMP/IPTC side data** inside some files: Pillow does not carry arbitrary
  XMP/IPTC blocks through a re-save, so they are dropped as a side effect.
  Not exhaustively audited — treat exotic containers with care.
- **JPEG copies are re-compressed** (quality 95 default). Pixel content is
  preserved but not bit-identical; PNG/TIFF/WebP re-saves are lossless for
  pixels.
- **Keep-list matching is by tag id across IFDs**, not per-IFD. Practical and
  simple, but a per-IFD override (e.g. keep `Exif.DateTimeOriginal` only)
  isn't supported yet.
- No packaged console entry point yet (run as `python exif_scrub.py`); a
  `pyproject.toml` could add an `exif-scrub` command later.

## License / credit

MIT — Copyright (c) 2026 Asdale Dynamics, LLC. See `LICENSE` and `README.md`.
