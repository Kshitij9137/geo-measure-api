from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import UploadedFileViewSet, health

router = SimpleRouter()
router.register("files", UploadedFileViewSet, basename="file")

urlpatterns = [path("health/", health, name="health"), *router.urls]
