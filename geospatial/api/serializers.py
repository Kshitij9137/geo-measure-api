from rest_framework import serializers

from geospatial.models import Feature, UploadedFile


class UploadRequestSerializer(serializers.Serializer):
    """Documents the multipart request body (validation lives in ``services.validators``)."""

    file = serializers.FileField(help_text="A .kml file, or a .zip containing one Shapefile.")


class UploadedFileSerializer(serializers.ModelSerializer):
    filename = serializers.CharField(source="original_filename")
    crs = serializers.CharField(source="original_crs", help_text="CRS of the uploaded data.")
    error = serializers.SerializerMethodField()

    class Meta:
        model = UploadedFile
        fields = (
            "id",
            "filename",
            "file_type",
            "status",
            "feature_count",
            "crs",
            "measurement_crs",
            "summary",
            "error",
            "created_at",
            "processed_at",
        )
        read_only_fields = fields

    def get_error(self, obj) -> dict | None:
        if not obj.error_code:
            return None
        return {"code": obj.error_code, "message": obj.error_message}


class MeasurementSerializer(serializers.Serializer):
    type = serializers.CharField()
    value = serializers.FloatField()
    unit = serializers.CharField()


class FeatureMeasurementSerializer(serializers.ModelSerializer):
    """One feature. ``crs`` is file-level in GDAL/GeoPandas (one CRS per layer), so it is
    injected from the serializer context rather than stored redundantly on every row."""

    feature_id = serializers.IntegerField(source="source_index")
    crs = serializers.SerializerMethodField()
    measurement = serializers.SerializerMethodField()
    error = serializers.CharField(source="error_message", allow_null=True)

    class Meta:
        model = Feature
        fields = (
            "feature_id",
            "layer",
            "geometry_type",
            "crs",
            "geometry",
            "properties",
            "measurement",
            "measurement_status",
            "error",
        )
        read_only_fields = fields

    def get_crs(self, obj) -> str:
        return self.context.get("crs", "")

    def get_measurement(self, obj) -> dict | None:
        if obj.measurement_value is None:
            return None
        return {
            "type": obj.measurement_type,
            "value": obj.measurement_value,
            "unit": obj.measurement_unit,
        }
