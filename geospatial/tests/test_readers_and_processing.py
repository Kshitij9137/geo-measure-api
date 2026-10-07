import datetime as dt
import math

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely import wkt
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, box

from geospatial.exceptions import EmptyDatasetError, MissingCRSError, UnreadableFileError
from geospatial.services.analysis import analyze_file
from geospatial.services.properties import sanitize_value
from geospatial.services.readers import KMLReader, ShapefileReader
from geospatial.services.types import FileStatus, FileType
from geospatial.services.types import MeasurementStatus as S

from .conftest import KML_LINE, KML_MULTIGEOMETRY, KML_POINT, KML_POLYGON


def _write(tmp_path, name, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ---- KML ------------------------------------------------------------------------------------
def test_kml_reader_assumes_wgs84_and_reads_all_layers(tmp_path, kml_folders_bytes):
    gdf = KMLReader().read(_write(tmp_path, "a.kml", kml_folders_bytes), tmp_path)
    assert gdf.crs.to_epsg() == 4326
    assert len(gdf) == 3  # 1 polygon from "Plots" + line and point from "Roads"
    assert set(gdf["__layer__"]) == {"Plots", "Roads"}


def test_kml_pipeline_measures_polygon_line_point(tmp_path, kml_bytes):
    path = _write(tmp_path, "s.kml", kml_bytes(KML_POLYGON, KML_LINE, KML_POINT))
    result = analyze_file(path, FileType.KML, tmp_path / "w")
    assert result.crs == "EPSG:4326"
    assert result.measurement_crs == "EPSG:32643"
    by_type = {f.geometry_type: f for f in result.features}
    assert by_type["Polygon"].measurement.value == pytest.approx(12_000, rel=0.05)  # ~109m x ~110m
    assert by_type["LineString"].measurement.value == pytest.approx(108.5, rel=0.02)
    assert by_type["Point"].measurement.status is S.NOT_REQUIRED
    assert result.status is FileStatus.COMPLETED
    assert result.summary["measurement_summary"] == {
        "measured": 2,
        "not_required": 1,
        "unsupported": 0,
        "invalid": 0,
    }


def test_kml_with_z_coordinates_is_measured_in_2d(tmp_path, kml_bytes):
    result = analyze_file(_write(tmp_path, "z.kml", kml_bytes(KML_LINE)), FileType.KML, tmp_path / "w")
    assert result.features[0].measurement.status is S.SUPPORTED


def test_kml_multigeometry_is_unsupported_and_file_completes_with_warnings(tmp_path, kml_bytes):
    path = _write(tmp_path, "m.kml", kml_bytes(KML_POLYGON, KML_MULTIGEOMETRY))
    result = analyze_file(path, FileType.KML, tmp_path / "w")
    statuses = {f.geometry_type: f.measurement.status for f in result.features}
    assert statuses["GeometryCollection"] is S.UNSUPPORTED
    assert statuses["Polygon"] is S.SUPPORTED
    assert result.status is FileStatus.COMPLETED_WITH_WARNINGS


def test_kml_extended_data_becomes_properties(tmp_path, kml_bytes):
    result = analyze_file(_write(tmp_path, "p.kml", kml_bytes(KML_POLYGON)), FileType.KML, tmp_path / "w")
    props = result.features[0].properties
    assert props["Name"] == "Plot" and props["owner"] == "Aereo"
    assert "__layer__" not in props


def test_malformed_kml_raises_controlled_error(tmp_path):
    with pytest.raises((UnreadableFileError, EmptyDatasetError)):
        analyze_file(_write(tmp_path, "bad.kml", b"<kml><Document><Placemark>"), FileType.KML, tmp_path / "w")


def test_kml_without_placemarks_is_empty_dataset(tmp_path, kml_bytes):
    with pytest.raises(EmptyDatasetError):
        analyze_file(_write(tmp_path, "e.kml", kml_bytes()), FileType.KML, tmp_path / "w")


# ---- Shapefile ------------------------------------------------------------------------------
def test_shapefile_geographic_pipeline(tmp_path, make_shapefile_zip, polygon_gdf):
    path = _write(tmp_path, "s.zip", make_shapefile_zip(polygon_gdf))
    result = analyze_file(path, FileType.SHAPEFILE, tmp_path / "w")
    assert (result.crs, result.measurement_crs) == ("EPSG:4326", "EPSG:32643")
    assert [f.geometry_type for f in result.features] == ["Polygon", "Polygon"]
    assert result.features[0].measurement.value == pytest.approx(109 * 110.5, rel=0.03)
    assert result.features[0].properties["name"] == "Area A"
    assert result.features[1].properties["elevation"] is None  # NaN -> null, JSON-safe
    assert result.status is FileStatus.COMPLETED


def test_shapefile_geographic_linestring_length(tmp_path, make_shapefile_zip, line_gdf):
    result = analyze_file(
        _write(tmp_path, "l.zip", make_shapefile_zip(line_gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.features[0].measurement.type == "length"
    assert result.features[0].measurement.value == pytest.approx(108.5, rel=0.02)


def test_shapefile_projected_polygon_is_measured_in_place_exactly(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 100, 100)], crs="EPSG:32643")
    result = analyze_file(
        _write(tmp_path, "p.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.crs == result.measurement_crs == "EPSG:32643"
    assert result.features[0].measurement.value == pytest.approx(10_000)


def test_shapefile_projected_line_is_measured_in_place_exactly(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (3, 4)])], crs="EPSG:32643")
    result = analyze_file(
        _write(tmp_path, "pl.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.features[0].measurement.value == pytest.approx(5.0)


def test_shapefile_multipolygon(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(
        geometry=[MultiPolygon([box(0, 0, 10, 10), box(20, 20, 30, 30)])], crs="EPSG:32643"
    )
    result = analyze_file(
        _write(tmp_path, "mp.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.features[0].geometry_type == "MultiPolygon"
    assert result.features[0].measurement.value == pytest.approx(200)


def test_shapefile_multilinestring(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(
        geometry=[MultiLineString([[(0, 0), (3, 4)], [(10, 10), (10, 20)]])], crs="EPSG:32643"
    )
    result = analyze_file(
        _write(tmp_path, "ml.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.features[0].geometry_type == "MultiLineString"
    assert result.features[0].measurement.value == pytest.approx(15)


def test_shapefile_points_need_no_measurement(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame({"name": ["T"]}, geometry=[Point(77.59, 12.97)], crs="EPSG:4326")
    result = analyze_file(
        _write(tmp_path, "pt.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.features[0].measurement.status is S.NOT_REQUIRED
    assert result.features[0].measurement.value is None


def test_shapefile_without_prj_raises_crs_missing(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)])  # no CRS -> no .prj written
    with pytest.raises(MissingCRSError):
        ShapefileReader().read(_write(tmp_path, "n.zip", make_shapefile_zip(gdf)), tmp_path)


def test_invalid_polygon_is_flagged_and_file_completes_with_warnings(tmp_path, make_shapefile_zip):
    bowtie = wkt.loads("POLYGON((0 0, 10 10, 10 0, 0 10, 0 0))")
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10), bowtie], crs="EPSG:32643")
    result = analyze_file(
        _write(tmp_path, "i.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert [f.measurement.status for f in result.features] == [S.SUPPORTED, S.INVALID]
    assert result.status is FileStatus.COMPLETED_WITH_WARNINGS
    assert result.summary["measurement_summary"]["invalid"] == 1


def test_shapefile_with_null_geometry_does_not_crash(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=[box(0, 0, 5, 5), None], crs="EPSG:32643")
    result = analyze_file(
        _write(tmp_path, "nul.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.features[1].geometry is None
    assert result.features[1].geometry_type is None
    assert result.features[1].measurement.status is S.INVALID
    assert result.summary["geometry_summary"] == {"Polygon": 1, "Missing": 1}


def test_empty_shapefile_is_a_controlled_error(tmp_path, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs="EPSG:32643"))
    with pytest.raises(EmptyDatasetError):
        analyze_file(_write(tmp_path, "emp.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w")


def test_feet_based_shapefile_is_converted_to_metres(tmp_path, make_shapefile_zip):
    square_m = box(-74.0, 40.7, -73.999, 40.701)
    gdf = gpd.GeoDataFrame(geometry=[square_m], crs="EPSG:4326").to_crs(2263)  # US survey feet
    result = analyze_file(
        _write(tmp_path, "ft.zip", make_shapefile_zip(gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    assert result.measurement_crs == "EPSG:32618"
    assert result.features[0].measurement.value == pytest.approx(84.5 * 111.2, rel=0.05)  # m², not ft²


def test_all_geometry_json_is_serialisable(tmp_path, make_shapefile_zip, polygon_gdf):
    import json

    result = analyze_file(
        _write(tmp_path, "j.zip", make_shapefile_zip(polygon_gdf)), FileType.SHAPEFILE, tmp_path / "w"
    )
    for f in result.features:
        json.dumps(f.geometry, allow_nan=False)
        json.dumps(f.properties, allow_nan=False)


# ---- Property sanitising --------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        (float("nan"), None),
        (float("inf"), None),
        (pd.NaT, None),
        (pd.NA, None),
        (None, None),
        (np.int64(7), 7),
        (np.float64(1.5), 1.5),
        (np.bool_(True), True),
        (pd.Timestamp("2024-05-01 10:00"), "2024-05-01T10:00:00"),
        (dt.date(2024, 5, 1), "2024-05-01"),
        (b"abc", "abc"),
        ("text", "text"),
        ({1, 2}, str({1, 2})),
    ],
)
def test_sanitize_value(value, expected):
    assert sanitize_value(value) == expected


def test_sanitize_value_keeps_finite_floats():
    assert math.isclose(sanitize_value(3.14), 3.14)
