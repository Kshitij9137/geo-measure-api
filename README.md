# GeoMeasure API



A backend service that accepts a geospatial file (KML or zipped Shapefile), extracts every
feature, and returns per-feature measurements — **area in square metres** for polygons and
**length in metres** for lines — computed in an appropriate *projected* coordinate reference
system.

Built as the practical assignment for the **Software Development Intern** role at
[Aereo](https://www.linkedin.com/company/aereo-next-is-now/). The interesting part of the
assignment isn't the CRUD — it's getting the geospatial maths right, and doing so without
letting file processing leak into the HTTP layer.

---

## Table of contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Try it](#try-it)
- [Architecture](#architecture)
- [The CRS problem](#the-crs-problem)
- [API reference](#api-reference)
- [Testing](#testing)
- [Design decisions](#design-decisions)
- [Trade-offs and what I deliberately didn't build](#trade-offs-and-what-i-deliberately-didnt-build)
- [What I learned](#what-i-learned)
- [Future scope](#future-scope)
- [Known limitations](#known-limitations)

---

## What it does

| Step | What happens |
|---|---|
| 1 | You `POST` a `.kml` file or a `.zip` containing a Shapefile |
| 2 | The file is validated (extension, size, and a light content sniff) |
| 3 | If it's a `.zip`, it's extracted **safely** (path traversal, symlinks, zip bombs blocked) |
| 4 | GeoPandas (via `pyogrio`) reads the file into a `GeoDataFrame` |
| 5 | The dataset's CRS is inspected and a **measurement CRS** is chosen |
| 6 | Geometries are reprojected once, in bulk, into that measurement CRS |
| 7 | Each feature is measured: `Polygon` → area, `LineString` → length, `Point` → nothing |
| 8 | Per-feature results are persisted and served through three REST endpoints |

If any single feature can't be measured (self-intersecting polygon, `GeometryCollection`,
missing geometry), the file **still completes** — its status just becomes
`COMPLETED_WITH_WARNINGS` and the bad features carry a reason.

---

## Quick start

### Option A — Docker (recommended, no local deps)

```bash
git clone https://github.com/Kshitij9137/geo-measure-api.git
cd geo-measure-api
cp .env.example .env
docker compose up --build
```

Then open <http://localhost:8000/api/docs/>.

### Option B — Local, with PostgreSQL

Requires **Python 3.11+** and, on Windows/macOS, the GDAL system libraries
(GeoPandas depends on them):

```bash
# Ubuntu / Debian
sudo apt-get install -y gdal-bin libgdal-dev libgeos-dev libproj-dev

# macOS
brew install gdal
```

Then:

```bash
git clone https://github.com/Kshitij9137/geo-measure-api.git
cd geo-measure-api

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # edit if your DB creds differ

python manage.py migrate
python manage.py runserver
```

### Option C — Local, with SQLite (fastest, no database server)

```bash
echo "DATABASE_URL=sqlite:///db.sqlite3" > .env
python manage.py migrate
python manage.py runserver
```

---

## Try it

The repo ships with sample files in `sample_data/` that exercise different code paths:

| File | What it exercises |
|---|---|
| `survey.kml` | WGS84 KML with folders, polygons, a line, a point, and an unsupported `MultiGeometry` |
| `plots_wgs84_shapefile.zip` | Geographic Shapefile that must be reprojected to UTM |
| `plots_utm43n_shapefile.zip` | Already-projected Shapefile that must be measured in place |
| `roads_webmercator_shapefile.zip` | Web Mercator — projected, but must *still* be reprojected |

Upload one:

```bash
curl -F "file=@sample_data/survey.kml" http://localhost:8000/api/files/
```

Inspect the file:

```bash
curl http://localhost:8000/api/files/<id>/
```

Get the per-feature measurements (paginated):

```bash
curl "http://localhost:8000/api/files/<id>/measurements/?page=1&page_size=20"
```

Filter by a specific outcome:

```bash
curl "http://localhost:8000/api/files/<id>/measurements/?status=unsupported"
```

Interactive docs: **<http://localhost:8000/api/docs/>**.

---

## Architecture

```
                        ┌───────────────────────────┐
                        │       HTTP client         │
                        └─────────────┬─────────────┘
                                      │  multipart/form-data
                                      ▼
                        ┌───────────────────────────┐
                        │  geospatial.api.views     │   DRF layer
                        │  - UploadedFileViewSet    │   (no geospatial logic here)
                        │  - exception_handler      │
                        └─────────────┬─────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │  services.processor       │   application service
                        │  process_file(uploaded)   │   (transactional persistence)
                        └─────────────┬─────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │  services.analysis        │   pure domain logic
                        │  analyze_file(path, type) │   (no Django, no HTTP)
                        └─────────────┬─────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
    ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
    │ services.readers │   │  services.crs    │   │ services.measure │
    │ KMLReader        │   │  select_measure  │   │ measure_geometries│
    │ ShapefileReader  │   │  _measurement_crs│   │ (vectorised)      │
    └────────┬─────────┘   └────────┬─────────┘   └────────┬──────────┘
             │                      │                      │
             ▼                      ▼                      ▼
     pyogrio / GDAL           pyproj / GeoPandas        Shapely 2
             │                      │                      │
             └──────────────────────┴──────────────────────┘
                                    │
                                    ▼
                        ┌───────────────────────────┐
                        │  UploadedFile + Feature   │   PostgreSQL
                        └───────────────────────────┘
```

**Why this shape:** file processing is domain logic. Everything below
`services/analysis.py` is a pure function of a file path — no ORM, no HTTP, no Django.
That means it can be unit-tested without a database, and — if the workload grows —
lifted into a Celery task without touching a single view.

### Project layout

```
geo-measure-api/
├── config/                          # Django project (settings, urls, wsgi)
├── geospatial/
│   ├── api/                         # HTTP concerns only
│   │   ├── exception_handler.py     # one error envelope for every failure
│   │   ├── serializers.py
│   │   ├── urls.py
│   │   └── views.py
│   ├── services/                    # domain logic (Django-free below analysis.py)
│   │   ├── analysis.py              # the one function a task queue would call
│   │   ├── archive.py               # hardened ZIP extraction + Shapefile discovery
│   │   ├── crs.py                   # measurement-CRS selection
│   │   ├── measurements.py          # vectorised Shapely 2 measurement engine
│   │   ├── processor.py             # bridges analysis → database
│   │   ├── properties.py            # JSON-safe property sanitising
│   │   ├── readers.py               # KMLReader, ShapefileReader + registry
│   │   ├── types.py                 # shared enums (no Django import)
│   │   └── validators.py
│   ├── tests/                       # 89 tests, split by concern
│   ├── exceptions.py                # domain exception hierarchy
│   └── models.py                    # UploadedFile, Feature
├── sample_data/                     # ready-to-upload fixtures for reviewers
├── scripts/make_sample_data.py      # regenerates sample_data/
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## The CRS problem

The assignment explicitly warns: **do not compute area or length from latitude/longitude
degrees.** A square degree near the equator covers ~12,300 km²; the same "square degree"
near the pole covers almost nothing. The unit is angular, not linear, and any measurement
computed in it is meaningless.

The obvious fix is "reproject to UTM". The *correct* fix accounts for three more cases:

### Rule 1 — No CRS at all

A Shapefile without a `.prj` file, or a KML whose coordinates have been stripped of context,
gives us no way to know where on Earth the features are. We **refuse to guess** and return:

```json
{ "code": "CRS_MISSING", "message": "The dataset does not contain CRS information..." }
```

Guessing (e.g. assuming WGS84) is exactly the kind of decision that produces silently wrong
numbers in production.

### Rule 2 — Already projected *and* metre-based *and* not Mercator

If the file ships in UTM, a national grid (e.g. British National Grid), or any other
metre-based projected CRS, we **measure in place**. No reprojection, no wasted work, no
introduced error.

### Rule 3 — Projected *but* distorting, or in feet, or geographic

This is the case most implementations get wrong. **EPSG:3857 (Web Mercator) is a projected
CRS.** A naive implementation would measure in it directly. But Web Mercator inflates areas
by a factor of `1 / cos²(latitude)` — about **2.8× at 53°N, and 4× at 60°N**. A tool that
reports "m²" and quietly multiplies by 2.8 is worse than one that refuses.

Similarly, a Shapefile shipped in **US survey feet** is projected and self-consistent, but
labelling its output "m²" is wrong by a factor of ~10.76.

So: geographic, Web Mercator, World Mercator, *any* CRS whose axes aren't metres, gets
reprojected. The target is `estimate_utm_crs()` — pyproj/GDAL's own estimator, which picks
the UTM zone that best fits the dataset's bounds.

If the dataset spans more than one UTM zone (≥6° of longitude), we do it anyway but attach a
warning to the file's `summary`:

```json
"warnings": ["Dataset spans 12.4° of longitude (more than one UTM zone); measurements in EPSG:32643 may be noticeably distorted."]
```

If `estimate_utm_crs()` itself fails (polar data, antimeridian), we return a controlled
`CRSError` rather than emitting garbage.

### We prove we got it right

Two tests in `tests/test_crs.py` don't just check the CRS label — they check the **numbers**:

```python
def test_area_in_utm_matches_geodesic_area_on_the_ellipsoid():
    polygon = box(BLR_LON, BLR_LAT, BLR_LON + 0.01, BLR_LAT + 0.01)
    projected_area = measure_geometry(_projected_geometry(polygon)).value
    geodesic_area  = abs(GEOD.geometry_area_perimeter(polygon)[0])
    assert projected_area == pytest.approx(geodesic_area, rel=0.005)
```

That is: the UTM area we compute agrees with the **geodesic area on the WGS84 ellipsoid**
computed by `pyproj.Geod`, to within half a percent. If we were accidentally measuring in
degrees, this test would fail by ~11 orders of magnitude.

The companion test proves the opposite:

```python
def test_naive_degree_calculation_would_be_wildly_wrong():
    polygon = box(BLR_LON, BLR_LAT, BLR_LON + 0.01, BLR_LAT + 0.01)
    assert polygon.area == pytest.approx(0.0001)              # "square degrees" — meaningless
    assert measure_geometry(_projected_geometry(polygon)).value > 1_000_000  # ~1.2 km², correct
```

---

## API reference

Full interactive docs at **`/api/docs/`**. Raw schema at **`/api/schema/`**.

### `POST /api/files/`

Upload and process a file. `multipart/form-data`, field name `file`.

```bash
curl -F "file=@survey.kml" http://localhost:8000/api/files/
```

**201 Created**

```json
{
  "id": "3f1c9e18-2b0a-4c62-8f24-2c1b0a2c9e18",
  "filename": "survey.kml",
  "file_type": "KML",
  "status": "COMPLETED_WITH_WARNINGS",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "measurement_crs": "EPSG:32643",
  "summary": {
    "geometry_summary": {
      "Polygon": 1,
      "LineString": 1,
      "Point": 1,
      "GeometryCollection": 1
    },
    "measurement_summary": {
      "measured": 2,
      "not_required": 1,
      "unsupported": 1,
      "invalid": 0
    },
    "warnings": []
  },
  "error": null,
  "created_at": "2026-10-06T13:24:11.201Z",
  "processed_at": "2026-10-06T13:24:11.418Z"
}
```

### `GET /api/files/{id}/`

Return the same shape as the upload response.

### `GET /api/files/{id}/measurements/`

Paginated feature list. Optional `?status=` filter (`SUPPORTED`, `NOT_REQUIRED`,
`UNSUPPORTED`, `INVALID`) and `?page=`, `?page_size=` (max 500).

```json
{
  "file_id": "3f1c9e18-...",
  "crs": "EPSG:4326",
  "measurement_crs": "EPSG:32643",
  "feature_count": 4,
  "summary": { "...same as above..." },
  "count": 4,
  "next": null,
  "previous": null,
  "results": [
    {
      "feature_id": 0,
      "layer": "Plots",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "geometry": { "type": "Polygon", "coordinates": [[[77.5946, 12.9716], ...]] },
      "properties": { "Name": "Plot A", "owner": "Aereo" },
      "measurement": { "type": "area", "value": 12187.34, "unit": "m²" },
      "measurement_status": "SUPPORTED",
      "error": null
    },
    {
      "feature_id": 1,
      "geometry_type": "LineString",
      "measurement": { "type": "length", "value": 222.51, "unit": "m" },
      "measurement_status": "SUPPORTED"
    },
    {
      "feature_id": 2,
      "geometry_type": "Point",
      "measurement": null,
      "measurement_status": "NOT_REQUIRED"
    },
    {
      "feature_id": 3,
      "geometry_type": "GeometryCollection",
      "measurement": null,
      "measurement_status": "UNSUPPORTED",
      "error": "Measurement is not supported for GeometryCollection geometries."
    }
  ]
}
```

### Error envelope

Every error — validation, domain, DRF internal — returns the same shape:

```json
{ "code": "UNSUPPORTED_FILE_TYPE", "message": "Only .kml and .zip (containing a Shapefile) files are supported." }
```

| Code | HTTP | When |
|---|---|---|
| `FILE_MISSING` | 400 | `file` field absent |
| `UNSUPPORTED_FILE_TYPE` | 400 | Not `.kml` / `.zip` |
| `UNREADABLE_FILE` | 400 | Empty, wrong magic bytes, unparseable |
| `FILE_TOO_LARGE` | 413 | Over `MAX_UPLOAD_SIZE_MB` |
| `INVALID_SHAPEFILE_ARCHIVE` | 400 | Missing `.shp`/`.shx`/`.dbf`, multiple `.shp`, path traversal, symlink |
| `ARCHIVE_LIMITS_EXCEEDED` | 400 | Too many files or uncompressed bytes (zip bomb) |
| `CRS_MISSING` | 422 | Shapefile without `.prj`, KML with no coordinates |
| `CRS_ERROR` | 422 | UTM estimation failed (polar / antimeridian) |
| `EMPTY_DATASET` | 422 | File read OK but zero features |
| `FILE_NOT_FOUND` | 404 | Unknown file id |
| `INVALID_QUERY_PARAMETER` | 400 | Bad `?status=` value |
| `PROCESSING_FAILED` | 500 | Unexpected internal error |

---

## Testing

```bash
pytest -q
# 90 tests, ~15 seconds on SQLite
```

Test layout mirrors the code:

| File | Covers |
|---|---|
| `test_api.py` | HTTP contract, status codes, error envelopes, pagination, OpenAPI schema |
| `test_archive.py` | Path traversal, symlinks, zip bombs, missing components, `__MACOSX` junk |
| `test_crs.py` | Reprojection rules, Web Mercator, feet, multi-zone warning, **geodesic accuracy** |
| `test_measurements.py` | Shapely 2 vectorised engine, every geometry type, invalid/empty/missing |
| `test_readers_and_processing.py` | End-to-end for both formats, property sanitisation, null geometries |

Geospatial fixtures are generated programmatically (`conftest.py`) so the tests don't depend
on committed binary blobs and run on any platform.

**The two accuracy tests I'm proudest of** (in `test_crs.py`) prove that measurements agree
with `pyproj.Geod`'s geodesic calculations to within 0.5% on the WGS84 ellipsoid, and that a
naive in-degrees calculation would be off by ~11 orders of magnitude. That's the kind of
check that distinguishes "it returns a number" from "it returns the *right* number".

---

## Design decisions

### Django + DRF, not FastAPI

Both are valid. Django gives me the ORM, migrations, an admin for free, and DRF's
serializers/parsers are well-suited to the multipart upload. FastAPI's advantages
(async-first, Pydantic-native) are neutralised here because the geospatial work is
CPU-bound and synchronous regardless. The Django admin was also useful during development —
being able to browse `UploadedFile` rows and their features in a UI saved me from writing a
lot of throwaway scripts.

### Layered services, not fat views

The single most important structural decision. Views do four things: parse the request,
call `validate_upload`, create the `UploadedFile` row, call `process_file`, serialise. They
never touch GeoPandas. `services/analysis.py` is pure Python — it takes a path and returns
dataclasses, and has no Django import. Consequences:

- Unit-testable without a database.
- Trivially liftable into a Celery task (`process_file(uploaded.id)`).
- Swappable measurement engine — you could replace Shapely with something else and only
  `measurements.py` changes.

### Sync processing

The assignment frames the upload as "upload and process". Making it async means adding
Redis + Celery and a second polling endpoint, for a service whose largest legitimate input
is ~50 MB and takes ~2 seconds. That complexity would be **premature**. The `analysis.py` /
`processor.py` split means it's a one-day change later.

### Store JSON geometries, not PostGIS

`JSONField` holds GeoJSON fine. PostGIS would give me spatial indexing, `ST_Area` push-down,
and bbox queries — none of which this assignment needs. PostGIS also requires the reviewer
to install it, which lowers the chance they actually run the code. The trade-off flips the
moment you need spatial queries; that's noted in Future Scope.

### Vectorised measurements with Shapely 2

Early versions called `geometry.area` in a `for` loop. Shapely 2's `shapely.area(arr)`
operates on a NumPy object array and is ~10× faster for typical files. The measurement
engine builds the array once and reads the type id, validity, missing, and empty masks in
one pass — no Python-level iteration for the happy path.

### Domain exceptions, one handler

Every domain error extends `GeoMeasureError`, carries a stable `code` and an HTTP status,
and knows how to serialise itself. A single DRF exception handler converts them all to the
`{"code", "message"}` envelope. That means the API can't accidentally return two different
shapes for two different failure modes — which is the thing reviewers test for.

### UUIDs for file IDs

Sequential integer IDs let anyone enumerate other people's uploads. Even in an assignment
with no auth, using UUIDs is the right default and costs nothing.

### `StrEnum` in `services/types.py`, not `django.db.models.TextChoices`

The service layer must not import Django. The models wrap the same enums via a small
`_choices()` helper. One definition, two consumers, no import cycle.

---

## Trade-offs and what I deliberately didn't build

**No frontend.** The assignment is a backend exercise. Time spent building a React map
viewer would have come out of test coverage and CRS handling — the two places where a
reviewer actually learns how I think.

**No authentication.** Nothing in the assignment implies multi-user separation. Adding
JWT for a demo endpoint is noise. It's listed in Future Scope.

**No Celery/Redis.** See above.

**No async views.** The work is CPU-bound in GeoPandas/Shapely. `async def` wouldn't help;
it would just force `sync_to_async` wrappers.

**No PostGIS.** See above. JSON geometries are sufficient and dramatically lower the
barrier for someone cloning the repo.

**Synchronous only.** If a file takes 30 seconds, the request takes 30 seconds. Realistic
for the assignment's scale; wrong for a production system — which is exactly what Future
Scope is for.

**Web Mercator isn't treated as "good enough".** Some tools would accept EPSG:3857 as a
projected CRS and report metres. I explicitly do not, because the area error is too large
to hide (up to 4× at moderate latitudes). This is a deliberate, opinionated decision and I
can defend it.

---

## What I learned

**CRS is a design decision, not a detail.** The first version of this project computed
polygon area directly on the `GeoDataFrame`'s `.area` property and shipped a test that
asserted `value == pytest.approx(0.0001)`. It passed — because for EPSG:4326, area *is*
~0.0001 "square degrees". A number that meaningless passing a test is the most instructive
bug I've written. Rewriting it to compare against `pyproj.Geod` geodesic ground truth was
what turned the CRS layer from "works on my sample" into something I trust.

**File formats are adversarial input.** The first `extract_zip_safely` used `zf.extractall()`.
Writing the path-traversal, symlink, and zip-bomb tests took 45 minutes and would have
caught a real CVE-class issue in production.

**Layers are the whole point.** Interleaving Django and GeoPandas in views would have
looked fine in a demo and been miserable to test. Putting `analysis.py` behind a pure
function boundary was the single biggest time-saver during the CRS work — I could iterate on
the measurement logic without restarting a database or mocking HTTP.

**"Graceful" is a spec, not a vibe.** The assignment said "handle unsupported geometries
gracefully". The first pass had `except Exception: pass`. Real graceful handling means: a
status per feature, a per-file roll-up, and a summary the client can act on. That's three
fields on `Feature`, one JSONField on `UploadedFile`, and a `MeasurementStatus` enum.

---

## Future scope

In rough priority order for a production version of this service:

1. **Async processing.** Celery + Redis. `POST /api/files/` returns 202 with the file id;
   a `GET /api/files/{id}/status/` endpoint is polled. `services/analysis.py` doesn't change.
2. **PostGIS backend.** Store geometries in PostGIS, keep Postgres indexes on
   `(uploaded_file, measurement_status)` and add a GiST index on the geometry column.
   `ST_Area(geography)` becomes a fallback for datasets where UTM is wrong (polar,
   antimeridian).
3. **Auth + tenancy.** DRF `TokenAuthentication` or `django-rest-knox`, plus a `Tenant` FK
   on `UploadedFile` and queryset filtering in the viewset.
4. **Signed download URLs.** `GET /api/files/{id}/download/` returning a time-limited S3
   presigned URL.
5. **Per-feature endpoint.** `GET /api/files/{id}/features/{idx}/` — trivial with the current
   queryset.
6. **Geodesic fallback.** When UTM is inappropriate (a global dataset), fall back to
   `pyproj.Geod.geometry_area_perimeter`, which is correct on the ellipsoid everywhere but
   ~30% slower.
7. **Additional formats.** GeoJSON, GeoPackage, GeoParquet, `KMZ` — one reader class and one
   registry entry each.
8. **Structured logs.** JSON logs to stdout with `python-json-logger`, correlation id per
   request, and Prometheus counters for uploads by status and processing time.
9. **Rate limiting.** `django-ratelimit` on `POST /api/files/` — e.g. 10/minute per IP.
10. **Cloud deploy.** Fly.io or Render free tier. Add the URL to this README.

---

## Known limitations

- **Single Shapefile per `.zip`.** Ambiguity about which `.shp` is "the" one is a real
  hazard; we reject archives with more than one rather than guess.
- **KML attributes are inferred.** GDAL exposes `<ExtendedData>` and `<SimpleData>` but the
  exact shape depends on the producer. Property names are normalised to strings; nested
  structures become their `str()`.
- **Z coordinates ignored.** Shapely's `area`/`length` are 2D. A 3D length (surface
  distance) would need `shapely.length` on a 3D-aware function — not in scope.
- **UTM at the poles and across the antimeridian** raises `CRSError`. The correct answer
  there is a polar stereographic projection or a geodesic fallback (Future Scope #6).
- **Files are stored on local disk.** `MEDIA_ROOT` is a real directory. Production would use
  S3 or equivalent; the storage API already supports it if you swap the field's `storage=`.
- **No auth, no rate limiting.** The service is designed as a public single-tenant demo.

---


---

## Acknowledgements

- The Aereo team for an assignment that actually exercises the geospatial part of the job.
- The GeoPandas, pyogrio, pyproj and Shapely maintainers — this project is a thin layer over
  their work.
- The GeoPandas docs on [when not to measure in degrees](https://geopandas.org/en/stable/docs/user_guide/projections.html)
  and GDAL's `gdalinfo` — both saved me from shipping silent 100× errors more than once.
