from rest_framework import serializers

from .models import TelemetryEvent


class TelemetryEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelemetryEvent
        fields = ["id", "session_id", "event_type", "x", "y", "ts", "received_at"]
        read_only_fields = ["id", "received_at"]
