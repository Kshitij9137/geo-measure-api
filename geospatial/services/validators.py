"""Upload validation: presence, extension, size and a light content sniff.

We don't trust the extension alone: a ``.zip`` must really be a ZIP and a ``.kml`` must look
like XML/KML. Deep validation (Shapefile components, geometry) happens in the readers.
"""

from __future__ import annotations

import os
import zipfile

from django.conf import settings

from geospatial.exceptions import (
    FileTooLargeError,
    MissingFileError,
    UnreadableFileError,
    UnsupportedFileTypeError,
)

from .types import FileType

EXTENSION_TO_TYPE = {".kml": FileType.KML, ".zip": FileType.SHAPEFILE}


def validate_upload(upload) -> FileType:
    """Validate a Django ``UploadedFile`` and return the detected ``FileType``."""
    if upload is None:
        raise MissingFileError()

    extension = os.path.splitext(upload.name or "")[1].lower()
    file_type = EXTENSION_TO_TYPE.get(extension)
    if file_type is None:
        raise UnsupportedFileTypeError()

    if upload.size == 0:
        raise UnreadableFileError("The uploaded file is empty.")
    if upload.size > settings.MAX_UPLOAD_SIZE_BYTES:
        limit_mb = settings.MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
        raise FileTooLargeError(f"The uploaded file exceeds the {limit_mb:g} MB limit.")

    _sniff_content(upload, file_type)
    return file_type


def _sniff_content(upload, file_type: FileType) -> None:
    upload.seek(0)
    try:
        if file_type is FileType.SHAPEFILE:
            if not zipfile.is_zipfile(upload):
                raise UnreadableFileError("The file has a .zip extension but is not a valid ZIP archive.")
        else:
            head = upload.read(8192).decode("utf-8", errors="ignore").lower()
            if "<kml" not in head:
                raise UnreadableFileError("The file has a .kml extension but does not look like KML.")
    finally:
        upload.seek(0)
