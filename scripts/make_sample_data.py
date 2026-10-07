"""Regenerate the files in sample_data/ (run: python scripts/make_sample_data.py)."""

import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, box

OUT = Path(__file__).resolve().parent.parent / "sample_data"
LON, LAT = 77.5946, 12.9716  # Bengaluru

KML = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document><name>Bengaluru survey</name>
<Folder><name>Plots</name>
<Placemark><name>Plot A</name><ExtendedData><Data name="owner"><value>Aereo</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>
{LON},{LAT},0 {LON + 0.001},{LAT},0 {LON + 0.001},{LAT + 0.001},0 {LON},{LAT + 0.001},0 {LON},{LAT},0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Folder>
<Folder><name>Infrastructure</name>
<Placemark><name>Access road</name><LineString><coordinates>
{LON},{LAT},900 {LON + 0.002},{LAT + 0.001},905
</coordinates></LineString></Placemark>
<Placemark><name>Tower</name><Point><coordinates>{LON},{LAT},900</coordinates></Point></Placemark>
<Placemark><name>Mixed (unsupported)</name><MultiGeometry>
<Point><coordinates>{LON},{LAT}</coordinates></Point>
<LineString><coordinates>{LON},{LAT} {LON + 0.0005},{LAT + 0.0005}</coordinates></LineString>
</MultiGeometry></Placemark>
</Folder>
</Document></kml>
"""


def zip_shapefile(gdf: gpd.GeoDataFrame, name: str, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        gdf.to_file(Path(tmp) / f"{name}.shp", driver="ESRI Shapefile")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(Path(tmp).iterdir()):
                z.write(f, f.name)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "survey.kml").write_text(KML, encoding="utf-8")

    plots = gpd.GeoDataFrame(
        {"name": ["Plot A", "Plot B"], "owner": ["Aereo", "Acme"]},
        geometry=[box(LON, LAT, LON + 0.001, LAT + 0.001), box(LON + 0.01, LAT, LON + 0.011, LAT + 0.001)],
        crs="EPSG:4326",
    )
    zip_shapefile(plots, "plots_wgs84", OUT / "plots_wgs84_shapefile.zip")
    zip_shapefile(plots.to_crs(32643), "plots_utm43n", OUT / "plots_utm43n_shapefile.zip")

    roads = gpd.GeoDataFrame(
        {"name": ["Access road"]},
        geometry=[LineString([(LON, LAT), (LON + 0.002, LAT + 0.001)])],
        crs="EPSG:4326",
    ).to_crs(3857)  # Web Mercator on purpose: exercises the "projected but distorting" rule
    zip_shapefile(roads, "roads_webmercator", OUT / "roads_webmercator_shapefile.zip")
    print("Wrote", *sorted(p.name for p in OUT.iterdir()), sep="\n  ")
