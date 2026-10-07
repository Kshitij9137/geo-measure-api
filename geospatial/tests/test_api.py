import uuid

import geopandas as gpd
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from shapely.geometry import LineString, box

from geospatial.models import Feature, UploadedFile

from .conftest import KML_LINE, KML_MULTIGEOMETRY, KML_POINT, KML_POLYGON

pytestmark = pytest.mark.django_db
UPLOAD_URL = "/api/files/"


@pytest.fixture
def client():
    return APIClient()


def upload(client, name, content, content_type="application/octet-stream"):
    return client.post(
        UPLOAD_URL, {"file": SimpleUploadedFile(name, content, content_type)}, format="multipart"
    )


# ---- POST /api/files/ -----------------------------------------------------------------------
def test_upload_valid_kml_returns_201_with_file_info(client, kml_bytes):
    response = upload(client, "survey.kml", kml_bytes(KML_POLYGON, KML_LINE, KML_POINT))
    assert response.status_code == 201
    body = response.json()
    uuid.UUID(body["id"])  # UUID, not a sequential integer
    assert body["filename"] == "survey.kml"
    assert body["file_type"] == "KML"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["measurement_crs"] == "EPSG:32643"
    assert body["status"] == "COMPLETED"
    assert body["error"] is None
    assert body["summary"]["geometry_summary"] == {"Polygon": 1, "LineString": 1, "Point": 1}


def test_upload_valid_shapefile_zip_returns_201(client, make_shapefile_zip, polygon_gdf):
    response = upload(client, "plots.zip", make_shapefile_zip(polygon_gdf))
    assert response.status_code == 201
    body = response.json()
    assert (body["file_type"], body["feature_count"], body["status"]) == ("SHAPEFILE", 2, "COMPLETED")
    assert Feature.objects.filter(uploaded_file_id=body["id"]).count() == 2


def test_upload_with_unsupported_geometry_completes_with_warnings(client, kml_bytes):
    response = upload(client, "mixed.kml", kml_bytes(KML_POLYGON, KML_MULTIGEOMETRY))
    assert response.status_code == 201
    assert response.json()["status"] == "COMPLETED_WITH_WARNINGS"
    assert response.json()["summary"]["measurement_summary"]["unsupported"] == 1


@pytest.mark.parametrize("name", ["data.txt", "data.geojson", "data.kmz", "noextension", "x.shp"])
def test_unsupported_extension_returns_400(client, name):
    response = upload(client, name, b"hello")
    assert response.status_code == 400
    assert response.json()["code"] == "UNSUPPORTED_FILE_TYPE"
    assert UploadedFile.objects.count() == 0  # rejected before anything is stored


def test_missing_file_field_returns_400(client):
    response = client.post(UPLOAD_URL, {}, format="multipart")
    assert response.status_code == 400
    assert response.json()["code"] == "FILE_MISSING"


def test_empty_file_returns_400(client):
    response = upload(client, "empty.kml", b"")
    assert response.status_code == 400
    assert response.json()["code"] == "UNREADABLE_FILE"


def test_fake_zip_with_zip_extension_returns_400(client):
    response = upload(client, "fake.zip", b"definitely not a zip")
    assert response.status_code == 400
    assert response.json()["code"] == "UNREADABLE_FILE"


def test_text_file_renamed_to_kml_returns_400(client):
    response = upload(client, "fake.kml", b"just some text, no xml")
    assert response.status_code == 400
    assert response.json()["code"] == "UNREADABLE_FILE"


def test_oversized_upload_returns_413(client, settings, kml_bytes):
    settings.MAX_UPLOAD_SIZE_BYTES = 100
    response = upload(client, "big.kml", kml_bytes(KML_POLYGON))
    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"


def test_zip_without_shp_returns_400_and_records_failure(client, tmp_path):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("readme.txt", "hello")
    response = upload(client, "nope.zip", buffer.getvalue())
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_SHAPEFILE_ARCHIVE"
    # The failed attempt is kept for auditing and is retrievable by id.
    detail = client.get(f"/api/files/{body['file_id']}/").json()
    assert detail["status"] == "FAILED"
    assert detail["error"]["code"] == "INVALID_SHAPEFILE_ARCHIVE"
    assert Feature.objects.count() == 0


def test_path_traversal_zip_returns_400(client):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("../../evil.shp", "x")
    response = upload(client, "evil.zip", buffer.getvalue())
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SHAPEFILE_ARCHIVE"


def test_shapefile_without_prj_returns_422_crs_missing(client, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)])
    response = upload(client, "noprj.zip", make_shapefile_zip(gdf))
    assert response.status_code == 422
    assert response.json()["code"] == "CRS_MISSING"
    assert UploadedFile.objects.get().status == "FAILED"


def test_empty_kml_returns_422(client, kml_bytes):
    response = upload(client, "empty.kml", kml_bytes())
    assert response.status_code == 422
    assert response.json()["code"] == "EMPTY_DATASET"


