# exif-scrub

Before-you-share photo cleaner from [Asdale Dynamics, LLC](https://dylanasdale.github.io).

Photos carry invisible baggage: where you were (GPS), what you shot it with
(Make, Model, Lens, Software), and when you shot it (DateTimeOriginal and
friends). Post it somewhere public and you post all of that too.

`exif-scrub` strips location, device, and capture metadata, shows you a plain
before/after tag report, and writes *cleaned copies* to a fresh folder — your
originals are never touched. Runs **fully offline**: no network, no accounts,
no uploads.

## What it does

- Strips GPS/location tags (latitude, longitude, altitude, GPS timestamps)
- Strips device tags (camera make/model, lens, serial numbers, maker notes)
- Strips capture tags (dates/times, exposure, ISO, flash, metering)
- Strips note-like tags (ImageDescription, UserComment, XP fields)
- Skips embedded thumbnails and maker-note blocks entirely (clean copies
  start from a fresh, minimal EXIF payload)
- Keeps working images: `Orientation` and `ColorSpace` are preserved by
  default so photos don't come out sideways or washed out
- Writes a text report (`scrub_report.txt`) listing every tag removed and kept,
  with counts by category; add `--json` for a machine-readable copy
- Batch mode: point it at a folder and it walks it recursively
- `--dry-run` shows the report without writing anything

This tool handles *metadata*, not duplicates or documents. For duplicate
hunting see photo-dedupe-lite-cli; for document redaction see
safe-share-redactor-cli.

## Requirements

- Python 3.10+ (written and tested on 3.13)
- Pillow (recent versions work; version 13 tested)

## Install

```bash
pip install Pillow
```

Or with the system package manager. Pillow is the only dependency.

## Usage

Single photo (writes `cleaned_<name>` folder next to the input, reports to
terminal and to `scrub_report.txt` inside the folder):

```bash
python exif_scrub.py IMG_1234.jpg
```

Whole folder, custom output location, JSON report too:

```bash
python exif_scrub.py ~/Pictures/vacation -o ~/Desktop/to-post --json
```

Keep extra useful tags, e.g. caption date and the capture date (names or hex):

```bash
python exif_scrub.py . --keep DateTimeOriginal,0x010E
```

Strip every single tag, even orientation and colorspace:

```bash
python exif_scrub.py . --keep-none
```

Preview without writing anything:

```bash
python exif_scrub.py IMG_1234.jpg --dry-run
```

### Options

| Option | Meaning |
| --- | --- |
| `input` | A photo file, or a folder to process recursively |
| `-o, --output DIR` | Output folder for cleaned copies (default: `cleaned_<input>` beside the input) |
| `--keep SPEC` | Extra tags to keep in the copy; comma-separated names or hex ids, repeatable. Default keep-list: `Orientation`, `ColorSpace` |
| `--keep-none` | Ignore the default keep-list and strip every tag |
| `-q, --quality N` | JPEG/WebP save quality (default 95) |
| `--dry-run` | Show the report, write nothing |
| `--json` | Also write `scrub_report.json` in the output folder |

JPEG, PNG, TIFF, and WebP are supported. Each run creates a **fresh** output
folder: if `cleaned_vacation` already exists with files, the next run writes
to `cleaned_vacation_1`, then `_2`, and so on. Cleaned copies always get
distinct names (`shot_1.jpg`) instead of overwriting anything.

## What the report looks like

```
File: IMG_0001.jpg
  Before: 13 tags (location 4, device 2, capture 4, notes 1, other 2)
  After:  2 tags (location 0, device 0, capture 0, notes 0, other 2)
  Removed (11):
    - Make: FruityPhone Ltd [device]
    - GPSLatitude: (40.0, 26.0, 0.4712) [location]
    - DateTimeOriginal: 2024:07:04 14:22:09 [capture]
    ...
  Kept on copy (2):
    + Orientation: 6 [other]
    + ColorSpace: 1 [other]
  Cleaned copy: /path/to/cleaned_foo/IMG_0001.jpg
```

## A note on quality

Re-saving a JPEG re-compresses it. The default quality of 95 is a good
practical middle ground, but the cleaned copy is not bit-for-bit identical to
the original. Pixels are otherwise untouched, and PNG/TIFF copies are lossless.

## Privacy

- 100% offline: the tool never opens a socket.
- No telemetry, no accounts.
- This tool strips metadata; it does not remove watermarks, visible text, or
  content from photos. It also cannot guarantee removal of opaque file-format
  quirks in formats outside JPEG/PNG/TIFF/WebP.

## Run the tests

```bash
python -m pytest tests -v
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Asdale Dynamics, LLC.

Part of the beginner-friendly tool collection at
[dylanasdale.github.io](https://dylanasdale.github.io).
