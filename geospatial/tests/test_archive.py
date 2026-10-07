import zipfile
from pathlib import Path

import pytest

from geospatial.exceptions import ArchiveLimitsExceededError, InvalidShapefileArchiveError
from geospatial.services.archive import extract_zip_safely, find_shapefile

LIMITS = {"max_files": 50, "max_total_bytes": 10 * 1024 * 1024}


def _zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def test_extracts_files_and_preserves_nested_folders(tmp_path):
    z = _zip(tmp_path, {"data/a.shp": b"1", "data/a.shx": b"2", "data/a.dbf": b"3"})
    files = extract_zip_safely(z, tmp_path / "out", **LIMITS)
    assert sorted(f.name for f in files) == ["a.dbf", "a.shp", "a.shx"]
    assert find_shapefile(files).name == "a.shp"


@pytest.mark.parametrize(
    "name", ["../evil.txt", "a/../../evil.txt", "/etc/evil.txt", "..\\evil.txt", "C:/evil.txt"]
)
def test_path_traversal_and_absolute_paths_are_rejected(tmp_path, name):
    z = _zip(tmp_path, {name: b"x"})
    with pytest.raises(InvalidShapefileArchiveError):
        extract_zip_safely(z, tmp_path / "out", **LIMITS)
    assert not (tmp_path / "evil.txt").exists()


def test_symlink_entries_are_rejected(tmp_path):
    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("link.shp")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(InvalidShapefileArchiveError, match="symbolic link"):
        extract_zip_safely(path, tmp_path / "out", **LIMITS)


def test_zip_bomb_is_rejected_by_uncompressed_size(tmp_path):
    z = _zip(tmp_path, {"big.shp": b"0" * 2_000_000})
    assert z.stat().st_size < 10_000  # tiny on disk, large when extracted
    with pytest.raises(ArchiveLimitsExceededError):
        extract_zip_safely(z, tmp_path / "out", max_files=10, max_total_bytes=1_000_000)


def test_too_many_files_is_rejected(tmp_path):
    z = _zip(tmp_path, {f"f{i}.txt": b"x" for i in range(5)})
    with pytest.raises(ArchiveLimitsExceededError):
        extract_zip_safely(z, tmp_path / "out", max_files=3, max_total_bytes=10_000)


def test_corrupt_zip_is_rejected(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"PK\x03\x04 this is not really a zip")
    with pytest.raises(InvalidShapefileArchiveError):
        extract_zip_safely(path, tmp_path / "out", **LIMITS)


def test_macos_junk_is_ignored(tmp_path):
    z = _zip(
        tmp_path, {"__MACOSX/._a.shp": b"j", "._a.dbf": b"j", "a.shp": b"1", "a.shx": b"2", "a.dbf": b"3"}
    )
    files = extract_zip_safely(z, tmp_path / "out", **LIMITS)
    assert sorted(f.name for f in files) == ["a.dbf", "a.shp", "a.shx"]


def test_archive_without_shp_is_rejected(tmp_path):
    files = extract_zip_safely(_zip(tmp_path, {"readme.txt": b"hi"}), tmp_path / "out", **LIMITS)
    with pytest.raises(InvalidShapefileArchiveError, match=r"\.shp"):
        find_shapefile(files)


def test_missing_required_components_are_named(tmp_path):
    files = extract_zip_safely(_zip(tmp_path, {"a.shp": b"1"}), tmp_path / "out", **LIMITS)
    with pytest.raises(InvalidShapefileArchiveError, match=r"\.shx.*\.dbf"):
        find_shapefile(files)


def test_multiple_shapefiles_are_rejected_with_clear_message(tmp_path):
    entries = {f"{n}.{e}": b"x" for n in ("a", "b") for e in ("shp", "shx", "dbf")}
    files = extract_zip_safely(_zip(tmp_path, entries), tmp_path / "out", **LIMITS)
    with pytest.raises(InvalidShapefileArchiveError, match="2 shapefiles"):
        find_shapefile(files)


def test_extension_matching_is_case_insensitive(tmp_path):
    z = _zip(tmp_path, {"A.SHP": b"1", "A.SHX": b"2", "A.DBF": b"3"})
    files = extract_zip_safely(z, tmp_path / "out", **LIMITS)
    assert find_shapefile(files).name == "A.SHP"
