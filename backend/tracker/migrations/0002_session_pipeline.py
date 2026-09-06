import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Count

import tracker.models


def migrate_legacy(apps, schema_editor):
    Session = apps.get_model("tracker", "Session")
    Event = apps.get_model("tracker", "TelemetryEvent")
    database = schema_editor.connection.alias
    groups = Event.objects.using(database).values("session_id").annotate(total=Count("id"))
    for group in groups:
        Session.objects.using(database).create(
            id=group["session_id"], task="legacy", schema_version=0,
            state="complete", event_count=group["total"],
        )
    for event in Event.objects.using(database).all().iterator():
        Event.objects.using(database).filter(pk=event.pk).update(event_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("tracker", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Session",
            fields=[
                ("id", models.CharField(default=tracker.models.session_id, editable=False, max_length=64, primary_key=True, serialize=False)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("task", models.CharField(default="mixed-demo", max_length=64)),
                ("schema_version", models.PositiveSmallIntegerField(default=1)),
                ("state", models.CharField(default="active", max_length=16)),
                ("label", models.CharField(default="unknown", max_length=16)),
                ("label_source", models.CharField(blank=True, max_length=200)),
                ("event_count", models.PositiveIntegerField(default=0)),
                ("fingerprint", models.JSONField(blank=True, null=True)),
                ("assessment", models.JSONField(blank=True, null=True)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddField("telemetryevent", "event_id", models.UUIDField(null=True)),
        migrations.RunPython(migrate_legacy, migrations.RunPython.noop),
        migrations.RenameField("telemetryevent", "session_id", "session"),
        migrations.AlterField("telemetryevent", "session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="tracker.session")),
        migrations.AlterField("telemetryevent", "event_id", models.UUIDField(default=uuid.uuid4)),
        migrations.AddField("telemetryevent", "seq", models.PositiveIntegerField(null=True)),
        migrations.AddField("telemetryevent", "elapsed_ms", models.FloatField(null=True)),
        migrations.AddField("telemetryevent", "metadata", models.JSONField(default=dict)),
        migrations.AlterField("telemetryevent", "x", models.FloatField(null=True)),
        migrations.AlterField("telemetryevent", "y", models.FloatField(null=True)),
        migrations.AlterField("telemetryevent", "ts", models.BigIntegerField()),
        migrations.AlterField("telemetryevent", "event_type", models.CharField(choices=[(value, value) for value in ("move", "click", "scroll", "focus", "shortcut")], db_index=True, max_length=16)),
        migrations.AlterModelOptions("telemetryevent", {"ordering": ["seq", "id"]}),
        migrations.AddConstraint("telemetryevent", models.UniqueConstraint(fields=("session", "event_id"), name="unique_session_event")),
        migrations.AddConstraint("telemetryevent", models.UniqueConstraint(fields=("session", "seq"), name="unique_session_sequence")),
        migrations.AddIndex("telemetryevent", models.Index(fields=["session", "ts"], name="session_event_time")),
        migrations.CreateModel(
            name="TelemetryBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("batch_id", models.UUIDField()),
                ("payload_hash", models.CharField(max_length=64)),
                ("event_count", models.PositiveIntegerField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="tracker.session")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("session", "batch_id"), name="unique_session_batch")]},
        ),
        migrations.CreateModel(
            name="LabelRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=16)),
                ("source", models.CharField(max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="label_revisions", to="tracker.session")),
            ],
        ),
    ]
