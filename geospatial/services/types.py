"""Plain enums shared by the services and the Django models (no Django imports here)."""

from __future__ import annotations

from enum import StrEnum


class FileType(StrEnum):
    KML = "KML"
    SHAPEFILE = "SHAPEFILE"


class MeasurementStatus(StrEnum):
    SUPPORTED = "SUPPORTED"  # an area / length was calculated
    NOT_REQUIRED = "NOT_REQUIRED"  # e.g. points: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"  # e.g. GeometryCollection
    INVALID = "INVALID"  # null / empty / self-intersecting / non-finite


class FileStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
