"""Hardened ZIP extraction and Shapefile discovery.

Protections: path traversal / absolute paths, symlinks, entry-count limit, total uncompressed
size limit (enforced while streaming, because ZIP headers can lie), and junk-file skipping.
"""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from geospatial.exceptions import (
    ArchiveLimitsExceededError,
    InvalidShapefileArchiveError,
)

REQUIRED_COMPONENTS = (".shp", ".shx", ".dbf")
_CHUNK = 1024 * 64


def _is_junk(parts: tuple[str, ...]) -> bool:
    return parts[0] == "__MACOSX" or parts[-1].startswith("._") or parts[-1] == ".DS_Store"


def _safe_relative_path(name: str) -> PurePosixPath:
    normalised = name.replace("\\", "/")
    path = PurePosixPath(normalised)
    if path.is_absolute() or (len(normalised) > 1 and normalised[1] == ":"):
        raise InvalidShapefileArchiveError("The ZIP archive contains an absolute path.")
    if ".." in path.parts:
        raise InvalidShapefileArchiveError("The ZIP archive contains an unsafe path (path traversal).")
    return path


def extract_zip_safely(zip_path: Path, dest: Path, *, max_files: int, max_total_bytes: int) -> list[Path]:
    """Extract ``zip_path`` into ``dest`` and return the extracted file paths."""
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    extracted: list[Path] = []
    total = 0

    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) > max_files:
                raise ArchiveLimitsExceededError(f"The ZIP archive contains more than {max_files} files.")
            if sum(m.file_size for m in members) > max_total_bytes:
                raise ArchiveLimitsExceededError(
                    "The ZIP archive's uncompressed size exceeds the allowed limit."
                )

            for member in members:
                relative = _safe_relative_path(member.filename)
                if not relative.parts or _is_junk(relative.parts):
                    continue
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise InvalidShapefileArchiveError("The ZIP archive contains a symbolic link.")

                target = (dest_root / Path(*relative.parts)).resolve()
                if not target.is_relative_to(dest_root):
                    raise InvalidShapefileArchiveError(
                        "The ZIP archive contains an unsafe path (path traversal)."
                    )
                target.parent.mkdir(parents=True, exist_ok=True)

                with archive.open(member) as src, open(target, "wb") as out:
                    while chunk := src.read(_CHUNK):
                        total += len(chunk)
                        if total > max_total_bytes:
                            raise ArchiveLimitsExceededError(
                                "The ZIP archive's uncompressed size exceeds the allowed limit."
                            )
                        out.write(chunk)
                extracted.append(target)
    except zipfile.BadZipFile as exc:
        raise InvalidShapefileArchiveError("The file is not a valid ZIP archive.") from exc
    except (RuntimeError, NotImplementedError) as exc:  # encrypted / unsupported compression
        raise InvalidShapefileArchiveError(
            "The ZIP archive is encrypted or uses an unsupported compression method."
        ) from exc
    except OSError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise InvalidShapefileArchiveError(f"The ZIP archive could not be extracted: {exc}") from exc

    return extracted


def find_shapefile(files: list[Path]) -> Path:
    """Return the single ``.shp`` in ``files`` after checking its sibling components."""
    shapefiles = [f for f in files if f.suffix.lower() == ".shp"]
    if not shapefiles:
        raise InvalidShapefileArchiveError("The ZIP archive does not contain a .shp file.")
    if len(shapefiles) > 1:
        names = ", ".join(sorted(f.name for f in shapefiles))
        raise InvalidShapefileArchiveError(
            f"The ZIP archive contains {len(shapefiles)} shapefiles ({names}); "
            "upload one shapefile per archive."
        )

    shp = shapefiles[0]
    siblings = {f.suffix.lower() for f in files if f.parent == shp.parent and f.stem == shp.stem}
    missing = [ext for ext in REQUIRED_COMPONENTS if ext not in siblings]
    if missing:
        raise InvalidShapefileArchiveError(
            f"The Shapefile is missing required component(s): {', '.join(missing)}."
        )
    return shp
