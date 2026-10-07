"""File analysis pipeline — pure geospatial logic, independent of Django models and HTTP.

    read -> select measurement CRS -> reproject -> measure -> normalise features

``analyze_file`` is the one function a task queue worker would call.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import shapely

from geospatial.exceptions import EmptyDatasetError

from .crs import crs_label, select_measurement_crs
from .measurements import Measurement, geometry_type_name, measure_geometries
from .properties import sanitize_properties
from .readers import LAYER_COLUMN, get_reader
from .types import FileStatus, FileType, MeasurementStatus

logger = logging.getLogger(__name__)


@dataclass
class FeatureRecord:
    source_index: int
    layer: str | None
    geometry_type: str | None
    geometry: dict | None
    properties: dict
    measurement: Measurement


@dataclass
class AnalysisResult:
    crs: str
    measurement_crs: str
    features: list[FeatureRecord]
    summary: dict
    status: FileStatus
    warnings: list[str] = field(default_factory=list)


def analyze_file(path: Path, file_type: FileType, workdir: Path) -> AnalysisResult:
    gdf = get_reader(file_type).read(path, workdir)
    if len(gdf) == 0:
        raise EmptyDatasetError()

    selection = select_measurement_crs(gdf)
    original_label = crs_label(gdf.crs)

    geom_col = gdf.geometry.name
    projected = gdf.geometry.to_crs(selection.crs) if selection.reprojected else gdf.geometry
    measurements = measure_geometries(projected.to_numpy())

    layers = gdf[LAYER_COLUMN].tolist() if LAYER_COLUMN in gdf.columns else [None] * len(gdf)
    attributes = gdf.drop(columns=[c for c in (geom_col, LAYER_COLUMN) if c in gdf.columns])
    records = attributes.to_dict("records")

    features: list[FeatureRecord] = []
    for i, (geom, measurement) in enumerate(zip(gdf.geometry.to_numpy(), measurements, strict=True)):
        features.append(
            FeatureRecord(
                source_index=i,
                layer=layers[i],
                geometry_type=geometry_type_name(geom),
                geometry=None if geom is None else json.loads(shapely.to_geojson(geom)),
                properties=sanitize_properties(records[i]),
                measurement=measurement,
            )
        )

    summary = _summarise(features, selection.warnings)
    has_problems = any(
        f.measurement.status in (MeasurementStatus.INVALID, MeasurementStatus.UNSUPPORTED) for f in features
    )
    status = (
        FileStatus.COMPLETED_WITH_WARNINGS if has_problems or selection.warnings else FileStatus.COMPLETED
    )
    logger.info("Analysed %d feature(s): %s", len(features), summary["measurement_summary"])
    return AnalysisResult(
        crs=original_label,
        measurement_crs=selection.label,
        features=features,
        summary=summary,
        status=status,
        warnings=selection.warnings,
    )


def _summarise(features: list[FeatureRecord], warnings: list[str]) -> dict:
    geometry_counts = Counter(f.geometry_type or "Missing" for f in features)
    status_counts = Counter(f.measurement.status for f in features)
    return {
        "geometry_summary": dict(geometry_counts),
        "measurement_summary": {
            "measured": status_counts[MeasurementStatus.SUPPORTED],
            "not_required": status_counts[MeasurementStatus.NOT_REQUIRED],
            "unsupported": status_counts[MeasurementStatus.UNSUPPORTED],
            "invalid": status_counts[MeasurementStatus.INVALID],
        },
        "warnings": warnings,
    }
