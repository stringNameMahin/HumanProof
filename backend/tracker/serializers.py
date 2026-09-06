import math

from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({key: "Unknown field." for key in sorted(unknown)})
        return super().to_internal_value(data)


class FiniteFloat(serializers.FloatField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            raise serializers.ValidationError("Must be a finite number.")
        return value


class SessionSerializer(StrictSerializer):
    task = serializers.ChoiceField(choices=["mixed-demo", "click-target", "reading", "form-demo"], default="mixed-demo")
    schema_version = serializers.ChoiceField(choices=[1], default=1)


class MetadataSerializer(StrictSerializer):
    pointer_type = serializers.ChoiceField(choices=["mouse", "touch", "pen", "keyboard", "unknown", ""], required=False)
    viewport_width = serializers.IntegerField(min_value=0, max_value=100000, required=False)
    viewport_height = serializers.IntegerField(min_value=0, max_value=100000, required=False)
    scroll_y = FiniteFloat(min_value=-10000000, max_value=10000000, required=False)
    delta_y = FiniteFloat(min_value=-10000000, max_value=10000000, required=False)
    scroll_source = serializers.ChoiceField(choices=["page", "panel"], required=False)
    focused = serializers.BooleanField(required=False)
    shortcut_category = serializers.ChoiceField(choices=["copy", "paste", "cut", "select_all", "undo", "redo", "navigation", "other"], required=False)
    target_width = FiniteFloat(min_value=0, max_value=100000, required=False)
    target_height = FiniteFloat(min_value=0, max_value=100000, required=False)


class EventSerializer(StrictSerializer):
    event_id = serializers.UUIDField()
    seq = serializers.IntegerField(min_value=0, max_value=10000000)
    event_type = serializers.ChoiceField(choices=["move", "click", "scroll", "focus", "shortcut"])
    x = FiniteFloat(min_value=-100000, max_value=100000, allow_null=True, default=None)
    y = FiniteFloat(min_value=-100000, max_value=100000, allow_null=True, default=None)
    ts = serializers.IntegerField(min_value=0, max_value=9999999999999)
    elapsed_ms = FiniteFloat(min_value=0, max_value=7200000)
    metadata = MetadataSerializer(default=dict)

    def validate(self, attrs):
        keyboard_click = attrs["event_type"] == "click" and attrs["metadata"].get("pointer_type") == "keyboard"
        if attrs["event_type"] in ("move", "click") and not keyboard_click and (attrs["x"] is None or attrs["y"] is None):
            raise serializers.ValidationError("Pointer events require x and y coordinates.")
        return attrs


class BatchSerializer(StrictSerializer):
    session_id = serializers.CharField(max_length=64)
    batch_id = serializers.UUIDField()
    schema_version = serializers.ChoiceField(choices=[1])
    events = EventSerializer(many=True, allow_empty=False, max_length=200)

    def validate_events(self, events):
        if len({item["event_id"] for item in events}) != len(events):
            raise serializers.ValidationError("Event IDs must be unique within a batch.")
        if len({item["seq"] for item in events}) != len(events):
            raise serializers.ValidationError("Sequences must be unique within a batch.")
        return events


class BrowserSignalsSerializer(StrictSerializer):
    webdriver = serializers.BooleanField(allow_null=True, required=False)
    hardware_concurrency = serializers.IntegerField(min_value=0, max_value=4096, allow_null=True, required=False)
    language = serializers.CharField(max_length=64, allow_blank=True, allow_null=True, required=False)
    timezone = serializers.CharField(max_length=100, allow_blank=True, allow_null=True, required=False)
    viewport_width = serializers.IntegerField(min_value=0, max_value=100000, allow_null=True, required=False)
    viewport_height = serializers.IntegerField(min_value=0, max_value=100000, allow_null=True, required=False)
    screen_width = serializers.IntegerField(min_value=0, max_value=100000, allow_null=True, required=False)
    screen_height = serializers.IntegerField(min_value=0, max_value=100000, allow_null=True, required=False)
    pixel_ratio = FiniteFloat(min_value=0, max_value=100, allow_null=True, required=False)
    max_touch_points = serializers.IntegerField(min_value=0, max_value=100, allow_null=True, required=False)
    pointer_type = serializers.ChoiceField(choices=["fine", "coarse", "none"], allow_null=True, required=False)
    availability = serializers.DictField(child=serializers.BooleanField(), required=False)

    def validate_availability(self, value):
        if any(key not in self.fields or key == "availability" for key in value):
            raise serializers.ValidationError("Unknown browser capability.")
        return value


class FingerprintSerializer(StrictSerializer):
    session_id = serializers.CharField(max_length=64)
    schema_version = serializers.ChoiceField(choices=[1])
    signals = BrowserSignalsSerializer()


class LabelSerializer(StrictSerializer):
    label = serializers.ChoiceField(choices=["human", "bot", "unknown"])
    source = serializers.CharField(max_length=200, min_length=3)
