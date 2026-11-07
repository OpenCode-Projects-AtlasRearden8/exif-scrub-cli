#!/usr/bin/env python3
"""exif-scrub: strip location, device, and capture metadata from photos before sharing.

Runs fully offline using Pillow. Originals are never modified; cleaned copies
are written to a fresh output folder and a before/after tag report is printed
and saved next to them.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from PIL.ExifTags import GPSTAGS, IFD, TAGS

try:
    from PIL.TiffImagePlugin import IFDRational
except ImportError:  # pragma: no cover
    IFDRational = int

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

BASE_IFD = "base"
EXIF_IFD = "exif"
GPS_IFD = "gps"
MAKERNOTE_IFD = "makernote"

IFD_POINTER_IDS = {0x8769, 0x8825, 0x927C}

DEFAULT_KEEP_SPECS = ["Orientation", "ColorSpace"]

DEVICE_TAG_IDS = {
    0x010F,  # Make
    0x0110,  # Model
    0x0131,  # Software
    0xA430,  # CameraOwnerName
    0xA431,  # BodySerialNumber
    0xA433,  # LensMake
    0xA434,  # LensModel
    0xA435,  # LensSerialNumber
    0xA420,  # ImageUniqueID
}

CAPTURE_TAG_IDS = {
    0x0132,  # DateTime
    0x829A,  # ExposureTime
    0x829D,  # FNumber
    0x8822,  # ExposureProgram
    0x8827,  # ISOSpeedRatings
    0x8830,  # SensitivityType
    0x9003,  # DateTimeOriginal
    0x9004,  # DateTimeDigitized
    0x9010,  # OffsetTime
    0x9011,  # OffsetTimeOriginal
    0x9012,  # OffsetTimeDigitized
    0x9201,  # ShutterSpeedValue
    0x9202,  # ApertureValue
    0x9203,  # BrightnessValue
    0x9204,  # ExposureBiasValue
    0x9205,  # MaxApertureValue
    0x9206,  # SubjectDistance
    0x9207,  # MeteringMode
    0x9208,  # LightSource
    0x9209,  # Flash
    0x920A,  # FocalLength
    0x9214,  # SubjectArea
}

LOCAL_NOTE_TAG_IDS = {
    0x010E,  # ImageDescription
    0x9286,  # UserComment
    0x9C9B,  # XPComment
    0x9C9C,  # XPKeywords
    0x9C9E,  # XPSubject
    0x9C9F,  # XPAuthor
}

CATEGORIES = ("location", "device", "capture", "notes", "other")


@dataclass
class TagEntry:
    ifd: str
    tag_id: int
    value: object

    @property
    def name(self) -> str:
        table = GPSTAGS if self.ifd == GPS_IFD else TAGS
        return table.get(self.tag_id, f"0x{self.tag_id:04X}")

    @property
    def value_text(self) -> str:
        text = str(self.value)
        if text.startswith("b'") or text.startswith('b"'):
            text = text[2:-1]
        text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
        return text.strip()[:80]

    @property
    def category(self) -> str:
        if self.ifd == GPS_IFD:
            return "location"
        if self.tag_id in CAPTURE_TAG_IDS:
            return "capture"
        if self.tag_id in LOCAL_NOTE_TAG_IDS:
            return "notes"
        if self.tag_id in DEVICE_TAG_IDS:
            return "device"
        return "other"


@dataclass
class FileReport:
    source: Path
    output: Path
    before: list[TagEntry] = field(default_factory=list)
    after: list[TagEntry] = field(default_factory=list)

    @property
    def removed(self) -> list[TagEntry]:
        kept_keys = {(e.ifd, e.tag_id, e.value_text) for e in self.after}
        return [e for e in self.before if (e.ifd, e.tag_id, e.value_text) not in kept_keys]

    def counts(self, tags: list[TagEntry]) -> dict[str, int]:
        counts = {c: 0 for c in CATEGORIES}
        for tag in tags:
            counts[tag.category] += 1
        return counts


def safe_value(value: object) -> object:
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", "replace").rstrip("\x00")
        return decoded or value
    if isinstance(value, dict):
        return {safe_value(k): safe_value(v) for k, v in dict(value).items()}
    return value


def collect_tags(exif: Image.Exif) -> list[TagEntry]:
    sections = [
        (BASE_IFD, None),
        (EXIF_IFD, IFD.Exif),
        (GPS_IFD, IFD.GPSInfo),
        (MAKERNOTE_IFD, IFD.MakerNote),
    ]
    entries: list[TagEntry] = []
    for ifd_name, ifd_id in sections:
        try:
            mapping = exif if ifd_id is None else exif.get_ifd(ifd_id)
            items = list(dict(mapping).items())
        except Exception:
            continue
        for tag_id, value in items:
            if ifd_id is None and tag_id in IFD_POINTER_IDS:
                continue
            entries.append(TagEntry(ifd_name, tag_id, safe_value(value)))
    return entries


def build_keep_ids(specs: list[str], keep_none: bool) -> set[int]:
    keep_ids: set[int] = set()
    if not keep_none:
        for spec in DEFAULT_KEEP_SPECS:
            keep_ids.add(find_tag_id(spec))
    for spec in specs:
        for piece in spec.split(","):
            stripped = piece.strip()
            if not stripped:
                continue
            if stripped.lstrip("-").isdigit() or stripped.upper().startswith("0X"):
                keep_ids.add(int(stripped, 0))
            else:
                keep_ids.add(find_tag_id(stripped))
    return keep_ids


def find_tag_id(name: str) -> int:
    lowered = name.lower()
    for table in (TAGS, GPSTAGS):
        for tag_id, tag_name in table.items():
            if tag_name.lower() == lowered:
                return tag_id
    raise ValueError(
        f"unknown tag name: {name!r} (use an EXIF/GPS tag name, or a hex id such as 0x0112)"
    )


def make_clean_exif(source_exif: Image.Exif, keep_ids: set[int]) -> Image.Exif:
    clean = Image.Exif()
    picked: dict[str, dict[int, object]] = {
        BASE_IFD: {},
        EXIF_IFD: {},
        GPS_IFD: {},
    }
    for tag in collect_tags(source_exif):
        if tag.ifd == MAKERNOTE_IFD:
            continue
        if tag.tag_id in keep_ids:
            picked[tag.ifd][tag.tag_id] = tag.value
    for tag_id, value in picked[BASE_IFD].items():
        clean[tag_id] = value
    if picked[EXIF_IFD]:
        clean[IFD.Exif] = dict(picked[EXIF_IFD])
    if picked[GPS_IFD]:
        clean[IFD.GPSInfo] = dict(picked[GPS_IFD])
    return clean


def find_images(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p
        for p in target.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def unique_output_dir(proposed: Path) -> Path:
    if not proposed.exists():
        return proposed
    counter = 1
    while True:
        candidate = proposed.parent / f"{proposed.name}_{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def unique_filename(name: str, taken: set[str]) -> str:
    lowered = name.lower()
    if lowered not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    counter = 1
    while True:
        candidate = f"{stem}_{counter}.{ext}" if dot else f"{name}_{counter}"
        if candidate.lower() not in taken:
            return candidate
        counter += 1


def scrub_file(src: Path, dest: Path, keep_ids: set[int], quality: int) -> FileReport:
    with Image.open(src) as img:
        before = collect_tags(img.getexif())
        clean_exif = make_clean_exif(img.getexif(), keep_ids)

        dest.parent.mkdir(parents=True, exist_ok=True)
        save_format = img.format if img.format in ("JPEG", "PNG", "TIFF", "WEBP") else "JPEG"

        save_kwargs: dict[str, object] = {"exif": clean_exif.tobytes()}
        icc = img.info.get("icc_profile")
        if icc:
            save_kwargs["icc_profile"] = icc
        if save_format in ("JPEG", "WEBP"):
            save_kwargs["quality"] = quality
        if save_format == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(dest, format=save_format, **save_kwargs)

    with Image.open(dest) as out_img:
        after = collect_tags(out_img.getexif())
    return FileReport(source=src, output=dest, before=before, after=after)


def output_name_for(src: Path) -> str:
    suffix = src.suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    return src.stem + suffix


def render_report(reports: list[FileReport]) -> str:
    lines: list[str] = ["EXIF SCRUB REPORT", "=" * 24, ""]
    removed_by_cat = {c: 0 for c in CATEGORIES}
    total_before = total_after = 0
    for report in reports:
        before_counts = report.counts(report.before)
        after_counts = report.counts(report.after)
        before_total = sum(before_counts.values())
        after_total = sum(after_counts.values())
        total_before += before_total
        total_after += after_total
        lines.append(f"File: {report.source.name}")
        lines.append(
            "  Before: %d tags (location %d, device %d, capture %d, notes %d, other %d)"
            % (
                before_total,
                before_counts["location"],
                before_counts["device"],
                before_counts["capture"],
                before_counts["notes"],
                before_counts["other"],
            )
        )
        lines.append(
            "  After:  %d tags (location %d, device %d, capture %d, notes %d, other %d)"
            % (
                after_total,
                after_counts["location"],
                after_counts["device"],
                after_counts["capture"],
                after_counts["notes"],
                after_counts["other"],
            )
        )
        lines.append(f"  Removed ({len(report.removed)}):")
        for tag in report.removed:
            removed_by_cat[tag.category] += 1
            lines.append(f"    - {tag.name}: {tag.value_text} [{tag.category}]")
        lines.append(f"  Kept on copy ({len(report.after)}):")
        for tag in report.after:
            lines.append(f"    + {tag.name}: {tag.value_text} [{tag.category}]")
        lines.append(f"  Cleaned copy: {report.output}")
        lines.append("")
    lines.append("TOTALS")
    lines.append("-" * 6)
    lines.append(f"  Files processed: {len(reports)}")
    lines.append(f"  Tags before:     {total_before}")
    lines.append(f"  Tags after:      {total_after}")
    for cat in CATEGORIES:
        lines.append(f"    {cat}: removed {removed_by_cat[cat]}")
    lines.append("")
    return "\n".join(lines)


def render_report_json(reports: list[FileReport]) -> str:
    payload = {
        "files": [
            {
                "source": str(report.source),
                "output": str(report.output),
                "before": [
                    {"ifd": t.ifd, "tag": t.name, "category": t.category, "value": t.value_text}
                    for t in report.before
                ],
                "after": [
                    {"ifd": t.ifd, "tag": t.name, "category": t.category, "value": t.value_text}
                    for t in report.after
                ],
                "removed": [
                    {"tag": t.name, "category": t.category, "value": t.value_text}
                    for t in report.removed
                ],
            }
            for report in reports
        ],
        "totals": {
            "files": len(reports),
            "tags_before": sum(len(r.before) for r in reports),
            "tags_after": sum(len(r.after) for r in reports),
        },
    }
    return json.dumps(payload, indent=2)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="exif-scrub",
        description="Strip location, device, and capture metadata from photos before "
        "sharing. Fully offline. Originals are never modified; cleaned copies are "
        "written to a fresh folder and a before/after tag report is produced.",
    )
    parser.add_argument("input", type=Path, help="Photo file or directory of photos to clean")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output folder for cleaned copies (default: cleaned_<input name> next to it)",
    )
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="SPEC",
        help="Comma-separated tag names or hex ids to keep in the copy "
        "(e.g. 'Orientation,ColorSpace' or '0x0112'). Defaults to Orientation,ColorSpace.",
    )
    parser.add_argument(
        "--keep-none",
        action="store_true",
        help="Strip every tag, ignoring the default keep-list",
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=95, help="JPEG/WebP save quality (default 95)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show the report without writing any files"
    )
    parser.add_argument(
        "--json", action="store_true", help="Also write scrub_report.json in the output folder"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target: Path = args.input
    if not target.exists():
        print(f"error: input not found: {target}", file=sys.stderr)
        return 2

    try:
        keep_ids = build_keep_ids(args.keep, args.keep_none)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sources = find_images(target)
    if not sources:
        print(
            "error: no supported image files found (jpg, jpeg, png, tif, tiff, webp)",
            file=sys.stderr,
        )
        return 2

    base_name = target.name if target.is_dir() else (target.stem or "photos")
    proposed_out = (
        args.output if args.output is not None else target.parent / f"cleaned_{base_name}"
    )

    temp_dir: tempfile.TemporaryDirectory | None = None
    if args.dry_run:
        temp_dir = tempfile.TemporaryDirectory(prefix="exif-scrub-preview-")
        out_dir = Path(temp_dir.name)
    else:
        out_dir = unique_output_dir(proposed_out)
        out_dir.mkdir(parents=True, exist_ok=True)

    try:
        reports: list[FileReport] = []
        taken: set[str] = set()
        for src in sources:
            name = unique_filename(output_name_for(src), taken)
            taken.add(name.lower())
            report = scrub_file(src, out_dir / name, keep_ids, args.quality)
            reports.append(report)

        text = render_report(reports)
        print(text)
        if args.dry_run:
            print(f"Dry run: no files were written (report preview for {len(reports)} file(s))")
        else:
            report_path = out_dir / "scrub_report.txt"
            report_path.write_text(text, encoding="utf-8")
            if args.json:
                (out_dir / "scrub_report.json").write_text(
                    render_report_json(reports), encoding="utf-8"
                )
            print(f"Cleaned copies and report saved in: {out_dir}")
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
