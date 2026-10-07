"""``process_file``: the application service that ties analysis to persistence.

Deliberately HTTP-agnostic. A Celery task would simply call ``process_file(uploaded.id)``.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from geospatial.exceptions import GeoMeasureError, ProcessingError
from geospatial.models import Feature, UploadedFile

from .analysis import analyze_file
from .types import FileStatus, FileType

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def process_file(uploaded: UploadedFile) -> UploadedFile:
    """Analyse ``uploaded`` and persist its features. Raises ``GeoMeasureError`` on failure."""
    started = time.perf_counter()
    logger.info("Started processing file %s (%s)", uploaded.id, uploaded.file_type)

    try:
        with tempfile.TemporaryDirectory(prefix="geomeasure-") as tmp:
            tmp_dir = Path(tmp)
            suffix = ".kml" if uploaded.file_type == FileType.KML.value else ".zip"
            local_path = tmp_dir / f"upload{suffix}"
            # Copy via the storage API so this also works with remote storage (e.g. S3).
            with uploaded.file.open("rb") as src, open(local_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            result = analyze_file(local_path, FileType(uploaded.file_type), tmp_dir / "work")

        with transaction.atomic():
            Feature.objects.bulk_create(
                [
                    Feature(
                        uploaded_file=uploaded,
                        source_index=f.source_index,
                        layer=f.layer,
                        geometry_type=f.geometry_type,
                        geometry=f.geometry,
                        properties=f.properties,
                        measurement_type=f.measurement.type,
                        measurement_value=f.measurement.value,
                        measurement_unit=f.measurement.unit,
                        measurement_status=f.measurement.status.value,
                        error_message=f.measurement.error,
                    )
                    for f in result.features
                ],
                batch_size=BULK_BATCH_SIZE,
            )
            uploaded.original_crs = result.crs
            uploaded.measurement_crs = result.measurement_crs
            uploaded.feature_count = len(result.features)
            uploaded.summary = result.summary
            uploaded.status = result.status.value
            uploaded.processed_at = timezone.now()
            uploaded.save()

    except GeoMeasureError as exc:
        logger.error("Failed processing file %s: [%s] %s", uploaded.id, exc.code, exc.message)
        _mark_failed(uploaded, exc.code, exc.message)
        exc.extra["file_id"] = str(uploaded.id)
        raise
    except Exception as exc:
        logger.exception("Unexpected error processing file %s", uploaded.id)
        error = ProcessingError()
        _mark_failed(uploaded, error.code, error.message)
        error.extra["file_id"] = str(uploaded.id)
        raise error from exc

    logger.info(
        "Processing completed for file %s: %d feature(s), status %s, %.2fs",
        uploaded.id,
        uploaded.feature_count,
        uploaded.status,
        time.perf_counter() - started,
    )
    return uploaded


def _mark_failed(uploaded: UploadedFile, code: str, message: str) -> None:
    uploaded.status = FileStatus.FAILED.value
    uploaded.error_code = code
    uploaded.error_message = message
    uploaded.processed_at = timezone.now()
    uploaded.save(update_fields=["status", "error_code", "error_message", "processed_at"])
