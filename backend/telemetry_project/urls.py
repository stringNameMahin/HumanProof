import mimetypes

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.urls import include, path


def index(request):
    return FileResponse((settings.FRONTEND_DIR / "index.html").open("rb"), content_type="text/html")


def asset(request, asset_path):
    candidate = (settings.FRONTEND_DIR / asset_path).resolve()
    if not candidate.is_relative_to(settings.FRONTEND_DIR.resolve()) or not candidate.is_file():
        raise Http404
    if candidate.suffix not in (".js", ".css"):
        raise Http404
    content_type = "text/javascript" if candidate.suffix == ".js" else mimetypes.guess_type(str(candidate))[0]
    return FileResponse(candidate.open("rb"), content_type=content_type)


def health(request):
    return JsonResponse({"status": "ok", "service": "humanproof"})


urlpatterns = [
    path("", index, name="index"),
    path("assets/<path:asset_path>", asset),
    path("health/", health),
    path("api/", include("tracker.urls")),
]
