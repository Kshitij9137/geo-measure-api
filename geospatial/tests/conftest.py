"""Test fixtures. Geospatial files are generated programmatically, not committed as binaries."""

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, box

# Bengaluru, EPSG:4326 (lon, lat). A 0.001° box is ~109 m x ~110 m.
BLR_LON, BLR_LAT = 77.5946, 12.9716


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


def zip_directory_bytes(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.iterdir()):
            archive.write(path, path.name)
    return buffer.getvalue()


@pytest.fixture
def make_shapefile_zip(tmp_path):
    """Write a GeoDataFrame to a zipped Shapefile and return the ZIP bytes."""
    counter = {"n": 0}

    def _make(gdf: gpd.GeoDataFrame, name: str = "survey") -> bytes:
        counter["n"] += 1
        out = tmp_path / f"shp_{counter['n']}"
        out.mkdir()
        gdf.to_file(out / f"{name}.shp", driver="ESRI Shapefile", engine="pyogrio")
        return zip_directory_bytes(out)

    return _make


@pytest.fixture
def geographic_gdf():
    """Mixed features in EPSG:4326 around Bengaluru."""
    d = 0.001
    return gpd.GeoDataFrame(
        {
            "name": ["Area A", "Road", "Tower"],
            "elevation": [920.5, None, 935],
        },
        geometry=[
            box(BLR_LON, BLR_LAT, BLR_LON + d, BLR_LAT + d),
            LineString([(BLR_LON, BLR_LAT), (BLR_LON + d, BLR_LAT)]),
            Point(BLR_LON, BLR_LAT),
        ],
        crs="EPSG:4326",
    )


@pytest.fixture
def polygon_gdf():
    """Two polygons in EPSG:4326 (a Shapefile holds a single geometry type, so no mixing here)."""
    d = 0.001
    return gpd.GeoDataFrame(
        {"name": ["Area A", "Area B"], "elevation": [920.5, None]},
        geometry=[
            box(BLR_LON, BLR_LAT, BLR_LON + d, BLR_LAT + d),
            box(BLR_LON + 0.01, BLR_LAT, BLR_LON + 0.01 + d, BLR_LAT + d),
        ],
        crs="EPSG:4326",
    )


@pytest.fixture
def line_gdf():
    d = 0.001
    return gpd.GeoDataFrame(
        {"name": ["Road"]},
        geometry=[LineString([(BLR_LON, BLR_LAT), (BLR_LON + d, BLR_LAT)])],
        crs="EPSG:4326",
    )


@pytest.fixture
def projected_gdf():
    """100 m square, a 3-4-5 line and a point in EPSG:32643 (UTM 43N, metres)."""
    return gpd.GeoDataFrame(
        {"name": ["Square", "Line345", "Pt"]},
        geometry=[box(0, 0, 100, 100), LineString([(0, 0), (3, 4)]), Point(5, 5)],
        crs="EPSG:32643",
    ).set_geometry("geometry")


KML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
{body}
</Document>
</kml>
"""

KML_POLYGON = """<Placemark><name>Plot</name>
<ExtendedData><Data name="owner"><value>Aereo</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>
77.5946,12.9716,0 77.5956,12.9716,0 77.5956,12.9726,0 77.5946,12.9726,0 77.5946,12.9716,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"""

KML_LINE = """<Placemark><name>Road</name><LineString><coordinates>
77.5946,12.9716,10 77.5956,12.9716,12
</coordinates></LineString></Placemark>"""

KML_POINT = (
    """<Placemark><name>Tower</name><Point><coordinates>77.5946,12.9716,0</coordinates></Point></Placemark>"""
)

KML_MULTIGEOMETRY = """<Placemark><name>Mixed</name><MultiGeometry>
<Point><coordinates>77.5946,12.9716</coordinates></Point>
<LineString><coordinates>77.5946,12.9716 77.5950,12.9720</coordinates></LineString>
</MultiGeometry></Placemark>"""


@pytest.fixture
def kml_bytes():
    def _make(*placemarks: str) -> bytes:
        return KML_TEMPLATE.format(body="\n".join(placemarks)).encode()

    return _make


@pytest.fixture
def kml_folders_bytes():
    """A KML with two folders (GDAL exposes these as two layers)."""
    body = (
        f"<Folder><name>Plots</name>{KML_POLYGON}</Folder>\n"
        f"<Folder><name>Roads</name>{KML_LINE}{KML_POINT}</Folder>"
    )
    return KML_TEMPLATE.format(body=body).encode()
