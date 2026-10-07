import pytest
from shapely import wkt
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from geospatial.services.measurements import geometry_type_name, measure_geometries, measure_geometry
from geospatial.services.types import MeasurementStatus as S


def test_polygon_area_100m_square_is_10000_sqm():
    m = measure_geometry(box(0, 0, 100, 100))
    assert m.status is S.SUPPORTED
    assert (m.type, m.unit) == ("area", "m²")
    assert m.value == pytest.approx(10_000, rel=1e-9)


def test_linestring_length_3_4_5_triangle():
    m = measure_geometry(LineString([(0, 0), (3, 4)]))
    assert (m.type, m.unit) == ("length", "m")
    assert m.value == pytest.approx(5.0)


def test_polygon_with_hole_subtracts_hole():
    poly = Polygon(box(0, 0, 100, 100).exterior.coords, [box(25, 25, 75, 75).exterior.coords])
    assert measure_geometry(poly).value == pytest.approx(10_000 - 2_500)


def test_multipolygon_area_is_summed():
    mp = MultiPolygon([box(0, 0, 10, 10), box(20, 20, 30, 30)])
    assert measure_geometry(mp).value == pytest.approx(200)


def test_multilinestring_length_is_summed():
    ml = MultiLineString([[(0, 0), (3, 4)], [(10, 10), (10, 20)]])
    assert measure_geometry(ml).value == pytest.approx(15)


@pytest.mark.parametrize("geom", [Point(1, 2), MultiPoint([(1, 2), (3, 4)])])
def test_points_need_no_measurement(geom):
    m = measure_geometry(geom)
    assert m.status is S.NOT_REQUIRED
    assert m.value is None


def test_geometry_collection_is_unsupported_not_a_crash():
    m = measure_geometry(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])]))
    assert m.status is S.UNSUPPORTED
    assert "GeometryCollection" in m.error


def test_self_intersecting_polygon_is_invalid():
    bowtie = wkt.loads("POLYGON((0 0, 10 10, 10 0, 0 10, 0 0))")
    m = measure_geometry(bowtie)
    assert m.status is S.INVALID
    assert "Self-intersection" in m.error


@pytest.mark.parametrize("geom, message", [(None, "Missing"), (Polygon(), "Empty")])
def test_missing_and_empty_geometries_are_invalid(geom, message):
    m = measure_geometry(geom)
    assert m.status is S.INVALID
    assert message in m.error


def test_non_finite_result_is_invalid():
    m = measure_geometry(LineString([(0, 0), (float("inf"), 1)]))
    assert m.status is S.INVALID


def test_vectorised_results_keep_input_order_and_mixed_types():
    results = measure_geometries([box(0, 0, 10, 10), None, Point(0, 0), LineString([(0, 0), (0, 7)])])
    assert [r.status for r in results] == [S.SUPPORTED, S.INVALID, S.NOT_REQUIRED, S.SUPPORTED]
    assert results[0].value == pytest.approx(100)
    assert results[3].value == pytest.approx(7)


def test_geometry_type_name():
    assert geometry_type_name(None) is None
    assert geometry_type_name(MultiPolygon([box(0, 0, 1, 1)])) == "MultiPolygon"
