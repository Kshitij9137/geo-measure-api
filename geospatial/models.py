import os
import uuid

from django.db import models

from .services.types import FileStatus, FileType, MeasurementStatus


def _choices(enum):
    return [(member.value, member.value) for member in enum]


def upload_path(instance, filename):
    extension = os.path.splitext(filename)[1].lower()
    return f"uploads/{instance.id}{extension}"


class UploadedFile(models.Model):
    # UUID primary key: not enumerable like sequential integers.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20, choices=_choices(FileType))
    file = models.FileField(upload_to=upload_path)
    status = models.CharField(
        max_length=30, choices=_choices(FileStatus), default=FileStatus.PROCESSING.value
    )
    original_crs = models.CharField(max_length=100, blank=True)
    measurement_crs = models.CharField(max_length=100, blank=True)
    feature_count = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_filename} ({self.status})"


class Feature(models.Model):
    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.CASCADE, related_name="features")
    source_index = models.PositiveIntegerField()
    layer = models.CharField(max_length=255, blank=True, null=True)
    geometry_type = models.CharField(max_length=30, blank=True, null=True)
    geometry = models.JSONField(null=True, blank=True)  # GeoJSON geometry in the original CRS
    properties = models.JSONField(default=dict, blank=True)
    measurement_type = models.CharField(max_length=10, blank=True, null=True)
    measurement_value = models.FloatField(null=True, blank=True)
    measurement_unit = models.CharField(max_length=10, blank=True, null=True)
    measurement_status = models.CharField(max_length=20, choices=_choices(MeasurementStatus))
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["source_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["uploaded_file", "source_index"], name="unique_feature_index_per_file"
            )
        ]
        indexes = [models.Index(fields=["uploaded_file", "measurement_status"])]

    def __str__(self):
        return f"Feature {self.source_index} of {self.uploaded_file_id}"
