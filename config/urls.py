from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    # Uploaded files. Django's own file serving is slow, but this is a
    # single-user LAN app behind traefik, and the alternative is another
    # container just to serve a directory.
    path("media/<path:path>", serve, {"document_root": settings.MEDIA_ROOT}),
    path("", include("blobs.urls")),
]
