import base64
import binascii
import hashlib
import hmac
import json

from django.conf import settings
from django.core import signing
from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.response import Response
from rest_framework.views import exception_handler as default_exception_handler

from .models import NetworkObservation, Session, TelemetryBatch, TelemetryEvent

TOKEN_SALT = "humanproof.collection.v1"
TOKEN_LIFETIME = 7200
EVENT_FIELDS = ["event_id", "seq", "event_type", "x", "y", "ts", "elapsed_ms", "metadata"]
EDGE_HEADER = "HTTP_X_HUMANPROOF_EDGE"
EDGE_SIGNATURE_HEADER = "HTTP_X_HUMANPROOF_EDGE_SIGNATURE"
MAX_NETWORK_OBSERVATIONS = 1200
OBSERVED_HEADERS = {
    "user_agent": "HTTP_USER_AGENT", "accept": "HTTP_ACCEPT", "accept_language": "HTTP_ACCEPT_LANGUAGE",
    "accept_encoding": "HTTP_ACCEPT_ENCODING", "origin": "HTTP_ORIGIN",
    "sec_fetch_site": "HTTP_SEC_FETCH_SITE", "sec_fetch_mode": "HTTP_SEC_FETCH_MODE",
    "sec_fetch_dest": "HTTP_SEC_FETCH_DEST", "sec_ch_ua": "HTTP_SEC_CH_UA",
    "sec_ch_ua_mobile": "HTTP_SEC_CH_UA_MOBILE", "sec_ch_ua_platform": "HTTP_SEC_CH_UA_PLATFORM",
    "connection": "HTTP_CONNECTION",
}


def exception_handler(exc, context):
    if isinstance(exc, OperationalError) and "locked" in str(exc).lower():
        return Response({"detail": "Storage is busy. Retry this request."}, status=503, headers={"Retry-After": "1"})
    return default_exception_handler(exc, context)


class Conflict(APIException):
    status_code = 409


class Unavailable(APIException):
    status_code = 503


class SessionThrottle(SimpleRateThrottle):
    scope = "sessions"
    setting = "HUMANPROOF_SESSION_RATE"

    def get_rate(self):
        return getattr(settings, self.setting)

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class IngestThrottle(SessionThrottle):
    scope = "ingestion"
    setting = "HUMANPROOF_INGEST_RATE"


class BodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH"):
            try:
                if len(request.body) > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
                    return JsonResponse({"detail": "Request body exceeds 256 KB."}, status=413)
            except RequestDataTooBig:
                return JsonResponse({"detail": "Request body exceeds 256 KB."}, status=413)
        return self.get_response(request)


def collection_session(request, session_id):
    token = request.headers.get("X-Collection-Token", "")
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_LIFETIME)
    except signing.BadSignature:
        raise PermissionDenied("Missing, invalid, or expired collection token.")
    if not isinstance(payload, dict) or payload.get("session_id") != str(session_id):
        raise PermissionDenied("Collection token belongs to another session.")
    return get_object_or_404(Session, pk=session_id)


def edge_metadata(request):
    """TLS details forwarded by edge/tls_edge.py; ignored unless the HMAC matches."""
    encoded = request.META.get(EDGE_HEADER, "")
    signature = request.META.get(EDGE_SIGNATURE_HEADER, "")
    key = settings.HUMANPROOF_EDGE_KEY
    if not encoded or not signature or not key or len(encoded) > 4096:
        return None
    expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(encoded.encode()))
    except (ValueError, binascii.Error):
        return None
    return data if isinstance(data, dict) else None


def observe_network(request, session, endpoint):
    """Store the request metadata the network layer analyses; never cookies or tokens."""
    if NetworkObservation.objects.filter(session=session).count() >= MAX_NETWORK_OBSERVATIONS:
        return
    names = [key[5:].lower().replace("_", "-") for key in request.META
             if key.startswith("HTTP_") and not key.startswith("HTTP_X_HUMANPROOF_EDGE")]
    data = {name: (request.META.get(header) or None) for name, header in OBSERVED_HEADERS.items()}
    data = {name: value[:512] if isinstance(value, str) else value for name, value in data.items()}
    data.update({
        "header_names": names[:64],
        "header_order_hash": hashlib.sha256(",".join(names).encode()).hexdigest()[:16],
        "tls": edge_metadata(request),
    })
    NetworkObservation.objects.create(session=session, endpoint=endpoint, data=data)


def network_records(session):
    return list(session.network_observations.values_list("data", flat=True))


def require_analyst(request):
    expected = settings.HUMANPROOF_ANALYST_KEY
    if not expected:
        raise Unavailable("Analyst access is not configured. Run init_demo and restart the API.")
    supplied = request.headers.get("X-Analyst-Key", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise PermissionDenied("A valid analyst key is required.")


def pagination(request, default=20, maximum=100):
    try:
        page = int(request.query_params.get("page", 1))
        size = int(request.query_params.get("page_size", default))
    except (ValueError, TypeError):
        raise ValidationError("Page and page_size must be integers.")
    if page < 1 or not 1 <= size <= maximum:
        raise ValidationError(f"Page must be positive; page_size must be 1 to {maximum}.")
    return page, size, (page - 1) * size


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def ingest(session, data):
    events = data["events"]
    digest = hashlib.sha256(canonical(events).encode()).hexdigest()
    try:
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=session.pk)
            previous = TelemetryBatch.objects.filter(session=session, batch_id=data["batch_id"]).first()
            if previous:
                if previous.payload_hash != digest:
                    raise Conflict("Batch ID was already used for different data.")
                return {"stored": 0, "duplicates": len(events), "batch_id": str(data["batch_id"])}
            if session.state != "active":
                raise Conflict("Collection has already completed.")
            if TelemetryBatch.objects.filter(session=session).count() >= 1000:
                raise Conflict("Session batch limit reached. Start a new session.")
            existing = {
                row["event_id"]: row
                for row in session.events.filter(event_id__in=[event["event_id"] for event in events]).values(*EVENT_FIELDS)
            }
            fresh = []
            for event in events:
                previous_event = existing.get(event["event_id"])
                if previous_event is not None:
                    if canonical(previous_event) != canonical(event):
                        raise Conflict("Event ID was already used for different data.")
                else:
                    fresh.append(TelemetryEvent(session=session, **event))
            if session.event_count + len(fresh) > 20000:
                raise Conflict("Session event limit reached. Finish this session.")
            TelemetryEvent.objects.bulk_create(fresh, batch_size=200)
            TelemetryBatch.objects.create(session=session, batch_id=data["batch_id"], payload_hash=digest, event_count=len(events))
            Session.objects.filter(pk=session.pk).update(event_count=F("event_count") + len(fresh))
            return {"stored": len(fresh), "duplicates": len(events) - len(fresh), "batch_id": str(data["batch_id"])}
    except IntegrityError:
        raise Conflict("Event sequence or batch identity conflicts with stored data. Retry with the original batch.")


def session_record(session):
    return {
        "id": session.id,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "task": session.task,
        "state": session.state,
        "schema_version": session.schema_version,
        "label": session.label,
        "label_source": session.label_source,
        "label_group": session.label_group,
        "event_count": session.event_count,
        "assessment": session.assessment,
    }


def public_assessment(session):
    if not session.assessment:
        return {"status": "pending", "risk_score": None, "decision": "observe"}
    return {key: session.assessment[key] for key in (
        "status", "risk_score", "rule_score", "model_score", "decision", "model_version", "feature_version",
        "reasons", "layer_scores", "available_layers", "limitations", "policy"
    ) if key in session.assessment}
