"""Measurement engine.

Pure functions: they take geometries that are *already in a metre-based projected CRS* and
return ``Measurement`` objects. They know nothing about Django, files or CRS selection.
Calculation is vectorised with Shapely 2 (``shapely.area`` / ``shapely.length``).

Z coordinates are ignored: Shapely's area/length are planar 2D calculations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import shapely

from .types import MeasurementStatus

AREA_UNIT = "m²"
LENGTH_UNIT = "m"

# Shapely geometry type ids
_POINT_TYPES = {0, 4}  # Point, MultiPoint
_LINE_TYPES = {1, 2, 5}  # LineString, LinearRing, MultiLineString
_POLYGON_TYPES = {3, 6}  # Polygon, MultiPolygon

_TYPE_NAMES = {
    0: "Point",
    1: "LineString",
    2: "LinearRing",
    3: "Polygon",
    4: "MultiPoint",
    5: "MultiLineString",
    6: "MultiPolygon",
    7: "GeometryCollection",
}


@dataclass(frozen=True)
class Measurement:
    status: MeasurementStatus
    type: str | None = None  # "area" | "length"
    value: float | None = None
    unit: str | None = None
    error: str | None = None


def geometry_type_name(geometry) -> str | None:
    """Return the geometry type name, or None for a missing geometry."""
    if geometry is None:
        return None
    return _TYPE_NAMES.get(int(shapely.get_type_id(geometry)), geometry.geom_type)


def measure_geometries(geometries: Sequence) -> list[Measurement]:
    """Measure many projected geometries at once (area for polygons, length for lines)."""
    arr = np.empty(len(geometries), dtype=object)
    arr[:] = list(geometries)

    type_ids = shapely.get_type_id(arr)  # -1 for missing
    missing = shapely.is_missing(arr)
    empty = shapely.is_empty(arr)
    valid = shapely.is_valid(arr)
    with np.errstate(invalid="ignore"):
        areas = shapely.area(arr)
        lengths = shapely.length(arr)

    results: list[Measurement] = []
    for i, tid in enumerate(type_ids):
        tid = int(tid)
        if missing[i]:
            results.append(_invalid("Missing geometry"))
        elif empty[i]:
            results.append(_invalid("Empty geometry"))
        elif tid in _POINT_TYPES:
            results.append(Measurement(MeasurementStatus.NOT_REQUIRED))
        elif tid in _POLYGON_TYPES or tid in _LINE_TYPES:
            if not valid[i]:
                reason = shapely.is_valid_reason(arr[i])
                results.append(_invalid(f"Invalid geometry: {reason}"))
                continue
            if tid in _POLYGON_TYPES:
                results.append(_supported("area", float(areas[i]), AREA_UNIT))
            else:
                results.append(_supported("length", float(lengths[i]), LENGTH_UNIT))
        else:
            name = _TYPE_NAMES.get(tid, "unknown")
            results.append(
                Measurement(
                    MeasurementStatus.UNSUPPORTED,
                    error=f"Measurement is not supported for {name} geometries.",
                )
            )
    return results


def measure_geometry(geometry) -> Measurement:
    """Convenience wrapper for a single geometry."""
    return measure_geometries([geometry])[0]


def _supported(kind: str, value: float, unit: str) -> Measurement:
    if not np.isfinite(value):
        return _invalid("Measurement is not finite; coordinates may be out of range for the dataset's CRS.")
    return Measurement(MeasurementStatus.SUPPORTED, type=kind, value=value, unit=unit)


def _invalid(message: str) -> Measurement:
    return Measurement(MeasurementStatus.INVALID, error=message)
