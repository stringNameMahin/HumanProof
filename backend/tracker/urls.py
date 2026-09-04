from django.urls import path

from .views import TelemetryIngestView

urlpatterns = [
    path("telemetry/", TelemetryIngestView.as_view(), name="telemetry-ingest"),
]