def test_failure_leaves_no_partial_features(client, kml_bytes, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(Feature.objects, "bulk_create", boom)
    response = upload(client, "s.kml", kml_bytes(KML_POLYGON))
    assert response.status_code == 500
    assert response.json()["code"] == "PROCESSING_FAILED"
    assert "exploded" not in response.json()["message"]  # internals are not leaked
    assert Feature.objects.count() == 0
    assert UploadedFile.objects.get().status == "FAILED"


# ---- GET /api/files/{id}/ -------------------------------------------------------------------
def test_get_file_information(client, kml_bytes):
    file_id = upload(client, "survey.kml", kml_bytes(KML_POLYGON)).json()["id"]
    response = client.get(f"/api/files/{file_id}/")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == file_id
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 1
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert body["created_at"] and body["processed_at"]


@pytest.mark.parametrize("path", [f"/api/files/{uuid.uuid4()}/", "/api/files/not-a-uuid/", "/api/files/123/"])
def test_unknown_or_malformed_id_returns_404(client, path):
    for suffix in ("", "measurements/"):
        response = client.get(path + suffix)
        assert response.status_code == 404
        assert response.json()["code"] == "FILE_NOT_FOUND"


# ---- GET /api/files/{id}/measurements/ ------------------------------------------------------
def test_measurements_response_shape_and_values(client, kml_bytes):
    file_id = upload(client, "s.kml", kml_bytes(KML_POLYGON, KML_LINE, KML_POINT)).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()

    assert body["file_id"] == file_id
    assert body["crs"] == "EPSG:4326"
    assert body["measurement_crs"] == "EPSG:32643"
    assert body["feature_count"] == 3 and body["count"] == 3
    polygon, line, point = body["results"]

    assert polygon["feature_id"] == 0 and polygon["geometry_type"] == "Polygon"
    assert polygon["crs"] == "EPSG:4326"
    assert polygon["geometry"]["type"] == "Polygon"
    assert polygon["properties"]["owner"] == "Aereo"
    assert polygon["measurement"]["type"] == "area" and polygon["measurement"]["unit"] == "m²"
    assert polygon["measurement"]["value"] == pytest.approx(12_000, rel=0.05)  # not ~1e-6 "degrees"
    assert polygon["measurement_status"] == "SUPPORTED" and polygon["error"] is None

    assert line["measurement"]["type"] == "length" and line["measurement"]["unit"] == "m"
    assert line["measurement"]["value"] == pytest.approx(108.5, rel=0.02)

    assert point["measurement"] is None
    assert point["measurement_status"] == "NOT_REQUIRED"


def test_projected_shapefile_measurements_are_exact_via_api(client, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 100, 100)], crs="EPSG:32643")
    file_id = upload(client, "p.zip", make_shapefile_zip(gdf)).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()
    assert body["crs"] == body["measurement_crs"] == "EPSG:32643"
    assert body["results"][0]["measurement"]["value"] == pytest.approx(10_000)


def test_unsupported_feature_reports_error_message(client, kml_bytes):
    file_id = upload(client, "m.kml", kml_bytes(KML_MULTIGEOMETRY)).json()["id"]
    result = client.get(f"/api/files/{file_id}/measurements/").json()["results"][0]
    assert result["measurement"] is None
    assert result["measurement_status"] == "UNSUPPORTED"
    assert "GeometryCollection" in result["error"]


@pytest.fixture
def twenty_lines_file(client, make_shapefile_zip):
    gdf = gpd.GeoDataFrame(
        {"n": range(20)},
        geometry=[LineString([(0, i), (10, i)]) for i in range(20)],
        crs="EPSG:32643",
    )
    return upload(client, "many.zip", make_shapefile_zip(gdf)).json()["id"]


def test_measurements_are_paginated(client, twenty_lines_file):
    first = client.get(f"/api/files/{twenty_lines_file}/measurements/?page_size=8").json()
    assert first["count"] == 20 and len(first["results"]) == 8
    assert first["next"] and first["previous"] is None
    assert [r["feature_id"] for r in first["results"]] == list(range(8))

    last = client.get(f"/api/files/{twenty_lines_file}/measurements/?page=3&page_size=8").json()
    assert [r["feature_id"] for r in last["results"]] == [16, 17, 18, 19]
    assert last["next"] is None


def test_page_size_is_capped(client, twenty_lines_file):
    body = client.get(f"/api/files/{twenty_lines_file}/measurements/?page_size=100000").json()
    assert len(body["results"]) == 20  # capped at max_page_size (500), only 20 exist


def test_out_of_range_page_returns_404_json(client, twenty_lines_file):
    response = client.get(f"/api/files/{twenty_lines_file}/measurements/?page=99")
    assert response.status_code == 404
    assert set(response.json()) == {"code", "message"}


def test_filter_measurements_by_status(client, kml_bytes):
    file_id = upload(client, "m.kml", kml_bytes(KML_POLYGON, KML_MULTIGEOMETRY, KML_POINT)).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/?status=unsupported").json()
    assert body["count"] == 1
    assert body["results"][0]["measurement_status"] == "UNSUPPORTED"


def test_invalid_status_filter_returns_400(client, kml_bytes):
    file_id = upload(client, "s.kml", kml_bytes(KML_POLYGON)).json()["id"]
    response = client.get(f"/api/files/{file_id}/measurements/?status=bogus")
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_QUERY_PARAMETER"


# ---- Misc -----------------------------------------------------------------------------------
def test_health_endpoint(client):
    response = client.get("/api/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}


def test_openapi_schema_documents_all_endpoints(client):
    response = client.get("/api/schema/?format=json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/files/" in paths
    assert "/api/files/{id}/" in paths
    assert "/api/files/{id}/measurements/" in paths
    assert client.get("/api/docs/").status_code == 200


def test_wrong_method_returns_json_error(client):
    response = client.delete(UPLOAD_URL)
    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
