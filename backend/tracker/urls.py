from django.urls import path

from .views import (
    AssessmentView, CompleteView, EvaluationView, FingerprintView, LabelView,
    LegacyView, SessionDetailView, SessionExportView, SessionListView, TelemetryIngestView,
)

urlpatterns = [
    path("telemetry/", LegacyView.as_view()),
    path("v1/sessions/", SessionListView.as_view()),
    path("v1/telemetry/", TelemetryIngestView.as_view()),
    path("v1/fingerprints/", FingerprintView.as_view()),
    path("v1/sessions/<str:session_id>/complete/", CompleteView.as_view()),
    path("v1/sessions/<str:session_id>/assessment/", AssessmentView.as_view()),
    path("v1/sessions/<str:session_id>/labels/", LabelView.as_view()),
    path("v1/sessions/<str:session_id>/export/", SessionExportView.as_view()),
    path("v1/sessions/<str:session_id>/", SessionDetailView.as_view()),
    path("v1/evaluations/", EvaluationView.as_view()),
]
