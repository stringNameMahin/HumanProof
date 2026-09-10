from django.conf import settings
from django.http import FileResponse
from django.urls import include, path


def index(request):
    """Serve the tracker page from the same origin as the API."""
    return FileResponse(open(settings.FRONTEND_DIR / "index.html", "rb"),
                        content_type="text/html")


urlpatterns = [
    path("", index, name="index"),
    path("api/", include("tracker.urls")),
]
