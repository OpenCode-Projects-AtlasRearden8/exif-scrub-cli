import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from PIL.ExifTags import IFD
from PIL.TiffImagePlugin import IFDRational

from exif_scrub import (
    CAPTURE_TAG_IDS,
    DEVICE_TAG_IDS,
    FileReport,
    TagEntry,
    build_keep_ids,
    find_tag_id,
    render_report,
    render_report_json,
    scrub_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

RATIONAL_GPS = (IFDRational(40), IFDRational(26), IFDRational(0.4712))


def make_metadata_image(path: Path, size: tuple[int, int] = (40, 30)) -> Path:
    img = Image.new("RGB", size, (10, 120, 220))
    exif = Image.Exif()
    exif[0x010F] = "FruityPhone Ltd"
    exif[0x0110] = "FruityPhone 15 Pro"
    exif[0x0112] = 6
    exif[0x010E] = "summer barbecue"
    exif[0x8769] = {
        0x9003: "2024:07:04 14:22:09",
        0x829A: IFDRational(1, 250),
        0x829D: IFDRational(14, 5),
        0x8827: 3200,
        0xA001: 1,
    }
    exif[0x8825] = {
        0x0001: "N",
        0x0002: RATIONAL_GPS,
        0x0003: "W",
        0x0004: RATIONAL_GPS,
    }
    img.save(path, exif=exif.tobytes(), quality=92)
    return path


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    return make_metadata_image(tmp_path / "IMG_0001.jpg")


def read_tags(path: Path) -> tuple[dict[int, object], dict[int, object], dict[int, object]]:
    with Image.open(path) as img:
        exif = img.getexif()
        return (
            dict(exif.items()),
            dict(exif.get_ifd(IFD.Exif).items()),
            dict(exif.get_ifd(IFD.GPSInfo).items()),
        )


def test_defaults_strip_location_device_capture(photo: Path, tmp_path: Path):
    out = tmp_path / "out1" / "IMG_0001.jpg"
    scrub_file(photo, out, build_keep_ids([], False), 95)
    base, exif_ifd, gps_ifd = read_tags(out)

    assert gps_ifd == {}
    for tag_id in (0x010F, 0x0110):
        assert tag_id not in base
    for tag_id in (0x9003, 0x829A, 0x829D, 0x8827):
        assert tag_id not in exif_ifd
    assert 0x010E not in base
    assert base.get(0x0112) == 6
    assert exif_ifd.get(0xA001) == 1


def test_keep_none_strips_everything(photo: Path, tmp_path: Path):
    out = tmp_path / "out2" / "IMG_0001.jpg"
    report = scrub_file(photo, out, build_keep_ids([], True), 95)
    base, exif_ifd, gps_ifd = read_tags(out)
    assert base == {} and exif_ifd == {} and gps_ifd == {}
    assert report.after == []


def test_keep_list_adds_extra_tags(photo: Path, tmp_path: Path):
    keep = build_keep_ids(["DateTimeOriginal", "0x010E"], False)
    out = tmp_path / "out3" / "IMG_0001.jpg"
    report = scrub_file(photo, out, keep, 95)
    base, exif_ifd, _ = read_tags(out)
    assert base.get(0x010E) == "summer barbecue"
    assert exif_ifd.get(0x9003) == "2024:07:04 14:22:09"
    assert base.get(0x0112) == 6
    kept_names = {t.name for t in report.after}
    assert {"Orientation", "ColorSpace", "DateTimeOriginal", "ImageDescription"} <= kept_names
    removed_names = {t.name for t in report.removed}
    assert {"Make", "Model", "GPSLatitude", "ExposureTime"} <= removed_names


def test_unknown_keep_spec_is_rejected():
    with pytest.raises(ValueError):
        build_keep_ids(["NotARealTag"], False)


def test_hex_keep_spec_resolves(photo: Path, tmp_path: Path):
    keep = build_keep_ids(["0x0112"], True)
    assert keep == {0x0112}
    out = tmp_path / "out4" / "IMG_0001.jpg"
    report = scrub_file(photo, out, keep, 95)
    base, _, _ = read_tags(out)
    assert base.get(0x0112) == 6
    assert base.get(0xA001) is None


def test_find_tag_id_matches_names():
    assert find_tag_id("Orientation") == 0x0112
    assert find_tag_id("gPSlatituderef".lower()) == 0x0001
    with pytest.raises(ValueError):
        find_tag_id("bogus")


def test_image_pixels_survive(photo: Path, tmp_path: Path):
    out = tmp_path / "out5" / "IMG_0001.jpg"
    scrub_file(photo, out, build_keep_ids([], False), 100)
    with Image.open(photo) as orig, Image.open(out) as cleaned:
        assert cleaned.size == orig.size
        assert cleaned.tobytes() == orig.tobytes()


def test_category_assignment():
    entry = TagEntry("gps", 0x0002, RATIONAL_GPS)
    assert entry.category == "location" and entry.name == "GPSLatitude"
    assert TagEntry("base", 0x010F, "X").category == "device"
    assert TagEntry("exif", 0x9003, "T").category == "capture"
    assert TagEntry("exif", 0x9286, "hello").category == "notes"
    assert TagEntry("base", 0x0112, 1).category == "other"
    assert set(CAPTURE_TAG_IDS) & set(DEVICE_TAG_IDS) == set()


def test_png_without_exif_copies_tags_free(tmp_path: Path):
    src = tmp_path / "plain.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(src)
    out = tmp_path / "out6" / "plain.png"
    report = scrub_file(src, out, build_keep_ids([], False), 95)
    assert report.before == [] and report.after == []
    assert out.exists()
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.size == (8, 8)


def test_directory_input_and_report_file(tmp_path: Path):
    photos = tmp_path / "photos"
    photos.mkdir()
    make_metadata_image(photos / "a.jpg")
    make_metadata_image(photos / "b.jpg")

    proc = subprocess.run(
        [sys.executable, "exif_scrub.py", str(photos), "-o", str(tmp_path / "clean"), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    stdout = proc.stdout
    clean = tmp_path / "clean"
    assert (clean / "scrub_report.txt").exists()
    assert (clean / "scrub_report.json").exists()
    assert (clean / "a.jpg").exists() and (clean / "b.jpg").exists()
    assert "Tags after:      4" in stdout
    assert "location: removed 8" in stdout

    report = json.loads((clean / "scrub_report.json").read_text(encoding="utf-8"))
    assert report["totals"] == {"files": 2, "tags_before": 26, "tags_after": 4}
    a = next(f for f in report["files"] if f["source"].endswith("a.jpg"))
    removed_tags = {r["tag"] for r in a["removed"]}
    assert {"Make", "Model", "GPSLatitude", "GPSLongitude", "DateTimeOriginal"} <= removed_tags


def test_default_output_and_fresh_folder(tmp_path: Path):
    photos = tmp_path / "shots"
    photos.mkdir()
    make_metadata_image(photos / "shot.jpg")
    first = subprocess.run(
        [sys.executable, "exif_scrub.py", str(photos / "shot.jpg")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "cleaned_shot" in first.stdout
    assert "cleaned_shot_1" not in first.stdout
    assert (photos / "cleaned_shot" / "shot.jpg").exists()

    second = subprocess.run(
        [sys.executable, "exif_scrub.py", str(photos / "shot.jpg")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "cleaned_shot_1" in second.stdout
    assert (photos / "cleaned_shot_1" / "shot.jpg").exists()


def test_dry_run_writes_nothing(tmp_path: Path):
    photos = tmp_path / "perk"
    photos.mkdir()
    make_metadata_image(photos / "p.jpg")
    proc = subprocess.run(
        [sys.executable, "exif_scrub.py", str(photos), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    stdout = proc.stdout
    assert "Dry run: no files were written" in stdout
    assert list(photos.rglob("*")) == [photos / "p.jpg"]


def test_original_never_modified(tmp_path: Path):
    photo = make_metadata_image(tmp_path / "story.jpg")
    original_bytes = photo.read_bytes()
    subprocess.run(
        [sys.executable, "exif_scrub.py", str(photo), "-o", str(tmp_path / "c")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert photo.read_bytes() == original_bytes


def test_report_text_structure(tmp_path: Path):
    photo = make_metadata_image(tmp_path / "rep.jpg")
    out = tmp_path / "rep_out" / "rep.jpg"
    report = scrub_file(photo, out, build_keep_ids([], False), 95)
    text = render_report([report])
    assert text.startswith("EXIF SCRUB REPORT")
    assert "File: rep.jpg" in text
    assert "Before: 13 tags" in text
    assert "After:  2 tags" in text
    assert "- GPSLatitude: " in text
    assert "[location]" in text
    assert "- Make: FruityPhone Ltd [device]" in text
    assert "- DateTimeOriginal: 2024:07:04 14:22:09 [capture]" in text
    assert "- ImageDescription: summer barbecue [notes]" in text
    assert "+ Orientation: 6 [other]" in text
    assert "Tags before:     13" in text
    assert "Tags after:      2" in text
    assert "location: removed 4" in text


def test_report_text_skips_removed_for_kept_tags(tmp_path: Path):
    photo = make_metadata_image(tmp_path / "keepy.jpg")
    out = tmp_path / "keep_out" / "keepy.jpg"
    report = scrub_file(photo, out, build_keep_ids(["DateTimeOriginal"], False), 95)
    rendered = render_report([report])
    stripped_lines = [ln for ln in rendered.splitlines() if ln.startswith("    - ")]
    assert any("GPSLatitude" in ln for ln in stripped_lines)
    assert not any("DateTimeOriginal" in ln for ln in stripped_lines)
    kept_lines = [ln for ln in rendered.splitlines() if ln.startswith("    + ")]
    assert any("DateTimeOriginal: 2024:07:04 14:22:09" in ln for ln in kept_lines)


def test_render_report_json_shape():
    entry_before = TagEntry("gps", 0x0002, RATIONAL_GPS)
    entry_after = TagEntry("base", 0x0112, 6)
    report = FileReport(
        source=Path("/p/in.jpg"),
        output=Path("/p/out/in.jpg"),
        before=[entry_before],
        after=[entry_after],
    )
    payload = json.loads(render_report_json([report]))
    assert payload["totals"] == {"files": 1, "tags_before": 1, "tags_after": 1}
    assert payload["files"][0]["removed"][0]["tag"] == "GPSLatitude"
    assert payload["files"][0]["after"][0]["tag"] == "Orientation"


def test_failure_paths(tmp_path: Path):
    images = tmp_path / "only_txt"
    images.mkdir()
    (images / "notes.txt").write_text("nothing to see")
    proc = subprocess.run(
        [sys.executable, "exif_scrub.py", str(images)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "no supported image files found" in proc.stderr

    proc = subprocess.run(
        [sys.executable, "exif_scrub.py", str(tmp_path / "void")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "input not found" in proc.stderr

    proc = subprocess.run(
        [sys.executable, "exif_scrub.py", str(tmp_path), "--keep", "Nope"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "unknown tag name" in proc.stderr
