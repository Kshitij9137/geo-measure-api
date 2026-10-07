from django.contrib import admin

from .models import Feature, UploadedFile


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "file_type",
        "status",
        "original_crs",
        "measurement_crs",
        "feature_count",
        "created_at",
        "processed_at",
    )
    list_filter = ("status", "file_type")
    search_fields = ("original_filename", "id")
    readonly_fields = ("id", "created_at", "processed_at", "summary")


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = (
        "uploaded_file",
        "source_index",
        "geometry_type",
        "measurement_status",
        "measurement_value",
        "measurement_unit",
    )
    list_filter = ("measurement_status", "geometry_type")
    raw_id_fields = ("uploaded_file",)
