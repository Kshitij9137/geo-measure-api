"""CRS detection and measurement-CRS selection.

Strategy
--------
1. No CRS                          -> ``MissingCRSError`` (we refuse to guess).
2. Projected, metre-based, and not a Mercator-family projection
                                    -> measure in the file's own CRS (no reprojection).
3. Anything else (geographic degrees, Web Mercator / other Mercator variants, feet-based
   projections)                    -> reproject to the UTM zone estimated from the data.

Rule 3 matters: EPSG:3857 is "projected" but inflates areas by 1/cos²(lat) (≈ 4x at lat 60),
and feet-based CRSs would silently make "m²" wrong by a factor of ~10.76.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import geopandas as gpd
from pyproj import CRS
from pyproj.exceptions import ProjError

from geospatial.exceptions import CRSError, MissingCRSError

logger = logging.getLogger(__name__)

# One UTM zone is 6 degrees wide; a dataset wider than this is distorted by single-zone UTM.
UTM_ZONE_WIDTH_DEGREES = 6.0


@dataclass(frozen=True)
class MeasurementCRS:
    crs: CRS
    label: str
    reprojected: bool
    warnings: list[str] = field(default_factory=list)


def crs_label(crs: CRS) -> str:
    """Human-readable identifier, e.g. ``EPSG:4326``; falls back to the CRS name."""
    authority = crs.to_authority(min_confidence=70)
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.name


def is_metre_based(crs: CRS) -> bool:
    axes = crs.axis_info
    return bool(axes) and all(abs(a.unit_conversion_factor - 1.0) < 1e-9 for a in axes)


def distorts_area(crs: CRS) -> bool:
    """True for non-equal-area-ish Mercator-family projections (Web Mercator, World Mercator...).

    Transverse Mercator (UTM, national grids) is intentionally *not* flagged.
    """
    operation = crs.coordinate_operation
    method = (operation.method_name if operation else "").lower()
    name = crs.name.lower()
    if "popular visualisation" in method or "auxiliary sphere" in method:
        return True
    if "mercator" in method and "transverse" not in method:
        return True
    return "web mercator" in name or "pseudo-mercator" in name


def is_suitable_projected_crs(crs: CRS) -> bool:
    return crs.is_projected and not crs.is_geographic and is_metre_based(crs) and not distorts_area(crs)


def select_measurement_crs(gdf: gpd.GeoDataFrame) -> MeasurementCRS:
    """Decide which CRS the dataset's geometries should be measured in."""
    crs = gdf.crs
    if crs is None:
        raise MissingCRSError()

    if is_suitable_projected_crs(crs):
        logger.info("Dataset CRS %s is projected and metre-based; measuring in place", crs_label(crs))
        return MeasurementCRS(crs=crs, label=crs_label(crs), reprojected=False)

    usable = gdf.geometry[~(gdf.geometry.isna() | gdf.geometry.is_empty)]
    if usable.empty:
        # Nothing measurable; keep the original CRS so downstream code still works.
        return MeasurementCRS(crs=crs, label=crs_label(crs), reprojected=False)

    try:
        target = usable.estimate_utm_crs()
        geographic_bounds = usable.to_crs(crs.geodetic_crs).total_bounds
    except (ProjError, RuntimeError, ValueError) as exc:
        raise CRSError(f"Could not estimate a UTM CRS for this dataset: {exc}") from exc

    warnings: list[str] = []
    lon_span = float(geographic_bounds[2] - geographic_bounds[0])
    if lon_span > UTM_ZONE_WIDTH_DEGREES:
        warnings.append(
            f"Dataset spans {lon_span:.1f}° of longitude (more than one UTM zone); "
            f"measurements in {crs_label(target)} may be noticeably distorted."
        )

    logger.info("Selected measurement CRS %s for dataset CRS %s", crs_label(target), crs_label(crs))
    return MeasurementCRS(crs=target, label=crs_label(target), reprojected=True, warnings=warnings)
