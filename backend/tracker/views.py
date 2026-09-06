from django.core import signing
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .api import (
    Conflict, EVENT_FIELDS, IngestThrottle, SessionThrottle, TOKEN_LIFETIME, TOKEN_SALT,
    collection_session, ingest, pagination, public_assessment, require_analyst, session_record,
)
from .models import LabelRevision, Session
from .serializers import BatchSerializer, FingerprintSerializer, LabelSerializer, SessionSerializer


class SessionListView(APIView):
    def get_throttles(self):
        return [SessionThrottle()] if self.request.method == "POST" else []

    def post(self, request):
        serializer = SessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = Session.objects.create(**serializer.validated_data)
        token = signing.dumps({"session_id": session.id}, salt=TOKEN_SALT)
        return Response({"session_id": session.id, "collection_token": token, "expires_in": TOKEN_LIFETIME}, status=201)

    def get(self, request):
        require_analyst(request)
        page, size, offset = pagination(request)
        sessions = Session.objects.all()
        for field, choices in (("label", ("human", "bot", "unknown")), ("state", ("active", "complete"))):
            value = request.query_params.get(field)
            if value:
                if value not in choices:
                    raise ValidationError({field: "Invalid filter."})
                sessions = sessions.filter(**{field: value})
        return Response({"count": sessions.count(), "page": page, "page_size": size,
                         "results": [session_record(item) for item in sessions[offset:offset + size]]})


class TelemetryIngestView(APIView):
    throttle_classes = [IngestThrottle]

    def post(self, request):
        serializer = BatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        session = collection_session(request, data["session_id"])
        result = ingest(session, data)
        return Response(result, status=201 if result["stored"] else 200)


class FingerprintView(APIView):
    throttle_classes = [IngestThrottle]

    def post(self, request):
        serializer = FingerprintSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        session = collection_session(request, data["session_id"])
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=session.pk)
            if session.state != "active":
                raise Conflict("Collection has already completed.")
            session.fingerprint = data["signals"]
            session.save(update_fields=["fingerprint"])
        return Response({"stored": True})


class CompleteView(APIView):
    throttle_classes = [IngestThrottle]

    def post(self, request, session_id):
        from ml.scoring import score_session

        session = collection_session(request, session_id)
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=session.pk)
            if session.state != "complete":
                events = list(session.events.values(*EVENT_FIELDS))
                session.assessment = score_session(events, session.fingerprint)
                session.state = "complete"
                session.completed_at = timezone.now()
                session.save(update_fields=["assessment", "state", "completed_at"])
        return Response(public_assessment(session))


class AssessmentView(APIView):
    def get(self, request, session_id):
        return Response(public_assessment(collection_session(request, session_id)))


class SessionDetailView(APIView):
    def get(self, request, session_id):
        require_analyst(request)
        session = get_object_or_404(Session, pk=session_id)
        page, size, offset = pagination(request, default=500, maximum=1000)
        data = session_record(session)
        data.update({
            "fingerprint": session.fingerprint,
            "events": list(session.events.values(*EVENT_FIELDS)[offset:offset + size]),
            "event_total": session.event_count, "event_page": page, "event_page_size": size,
            "label_revisions": list(session.label_revisions.order_by("-created_at").values("label", "source", "created_at")[:20]),
        })
        return Response(data)


class SessionExportView(APIView):
    def get(self, request, session_id):
        require_analyst(request)
        session = get_object_or_404(Session, pk=session_id)
        data = session_record(session)
        data.update({
            "fingerprint": session.fingerprint,
            "events": list(session.events.values(*EVENT_FIELDS)[:20000]),
            "event_total": session.event_count,
            "export_limit": 20000,
            "truncated": session.event_count > 20000,
            "label_revisions": list(session.label_revisions.values("label", "source", "created_at")),
            "export_schema": "humanproof-session-v1",
        })
        return Response(data)


class LabelView(APIView):
    def post(self, request, session_id):
        require_analyst(request)
        serializer = LabelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            session = get_object_or_404(Session.objects.select_for_update(), pk=session_id)
            session.label = serializer.validated_data["label"]
            session.label_source = serializer.validated_data["source"]
            session.save(update_fields=["label", "label_source"])
            LabelRevision.objects.create(session=session, label=session.label, source=session.label_source)
        return Response({"label": session.label, "source": session.label_source})


class EvaluationView(APIView):
    def get(self, request):
        require_analyst(request)
        try:
            threshold = float(request.query_params.get("threshold", 60))
        except (TypeError, ValueError):
            raise ValidationError("Threshold must be between 0 and 100.")
        if not 0 <= threshold <= 100:
            raise ValidationError("Threshold must be between 0 and 100.")
        matrix = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
        total = excluded = 0
        for session in Session.objects.filter(label__in=["human", "bot"]).only("label", "assessment").iterator():
            total += 1
            result = session.assessment or {}
            if result.get("status") != "ready" or result.get("risk_score") is None or result.get("model_version") != "rules-v1":
                excluded += 1
                continue
            predicted_bot = result["risk_score"] >= threshold
            key = ("tp" if predicted_bot else "fn") if session.label == "bot" else ("fp" if predicted_bot else "tn")
            matrix[key] += 1
        tp, tn, fp, fn = (matrix[key] for key in ("tp", "tn", "fp", "fn"))
        def ratio(numerator, denominator):
            return numerator / denominator if denominator else None
        return Response({
            "threshold": threshold, "total_labeled": total, "labeled_assessed": total - excluded,
            "excluded_insufficient": excluded, "confusion_matrix": matrix, "model_version": "rules-v1",
            "metrics": {"precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
                        "false_positive_rate": ratio(fp, fp + tn), "accuracy": ratio(tp + tn, tp + tn + fp + fn)},
            "limitations": [
                "Preliminary labeled demo sessions, not a frozen held-out evaluation.",
                "Rule scores and threshold are experimental and are not calibrated probabilities.",
                "Insufficient, pending, and incompatible model results are excluded and counted.",
                "Repeated participants and automation runs may not be independent.",
                "Network analysis and trained ensemble fusion are not implemented.",
            ],
        })


class LegacyView(APIView):
    def get(self, request):
        return Response({"detail": "Use the protected /api/v1/sessions/ endpoints."}, status=410)

    def post(self, request):
        return Response({"detail": "Use /api/v1/sessions/ and versioned /api/v1/telemetry/ batches."}, status=410)
