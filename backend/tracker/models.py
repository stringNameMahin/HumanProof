import uuid

from django.db import models


def session_id():
    return str(uuid.uuid4())


class Session(models.Model):
    id = models.CharField(primary_key=True, max_length=64, default=session_id, editable=False)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    task = models.CharField(max_length=64, default="mixed-demo")
    schema_version = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(max_length=16, default="active")
    label = models.CharField(max_length=16, default="unknown")
    label_source = models.CharField(max_length=200, blank=True)
    event_count = models.PositiveIntegerField(default=0)
    fingerprint = models.JSONField(null=True, blank=True)
    assessment = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]


class TelemetryEvent(models.Model):
    EVENT_TYPES = [(value, value) for value in ("move", "click", "scroll", "focus", "shortcut")]
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="events")
    event_id = models.UUIDField(default=uuid.uuid4)
    seq = models.PositiveIntegerField(null=True)
    event_type = models.CharField(max_length=16, choices=EVENT_TYPES, db_index=True)
    x = models.FloatField(null=True)
    y = models.FloatField(null=True)
    ts = models.BigIntegerField()
    elapsed_ms = models.FloatField(null=True)
    metadata = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["seq", "id"]
        constraints = [
            models.UniqueConstraint(fields=["session", "event_id"], name="unique_session_event"),
            models.UniqueConstraint(fields=["session", "seq"], name="unique_session_sequence"),
        ]
        indexes = [models.Index(fields=["session", "ts"], name="session_event_time")]


class TelemetryBatch(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    batch_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    event_count = models.PositiveIntegerField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "batch_id"], name="unique_session_batch")]


class LabelRevision(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="label_revisions")
    label = models.CharField(max_length=16)
    source = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
