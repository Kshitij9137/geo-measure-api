import geopandas as gpd
import pytest
from pyproj import CRS, Geod
from shapely.geometry import LineString, Point, box

from geospatial.exceptions import MissingCRSError
from geospatial.services.crs import (
    crs_label,
    distorts_area,
    is_metre_based,
    select_measurement_crs,
)
from geospatial.services.measurements import measure_geometry

from .conftest import BLR_LAT, BLR_LON


def test_geographic_crs_is_reprojected_to_utm_43n_for_bengaluru(geographic_gdf):
    selection = select_measurement_crs(geographic_gdf)
    assert selection.reprojected is True
    assert selection.label == "EPSG:32643"
    assert is_metre_based(selection.crs)


def test_projected_metre_crs_is_used_as_is(projected_gdf):
    selection = select_measurement_crs(projected_gdf)
    assert selection.reprojected is False
    assert selection.label == "EPSG:32643"


def test_web_mercator_is_reprojected_because_it_distorts_area(geographic_gdf):
    gdf = geographic_gdf.to_crs(3857)
    assert distorts_area(gdf.crs)
    selection = select_measurement_crs(gdf)
    assert selection.reprojected is True
    assert selection.label == "EPSG:32643"


def test_feet_based_projected_crs_is_reprojected_to_metres():
    # NY State Plane Long Island (US survey feet)
    gdf = gpd.GeoDataFrame(geometry=[box(-74.0, 40.7, -73.99, 40.71)], crs="EPSG:4326").to_crs(2263)
    assert not is_metre_based(gdf.crs)
    selection = select_measurement_crs(gdf)
    assert selection.reprojected is True
    assert is_metre_based(selection.crs)
    assert selection.label == "EPSG:32618"


def test_utm_and_national_grids_are_not_flagged_as_distorting():
    assert not distorts_area(CRS.from_epsg(32643))
    assert not distorts_area(CRS.from_epsg(27700))  # British National Grid (transverse mercator)


def test_missing_crs_raises():
    gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)])
    with pytest.raises(MissingCRSError):
        select_measurement_crs(gdf)


def test_dataset_spanning_multiple_utm_zones_adds_warning():
    gdf = gpd.GeoDataFrame(geometry=[Point(70, 20), Point(85, 22)], crs="EPSG:4326")
    selection = select_measurement_crs(gdf)
    assert any("more than one UTM zone" in w for w in selection.warnings)


def test_single_zone_dataset_has_no_warning(geographic_gdf):
    assert select_measurement_crs(geographic_gdf).warnings == []


def test_all_null_geometries_do_not_crash_selection():
    gdf = gpd.GeoDataFrame(geometry=[None, None], crs="EPSG:4326")
    selection = select_measurement_crs(gdf)
    assert selection.reprojected is False


def test_crs_label_falls_back_to_name_without_authority():
    custom = CRS.from_proj4(
        "+proj=tmerc +ellps=WGS84 +lat_0=0 +lon_0=77 +k=0.9 +x_0=0 +y_0=0 +units=m +no_defs"
    )
    assert crs_label(custom)  # non-empty, does not raise


# ---- Accuracy: prove we are NOT computing in degrees ---------------------------------------
GEOD = Geod(ellps="WGS84")


def _projected_geometry(geom, source="EPSG:4326"):
    gdf = gpd.GeoDataFrame(geometry=[geom], crs=source)
    selection = select_measurement_crs(gdf)
    return gdf.geometry.to_crs(selection.crs).iloc[0]


def test_area_in_utm_matches_geodesic_area_on_the_ellipsoid():
    polygon = box(BLR_LON, BLR_LAT, BLR_LON + 0.01, BLR_LAT + 0.01)  # ~1.1 km square
    projected_area = measure_geometry(_projected_geometry(polygon)).value
    geodesic_area = abs(GEOD.geometry_area_perimeter(polygon)[0])
    assert projected_area == pytest.approx(geodesic_area, rel=0.005)  # within 0.5%
    assert 1.1e6 < projected_area < 1.4e6  # ~1.2 km², nowhere near the raw degree value


def test_length_in_utm_matches_geodesic_length():
    line = LineString([(BLR_LON, BLR_LAT), (BLR_LON + 0.02, BLR_LAT + 0.01)])
    projected_length = measure_geometry(_projected_geometry(line)).value
    geodesic_length = GEOD.geometry_length(line)
    assert projected_length == pytest.approx(geodesic_length, rel=0.005)


def test_naive_degree_calculation_would_be_wildly_wrong():
    polygon = box(BLR_LON, BLR_LAT, BLR_LON + 0.01, BLR_LAT + 0.01)
    assert polygon.area == pytest.approx(0.0001)  # "square degrees" - meaningless as m²
    assert measure_geometry(_projected_geometry(polygon)).value > 1_000_000


def test_web_mercator_area_would_be_wrong_but_we_correct_it():
    polygon = box(BLR_LON, BLR_LAT + 40, BLR_LON + 0.01, BLR_LAT + 40.01)  # ~53°N
    geodesic_area = abs(GEOD.geometry_area_perimeter(polygon)[0])
    in_3857 = gpd.GeoSeries([polygon], crs=4326).to_crs(3857).iloc[0].area
    assert in_3857 > geodesic_area * 2  # Web Mercator inflates area by 1/cos²(lat) ~ 2.8x here
    gdf = gpd.GeoDataFrame(geometry=[polygon], crs=4326).to_crs(3857)
    selection = select_measurement_crs(gdf)
    corrected = measure_geometry(gdf.geometry.to_crs(selection.crs).iloc[0]).value
    assert corrected == pytest.approx(geodesic_area, rel=0.01)


def test_polar_dataset_beyond_utm_range_raises_controlled_error():
    from geospatial.exceptions import CRSError

    gdf = gpd.GeoDataFrame(geometry=[Point(10, 89.5), Point(10.1, 89.5)], crs="EPSG:4326")
    with pytest.raises(CRSError):
        select_measurement_crs(gdf)
