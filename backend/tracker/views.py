from django.core import signing
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .api import (
    Conflict, EVENT_FIELDS, IngestThrottle, SessionThrottle, TOKEN_LIFETIME, TOKEN_SALT,
    collection_session, ingest, network_records, observe_network, pagination, public_assessment,
    require_analyst, session_record,
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
        observe_network(request, session, "session")
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
        if result["stored"]:
            observe_network(request, session, "telemetry")
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
        observe_network(request, session, "fingerprint")
        return Response({"stored": True})


class CompleteView(APIView):
    throttle_classes = [IngestThrottle]

    def post(self, request, session_id):
        from ml.inference import assess

        session = collection_session(request, session_id)
        if session.state != "complete":
            observe_network(request, session, "complete")
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=session.pk)
            if session.state != "complete":
                events = list(session.events.values(*EVENT_FIELDS))
                session.assessment = assess(events, session.fingerprint, network_records(session), session.task)
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
            "label_revisions": list(session.label_revisions.order_by("-created_at").values("label", "source", "group", "created_at")[:20]),
            "network_observations": list(session.network_observations.values("endpoint", "observed_at", "data")[:50]),
            "network_observation_total": session.network_observations.count(),
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
            "label_revisions": list(session.label_revisions.values("label", "source", "group", "created_at")),
            "network_observations": network_records(session),
            "export_schema": "humanproof-session-v2",
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
            session.label_group = serializer.validated_data["group"]
            session.save(update_fields=["label", "label_source", "label_group"])
            LabelRevision.objects.create(session=session, label=session.label, source=session.label_source,
                                         group=session.label_group)
        return Response({"label": session.label, "source": session.label_source, "group": session.label_group})


class EvaluationView(APIView):
    def get(self, request):
        from ml.inference import current_model_version

        require_analyst(request)
        try:
            threshold = float(request.query_params.get("threshold", 60))
        except (TypeError, ValueError):
            raise ValidationError("Threshold must be between 0 and 100.")
        if not 0 <= threshold <= 100:
            raise ValidationError("Threshold must be between 0 and 100.")
        model_version = request.query_params.get("model_version") or current_model_version()
        matrix = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
        total = excluded = 0
        scores = []
        for session in Session.objects.filter(label__in=["human", "bot"]).only("label", "label_group", "assessment").iterator():
            total += 1
            result = session.assessment or {}
            if result.get("status") != "ready" or result.get("risk_score") is None or result.get("model_version") != model_version:
                excluded += 1
                continue
            scores.append({"label": session.label, "group": session.label_group, "risk_score": result["risk_score"]})
            predicted_bot = result["risk_score"] >= threshold
            key = ("tp" if predicted_bot else "fn") if session.label == "bot" else ("fp" if predicted_bot else "tn")
            matrix[key] += 1
        tp, tn, fp, fn = (matrix[key] for key in ("tp", "tn", "fp", "fn"))
        def ratio(numerator, denominator):
            return numerator / denominator if denominator else None
        return Response({
            "threshold": threshold, "total_labeled": total, "labeled_assessed": total - excluded,
            "excluded_insufficient": excluded, "confusion_matrix": matrix, "model_version": model_version,
            "scores": scores[:2000],
            "metrics": {"precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
                        "false_positive_rate": ratio(fp, fp + tn), "accuracy": ratio(tp + tn, tp + tn + fp + fn)},
            "limitations": [
                "Preliminary labeled demo sessions, not a frozen held-out evaluation.",
                "Scores and threshold are experimental; see the model report for calibration and held-out results.",
                "Insufficient, pending, and other model versions are excluded and counted; run rescore after training.",
                "Repeated participants and automation runs may not be independent.",
                "Sessions used to train the model score optimistically here; see the model report for held-out results.",
            ],
        })


class ModelView(APIView):
    """Active scorer, the trained model card (if any), the rule catalogue, and the policy."""

    def get(self, request):
        from ml.inference import POLICY, current_model_version, load_model, model_path
        from ml.scoring import MODEL_VERSION, rule_catalogue

        require_analyst(request)
        model = load_model()
        return Response({
            "active_version": current_model_version(), "rules_version": MODEL_VERSION,
            "model": model.spec if model else None, "model_file": model_path().name,
            "rules": rule_catalogue(), "policy": POLICY,
        })


class LegacyView(APIView):
    def get(self, request):
        return Response({"detail": "Use the protected /api/v1/sessions/ endpoints."}, status=410)

    def post(self, request):
        return Response({"detail": "Use /api/v1/sessions/ and versioned /api/v1/telemetry/ batches."}, status=410)
