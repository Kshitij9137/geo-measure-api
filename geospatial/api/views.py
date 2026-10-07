import os

from django.db import connection
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from geospatial.exceptions import InvalidQueryParameterError, UploadedFileNotFoundError
from geospatial.models import UploadedFile
from geospatial.services.processor import process_file
from geospatial.services.types import MeasurementStatus
from geospatial.services.validators import validate_upload

from .serializers import (
    FeatureMeasurementSerializer,
    UploadedFileSerializer,
    UploadRequestSerializer,
)


class MeasurementPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


class UploadedFileViewSet(viewsets.GenericViewSet):
    queryset = UploadedFile.objects.all()
    serializer_class = UploadedFileSerializer
    pagination_class = MeasurementPagination

    def get_parsers(self):
        # Only the upload action needs multipart; everything else is a plain GET.
        return [MultiPartParser(), FormParser()]

    def get_object(self):
        try:
            return super().get_object()
        except Http404 as exc:
            raise UploadedFileNotFoundError() from exc

    @extend_schema(
        request={"multipart/form-data": UploadRequestSerializer},
        responses={201: UploadedFileSerializer},
        summary="Upload and process a geospatial file",
    )
    def create(self, request):
        upload = request.FILES.get("file")
        file_type = validate_upload(upload)

        uploaded = UploadedFile.objects.create(
            original_filename=os.path.basename(upload.name)[:255],
            file_type=file_type.value,
            file=upload,
        )
        process_file(uploaded)  # raises GeoMeasureError (-> 4xx/5xx JSON) on failure
        return Response(UploadedFileSerializer(uploaded).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: UploadedFileSerializer}, summary="File information")
    def retrieve(self, request, pk=None):
        return Response(UploadedFileSerializer(self.get_object()).data)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "status",
                str,
                enum=[s.value for s in MeasurementStatus],
                description="Only return features with this measurement status.",
            ),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: FeatureMeasurementSerializer(many=True)},
        summary="Per-feature measurements (paginated)",
    )
    @action(detail=True, methods=["get"], url_path="measurements")
    def measurements(self, request, pk=None):
        uploaded = self.get_object()
        features = uploaded.features.all()

        status_filter = request.query_params.get("status")
        if status_filter:
            status_filter = status_filter.upper()
            if status_filter not in {s.value for s in MeasurementStatus}:
                valid = ", ".join(s.value for s in MeasurementStatus)
                raise InvalidQueryParameterError(f"'status' must be one of: {valid}.")
            features = features.filter(measurement_status=status_filter)

        paginator = self.paginator
        page = paginator.paginate_queryset(features, request, view=self)
        data = FeatureMeasurementSerializer(page, many=True, context={"crs": uploaded.original_crs}).data
        response = paginator.get_paginated_response(data)
        response.data = {
            "file_id": str(uploaded.id),
            "crs": uploaded.original_crs,
            "measurement_crs": uploaded.measurement_crs,
            "feature_count": uploaded.feature_count,
            "summary": uploaded.summary,
            **response.data,
        }
        return response


@extend_schema(responses={200: dict}, summary="Health check")
@api_view(["GET"])
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return Response({"status": "unavailable", "database": "down"}, status=503)
    return Response({"status": "ok", "database": "up"})
