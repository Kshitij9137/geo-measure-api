"""Domain exceptions.

Every error that should reach the API client as a controlled response derives from
``GeoMeasureError``. Each carries a stable machine-readable ``code`` and an HTTP status,
and the single DRF exception handler turns them into ``{"code": ..., "message": ...}``.
"""

from __future__ import annotations


class GeoMeasureError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    default_message = "The request could not be processed."

    def __init__(self, message: str | None = None, **extra):
        self.message = message or self.default_message
        self.extra = extra
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


# --- Upload validation -------------------------------------------------------------------
class MissingFileError(GeoMeasureError):
    code = "FILE_MISSING"
    default_message = "No file was provided. Send a multipart/form-data request with a 'file' field."


class UnsupportedFileTypeError(GeoMeasureError):
    code = "UNSUPPORTED_FILE_TYPE"
    default_message = "Only .kml and .zip (containing a Shapefile) files are supported."


class FileTooLargeError(GeoMeasureError):
    status_code = 413
    code = "FILE_TOO_LARGE"
    default_message = "The uploaded file exceeds the maximum allowed size."


# --- Reading -----------------------------------------------------------------------------
class UnreadableFileError(GeoMeasureError):
    code = "UNREADABLE_FILE"
    default_message = "The file could not be read as a valid geospatial dataset."


class InvalidShapefileArchiveError(GeoMeasureError):
    code = "INVALID_SHAPEFILE_ARCHIVE"
    default_message = "The ZIP archive does not contain a valid Shapefile."


class ArchiveLimitsExceededError(GeoMeasureError):
    code = "ARCHIVE_LIMITS_EXCEEDED"
    default_message = "The ZIP archive exceeds the allowed number of files or uncompressed size."


class EmptyDatasetError(GeoMeasureError):
    status_code = 422
    code = "EMPTY_DATASET"
    default_message = "The file was read successfully but contains no features."


# --- CRS ---------------------------------------------------------------------------------
class MissingCRSError(GeoMeasureError):
    status_code = 422
    code = "CRS_MISSING"
    default_message = (
        "The dataset does not contain CRS information, which is required for reliable measurement."
    )


class CRSError(GeoMeasureError):
    status_code = 422
    code = "CRS_ERROR"
    default_message = "A suitable coordinate reference system could not be determined."


# --- Lookup / internal -------------------------------------------------------------------
class UploadedFileNotFoundError(GeoMeasureError):
    status_code = 404
    code = "FILE_NOT_FOUND"
    default_message = "The requested file does not exist."


class InvalidQueryParameterError(GeoMeasureError):
    code = "INVALID_QUERY_PARAMETER"


class ProcessingError(GeoMeasureError):
    status_code = 500
    code = "PROCESSING_FAILED"
    default_message = "An unexpected error occurred while processing the file."
