# GeoMeasure API

A backend service that accepts a geospatial file (**KML** or a **zipped Shapefile**), extracts every
feature, and returns per-feature geometry, CRS, attributes and **area / length measurements calculated
in a proper projected coordinate system**.

![tests](https://github.com/<your-username>/geo-measure-api/actions/workflows/tests.yml/badge.svg)

- **111 tests, 98% coverage**, run against both SQLite and PostgreSQL 16
- Measurements cross-checked against independent **geodesic (ellipsoidal)** calculations
- Hardened ZIP handling, graceful per-feature error handling, consistent error format, pagination, OpenAPI docs

## Contents
[Quick start](#quick-start) · [API](#api) · [Architecture](#architecture) · [CRS handling](#crs-handling) ·
[Design decisions](#design-decisions-and-alternatives-considered) · [Error handling](#error-handling) ·
[Testing](#testing) · [Known limitations](#known-limitations) · [Future scope](#future-scope) · [What I learned](#what-i-learned)

## Tech stack

Python 3.12 · Django · Django REST Framework · GeoPandas · Shapely 2 · PyProj · pyogrio (GDAL) ·
PostgreSQL · Docker · pytest · GitHub Actions · drf-spectacular (OpenAPI/Swagger)

---

## Quick start

### Option A: Docker (PostgreSQL included)

```bash
git clone https://github.com/<your-username>/geo-measure-api.git
cd geo-measure-api
docker compose up --build
```

API on <http://localhost:8000>, interactive docs on <http://localhost:8000/api/docs/>.

### Option B: Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py runserver
```

Without `DATABASE_URL` the app uses a local SQLite file, so no database setup is needed. Settings are read from
**environment variables** (see [Configuration](#configuration)); a `.env` file is *not* loaded automatically by
Django. Docker Compose does read a `.env` file next to `docker-compose.yml` for the `DJANGO_*` variables.

No system GDAL install is needed: the GeoPandas/pyogrio/Shapely/PyProj wheels bundle GDAL, GEOS and PROJ.
To use PostgreSQL locally, set `DATABASE_URL=postgres://user:pass@localhost:5432/dbname`.

### Try it

`sample_data/` contains ready-made files (regenerate with `python scripts/make_sample_data.py`):

| File | Purpose |
|---|---|
| `survey.kml` | Two folders, polygon + line + point + an unsupported `MultiGeometry`, Z coordinates |
| `plots_wgs84_shapefile.zip` | Polygons in EPSG:4326 (must be reprojected) |
| `plots_utm43n_shapefile.zip` | Same polygons already in EPSG:32643 (measured in place) |
| `roads_webmercator_shapefile.zip` | Line in EPSG:3857, "projected" but area/length-distorting |

```bash
curl -F "file=@sample_data/survey.kml" http://localhost:8000/api/files/
curl http://localhost:8000/api/files/<id>/
curl http://localhost:8000/api/files/<id>/measurements/
```

### Run the tests

```bash
pytest                                   # 111 tests
pytest --cov=geospatial --cov-report=term-missing
ruff check .
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | insecure dev key | **Set this in any real deployment** |
| `DJANGO_DEBUG` | `False` | |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | comma separated |
| `DATABASE_URL` | SQLite file | e.g. `postgres://user:pass@host:5432/db` |
| `MAX_UPLOAD_SIZE_MB` | `50` | upload size limit |
| `ZIP_MAX_FILES` / `ZIP_MAX_UNCOMPRESSED_MB` | `200` / `500` | zip-bomb protection |
| `MEDIA_ROOT` | `./media` | where uploads are stored |

---

## API

Interactive documentation: **`/api/docs/`** (Swagger UI) · schema: `/api/schema/`.
All errors share one shape, see [Error handling](#error-handling).

### `POST /api/files/`: upload and process

`multipart/form-data` with a `file` field: a `.kml`, or a `.zip` containing exactly one Shapefile.
Processing is synchronous; the response already contains the final status.

```bash
curl -F "file=@sample_data/survey.kml" http://localhost:8000/api/files/
```

`201 Created`

```json
{
  "id": "4a8d7130-d5a9-45c0-b072-7be3a895bec5",
  "filename": "survey.kml",
  "file_type": "KML",
  "status": "COMPLETED_WITH_WARNINGS",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "measurement_crs": "EPSG:32643",
  "summary": {
    "geometry_summary": {"Polygon": 1, "LineString": 1, "Point": 1, "GeometryCollection": 1},
    "measurement_summary": {"measured": 2, "not_required": 1, "unsupported": 1, "invalid": 0},
    "warnings": []
  },
  "error": null,
  "created_at": "2026-10-06T18:33:27.527950Z",
  "processed_at": "2026-10-06T18:33:27.664432Z"
}
```

`status` is one of `PROCESSING`, `COMPLETED`, `COMPLETED_WITH_WARNINGS` (some features were unsupported or
invalid, or the dataset spans several UTM zones) or `FAILED`.

### `GET /api/files/{id}/`: file information

Returns the same object as above (`crs` = CRS of the uploaded data, `measurement_crs` = CRS used for
calculation). `404 FILE_NOT_FOUND` for an unknown or malformed id.

### `GET /api/files/{id}/measurements/`: per-feature results

Query parameters: `page`, `page_size` (default 50, max 500), `status` (filter by
`SUPPORTED | NOT_REQUIRED | UNSUPPORTED | INVALID`).

```json
{
  "file_id": "4a8d7130-d5a9-45c0-b072-7be3a895bec5",
  "crs": "EPSG:4326",
  "measurement_crs": "EPSG:32643",
  "feature_count": 4,
  "summary": {"...": "..."},
  "count": 4,
  "next": null,
  "previous": null,
  "results": [
    {
      "feature_id": 0,
      "layer": "Plots",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "geometry": {"type": "Polygon", "coordinates": [[[77.5946, 12.9716, 0.0], "..."]]},
      "properties": {"Name": "Plot A", "owner": "Aereo"},
      "measurement": {"type": "area", "value": 12016.978, "unit": "m²"},
      "measurement_status": "SUPPORTED",
      "error": null
    },
    {
      "feature_id": 1, "layer": "Infrastructure", "geometry_type": "LineString", "crs": "EPSG:4326",
      "measurement": {"type": "length", "value": 243.709, "unit": "m"}, "measurement_status": "SUPPORTED", "...": "..."
    },
    {
      "feature_id": 2, "layer": "Infrastructure", "geometry_type": "Point", "crs": "EPSG:4326",
      "measurement": null, "measurement_status": "NOT_REQUIRED", "error": null, "...": "..."
    },
    {
      "feature_id": 3, "layer": "Infrastructure", "geometry_type": "GeometryCollection", "crs": "EPSG:4326",
      "measurement": null, "measurement_status": "UNSUPPORTED",
      "error": "Measurement is not supported for GeometryCollection geometries.", "...": "..."
    }
  ]
}
```

Geometry is returned as GeoJSON in the file's **original** CRS. Only the measurement uses the projected CRS.
`crs` is repeated on each feature because the assignment asks for it per feature; internally it is a
file-level property (GDAL/GeoPandas hold one CRS per layer), so it is not stored redundantly per row.

### `GET /api/health/`

`{"status": "ok", "database": "up"}` (`503` if the database is unreachable).

---

## Architecture

```
Client
  │  multipart upload
  ▼
DRF ViewSet (geospatial/api)         thin: validate → persist → delegate → serialise
  │
  ▼
validators.py                        extension, size, content sniffing (is it really a ZIP / KML?)
  │
  ▼
processor.process_file()             persistence + status; the ONLY place that touches the DB
  │        (HTTP-agnostic: a Celery task could call it unchanged)
  ▼
analysis.analyze_file()              pure geospatial pipeline, no Django models
  │
  ├─► readers.py ── KMLReader (all layers, WGS84)
  │              └─ ShapefileReader ── archive.py (hardened extraction, component checks)
  ▼
GeoDataFrame
  │
  ├─► crs.py            choose measurement CRS (metre-based projected, else UTM)
  ├─► measurements.py   vectorised area / length, status per feature
  └─► properties.py     JSON-safe attributes
  ▼
PostgreSQL (UploadedFile 1──n Feature)
```

```
config/                 Django project (settings read from environment variables)
geospatial/
  api/                  views, serializers, URLs, exception handler    (HTTP layer)
  services/             readers, archive, crs, measurements, analysis,
                        processor, validators, properties              (domain logic)
  models.py             UploadedFile, Feature
  exceptions.py         one exception hierarchy, each with a stable error code
  tests/                111 tests; fixtures generated programmatically
sample_data/  scripts/  Dockerfile  docker-compose.yml  .github/workflows/tests.yml
```

### File-processing flow

1. **Validate**: extension, non-empty, size limit, content sniff (a `.zip` must be a ZIP; a `.kml` must look like KML).
2. **Store** an `UploadedFile` (UUID id) and copy it to a temp directory (via the storage API, so S3 would work).
3. **Read**: KML → every layer/folder; ZIP → safe extraction, require exactly one `.shp` with `.shx` and `.dbf`, read with pyogrio.
4. **Select the measurement CRS** and reproject (below).
5. **Measure** all geometries in one vectorised pass; classify each feature.
6. **Persist** all features with `bulk_create` inside one transaction, then set the file status.
   If anything fails, the transaction rolls back (no partial features), the file is marked `FAILED`, and the
   client gets a controlled error that includes the `file_id`.

### Measurement flow

| Geometry | Result | Status |
|---|---|---|
| Polygon, MultiPolygon | area (m²) | `SUPPORTED` |
| LineString, MultiLineString, LinearRing | length (m) | `SUPPORTED` |
| Point, MultiPoint | none | `NOT_REQUIRED` |
| GeometryCollection (e.g. KML `MultiGeometry`) | none | `UNSUPPORTED` |
| Null / empty / self-intersecting / non-finite result | none | `INVALID` (with reason) |

A bad feature never fails the whole file; the file becomes `COMPLETED_WITH_WARNINGS`.
Z coordinates are ignored: calculations are planar and 2D.

---

## CRS handling

Latitude/longitude are angles, not distances. `polygon.area` on EPSG:4326 data returns "square degrees",
not square metres (a 0.01° box here evaluates to `0.0001` when the real answer is about 1.2 million m²).
The dataset is therefore transformed to a projected, metre-based CRS **before** measuring.

| Input CRS | Action | Why |
|---|---|---|
| None (Shapefile without `.prj`) | Reject: `422 CRS_MISSING` | Guessing would silently produce wrong numbers |
| KML | Treated as EPSG:4326 | The KML specification mandates WGS84; there is no CRS to read |
| Geographic (e.g. EPSG:4326) | Reproject to the UTM zone estimated from the data (`estimate_utm_crs`) | Small distortion, metre units |
| Projected, **metre-based**, not Mercator-family (e.g. UTM, national grids) | Measure in place, no reprojection | Already suitable; avoids pointless round-trips |
| Web Mercator (EPSG:3857) or other Mercator variants | Reproject to UTM | "Projected" but inflates area by 1/cos²(lat), about 4x at 60°N |
| Feet-based projected CRS (e.g. US State Plane) | Reproject to UTM | Otherwise "m²" would be wrong by a factor of about 10.76 |

Both CRSs are stored and returned (`crs` and `measurement_crs`). Units are always metres because every path
ends in a metre-based CRS.

**Verified, not assumed.** Tests compare the UTM results with `pyproj.Geod` geodesic area/length on the WGS84
ellipsoid (within 0.5%), and with exact known geometries (100 m square = 10,000 m², a 3-4-5 line = 5 m).
On the sample data the same polygon measures identically whether uploaded in WGS84 or pre-projected to UTM,
and the Web Mercator road measures 243.7 m (its raw Web Mercator length would be 250.2 m).

**Limitations of this strategy** (also in [Known limitations](#known-limitations)): UTM is not equal-area and
distorts increasingly across its 6° zone; a dataset spanning more than one zone gets a warning and a
`COMPLETED_WITH_WARNINGS` status instead of silently inaccurate numbers.

---

## Design decisions and alternatives considered

| Decision | Alternatives considered | Why this choice |
|---|---|---|
| **Django + DRF** | FastAPI | I know Django well, and its ORM, admin and migrations cover persistence cheaply. FastAPI would be a good fit for pure async APIs; the geospatial core is framework-independent either way. |
| **GeoPandas / Shapely / PyProj** | Raw Fiona/pyogrio + Shapely; GDAL/OGR bindings | GeoPandas gives format reading, CRS handling and vectorised geometry in one tested stack. pyogrio (GDAL) is its reader, so format support is the same. |
| **Geometry stored as JSON (GeoJSON), not PostGIS** | GeoDjango + PostGIS | Nothing here needs spatial *queries*: processing happens in Python. Skipping PostGIS keeps setup simple (no GDAL/system libs in Django). It would be the right move once spatial querying is needed. |
| **UTM (estimated per dataset) for measurement** | One equal-area CRS (e.g. EASE-Grid, Albers); pure geodesic calculation | UTM is accurate for the typical survey-sized site, familiar to GIS users, and cheap. Geodesic maths is the most accurate and I use it as the independent test oracle; equal-area projections need a regional choice. |
| **Reject Shapefiles without `.prj`** | Assume EPSG:4326 | A wrong assumption yields plausible but wrong areas, the worst failure mode for a measurement tool. |
| **One Shapefile per ZIP** | Merge all, or process each | Different shapefiles can have different CRSs, so merging is ambiguous. A clear error is simpler and honest. |
| **Synchronous processing** | Celery + Redis | The assignment describes upload *and* process in one call. `process_file` is HTTP-agnostic, so moving to a queue means changing one call site. |
| **Pure `analyze_file` + thin `process_file`** | All logic in views / models | Analysis is unit-testable without a database or HTTP. |
| **Dict registry of reader classes** | Factory class, `if/elif` on extension | A small ABC plus a dict shows polymorphism without ceremony; adding a format is one class and one entry. |
| **Vectorised measurement** (`shapely.area/length` on arrays) | `iterrows()` loop | Order of magnitude faster on large files. |
| **UUID primary keys** | Integer ids | Not enumerable. (Not authorisation, see limitations.) |
| **Failed uploads kept as `FAILED` records** | Delete on failure | Auditable and retrievable by the `file_id` returned in the error. |
| **Fixtures generated in code** | Committed binary files | Reviewable, intention-revealing, no opaque blobs. |

---

## Error handling

Every error, including DRF's own (404, 405, ...) and unexpected 500s, uses one shape:

```json
{"code": "CRS_MISSING", "message": "The Shapefile has no CRS definition (missing or empty .prj file); ..."}
```

Failures that happen *after* a record was created (processing errors) also include `"file_id"`.

| HTTP | `code` | When |
|---|---|---|
| 400 | `FILE_MISSING` | no `file` field |
| 400 | `UNSUPPORTED_FILE_TYPE` | not `.kml` / `.zip` |
| 400 | `UNREADABLE_FILE` | empty file, fake ZIP/KML, parse failure |
| 400 | `INVALID_SHAPEFILE_ARCHIVE` | no `.shp`, missing `.shx`/`.dbf`, several shapefiles, path traversal, symlink, corrupt/encrypted ZIP |
| 400 | `ARCHIVE_LIMITS_EXCEEDED` | too many files / too large when extracted |
| 400 | `INVALID_QUERY_PARAMETER` | bad `status` filter |
| 404 | `FILE_NOT_FOUND` | unknown or malformed id |
| 413 | `FILE_TOO_LARGE` | exceeds `MAX_UPLOAD_SIZE_MB` |
| 422 | `CRS_MISSING` / `CRS_ERROR` | no CRS / no usable measurement CRS |
| 422 | `EMPTY_DATASET` | readable file with zero features |
| 500 | `PROCESSING_FAILED` | unexpected failure (internals are logged, not leaked) |

### Security and robustness

- ZIP extraction rejects path traversal (`../`, absolute, drive-letter, backslash tricks) and symlinks, caps file count and total
  **uncompressed** size (enforced while streaming, because ZIP headers can lie), and skips `__MACOSX`/`._*` junk.
- Extraction happens in a `TemporaryDirectory` that is always cleaned up; stored uploads are renamed to `<uuid><ext>`
  so user-supplied filenames never touch the filesystem.
- Content sniffing so a renamed `.txt` is not accepted as `.kml` / `.zip`.
- Attribute values are sanitised (NaN, NaT, numpy scalars, timestamps, bytes), so responses are always valid JSON.
- Logging via the `logging` module with file id, detected CRS, chosen CRS, feature counts and timings.

---

## Testing

111 tests, 98% line coverage. Structure:

| Module | Covers |
|---|---|
| `test_measurements.py` | exact areas/lengths, holes, Multi*, points, GeometryCollection, bow-tie, null/empty, non-finite, vectorised ordering |
| `test_crs.py` | CRS selection rules (geographic, projected, 3857, feet, missing), multi-zone warning, **geodesic cross-checks**, "naive degrees would be wrong" |
| `test_archive.py` | path traversal, symlinks, zip bomb, file-count cap, corrupt ZIP, junk files, missing components, multiple shapefiles |
| `test_readers_and_processing.py` | KML multi-folder/Z/MultiGeometry/malformed/empty; Shapefile per geometry type, projected vs geographic, no `.prj`, null geometry, invalid polygon, feet CRS, property sanitising |
| `test_api.py` | all three endpoints, every error code, pagination, filtering, rollback on failure, OpenAPI schema, health |

The suite passes on SQLite and on PostgreSQL 16. CI (`.github/workflows/tests.yml`) runs lint, a
missing-migrations check and the tests against a PostgreSQL service container.

---

## Known limitations

- **UTM is not equal-area.** Accuracy degrades toward the zone edges and for datasets spanning multiple zones (warned, not corrected).
- **Polar datasets** (beyond UTM's ±84° range) are rejected with `CRS_ERROR`.
- **KMZ, GeoJSON, GeoPackage** are not supported. Only KML and zipped Shapefiles. Only one Shapefile per ZIP.
- **No authentication / authorisation.** UUIDs are unguessable but anyone with an id can read that file's results.
- **Synchronous processing** blocks the request for very large files, and the upload is read fully by Django before validation (put a proxy size limit in front in production).
- **Measurements are planar and 2D**: Z (elevation) is ignored, so a sloped surface's true surface area/length is not computed.
- Geometry is stored as JSON; there are no spatial indexes or spatial queries.
- The Dockerfile and `docker-compose.yml` were written carefully but **not executed in the environment where this was developed** (no Docker available). The app itself was run and tested directly against PostgreSQL 16.

## Future scope

- **Async processing**: move `process_file` into a Celery task (Redis broker); the API returns `PROCESSING` and clients poll `GET /api/files/{id}/`. The service is already HTTP-agnostic, so this is a single call-site change.
- **Authentication and ownership**: token/JWT auth, files scoped to their owner, per-user quotas and rate limiting.
- **PostGIS / GeoDjango** for spatial indexes and queries (intersections, nearest-feature, bounding-box filters) and vector tiles.
- **Cloud storage**: S3 via `django-storages` (the processor already uses the storage API, not file paths).
- **More formats**: KMZ, GeoJSON, GeoPackage, plus multi-shapefile archives with per-layer CRS.
- **Geodesic mode** (`?method=geodesic`) as an alternative for large or multi-zone datasets.
- **Streaming/chunked reading** for very large files, and retention/cleanup of stored uploads.
- Metrics, structured JSON logs and tracing for production observability.

## What I learned

*(Replace/extend this with your own words before submitting; the points below are what building this taught me.)*

- "Projected" does not mean "safe to measure in": Web Mercator and feet-based CRSs both give wrong numbers unless handled.
- KML has no CRS (always WGS84), and GDAL exposes each KML folder as a layer, so reading only the first layer silently drops features.
- A Shapefile can hold only one geometry type, so mixed-geometry test data has to come from KML.
- Independent verification beats trusting the library: comparing UTM results with geodesic calculations proved the approach.
- Keeping geospatial logic free of Django (`analyze_file`) made it easy to test thoroughly and easy to evolve.
- ZIP files are untrusted input: path traversal, symlinks and decompression bombs are real concerns.
