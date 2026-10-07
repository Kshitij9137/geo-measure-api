"""One DRF exception handler so *every* error uses the same ``{"code", "message"}`` shape."""

import logging

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from geospatial.exceptions import GeoMeasureError

logger = logging.getLogger(__name__)


def _flatten(detail) -> str:
    if isinstance(detail, dict):
        return "; ".join(f"{key}: {_flatten(value)}" for key, value in detail.items())
    if isinstance(detail, list | tuple):
        return " ".join(_flatten(item) for item in detail)
    return str(detail)


def api_exception_handler(exc, context):
    if isinstance(exc, GeoMeasureError):
        return Response(exc.to_dict(), status=exc.status_code)

    response = drf_exception_handler(exc, context)
    if response is not None:
        code = getattr(exc, "default_code", "error")
        body = {"code": str(code).upper(), "message": _flatten(getattr(exc, "detail", str(exc)))}
        if isinstance(exc, ValidationError):
            body["code"] = "VALIDATION_ERROR"
        response.data = body
        return response

    logger.exception("Unhandled exception in API view", exc_info=exc)
    return Response({"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}, status=500)
