import uuid
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from .api import TOKEN_SALT
from .models import LabelRevision, Session, TelemetryBatch, TelemetryEvent


@override_settings(HUMANPROOF_ANALYST_KEY="test-analyst-key")
class PipelineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        response = self.client.post("/api/v1/sessions/", {"task": "mixed-demo", "schema_version": 1}, format="json")
        self.assertEqual(response.status_code, 201)
        self.session_id = response.data["session_id"]
        self.token = response.data["collection_token"]
        self.auth = {"HTTP_X_COLLECTION_TOKEN": self.token}
        self.analyst = {"HTTP_X_ANALYST_KEY": "test-analyst-key"}

    def event(self, seq=0, **kwargs):
        return {"event_id": str(uuid.uuid4()), "seq": seq, "event_type": "move", "x": seq * 3,
                "y": 20, "ts": 1800000000000 + seq * 250, "elapsed_ms": seq * 250,
                "metadata": {"pointer_type": "mouse"}, **kwargs}

    def batch(self, events=None):
        return {"session_id": self.session_id, "batch_id": str(uuid.uuid4()), "schema_version": 1,
                "events": events if events is not None else [self.event()]}

    def upload(self, batch):
        return self.client.post("/api/v1/telemetry/", batch, format="json", **self.auth)

    def test_retry_and_event_deduplication(self):
        batch = self.batch()
        self.assertEqual(self.upload(batch).data["stored"], 1)
        retry = self.upload(batch)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["duplicates"], 1)
        batch["batch_id"] = str(uuid.uuid4())
        self.assertEqual(self.upload(batch).data["stored"], 0)
        self.assertEqual(TelemetryEvent.objects.count(), 1)
        self.assertEqual(Session.objects.get(pk=self.session_id).event_count, 1)

    def test_reused_identity_with_changed_data_is_rejected(self):
        batch = self.batch()
        self.upload(batch)
        batch["events"][0]["x"] = 900
        self.assertEqual(self.upload(batch).status_code, 409)
        batch["batch_id"] = str(uuid.uuid4())
        self.assertEqual(self.upload(batch).status_code, 409)
        self.assertEqual(TelemetryEvent.objects.get().x, 0)

    def test_duplicate_sequence_rolls_back_entire_batch(self):
        self.upload(self.batch())
        response = self.upload(self.batch([self.event(1), self.event(0)]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(TelemetryEvent.objects.count(), 1)
        self.assertEqual(TelemetryBatch.objects.count(), 1)

    def test_malformed_batch_is_atomic(self):
        for invalid in ({"event_type": "password"}, {"metadata": {"text": "private"}}, {"x": None}, {"elapsed_ms": -1}):
            with self.subTest(invalid=invalid):
                response = self.upload(self.batch([self.event(0), self.event(1, **invalid)]))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(TelemetryEvent.objects.count(), 0)

    def test_batch_and_body_limits(self):
        self.assertEqual(self.upload(self.batch([self.event(i) for i in range(201)])).status_code, 400)
        response = self.client.post("/api/v1/telemetry/", '{"large":"' + "x" * 300000 + '"}', content_type="application/json")
        self.assertEqual(response.status_code, 413)

    def test_token_scope_and_expiration(self):
        other = self.client.post("/api/v1/sessions/", {}, format="json").data
        batch = self.batch()
        batch["session_id"] = other["session_id"]
        self.assertEqual(self.upload(batch).status_code, 403)
        with patch("django.core.signing.time.time", return_value=1):
            expired = signing.dumps({"session_id": self.session_id}, salt=TOKEN_SALT)
        response = self.client.post("/api/v1/telemetry/", self.batch(), format="json", HTTP_X_COLLECTION_TOKEN=expired)
        self.assertEqual(response.status_code, 403)

    def test_browser_allowlist_and_keyboard_click(self):
        body = {"session_id": self.session_id, "schema_version": 1, "signals": {"webdriver": False, "availability": {"webdriver": True}}}
        self.assertEqual(self.client.post("/api/v1/fingerprints/", body, format="json", **self.auth).status_code, 200)
        body["signals"]["typed_text"] = "private"
        self.assertEqual(self.client.post("/api/v1/fingerprints/", body, format="json", **self.auth).status_code, 400)
        event = self.event(event_type="click", x=None, y=None, metadata={"pointer_type": "keyboard"})
        self.assertEqual(self.upload(self.batch([event])).status_code, 201)

    def test_session_completion_is_idempotent_and_freezes_input(self):
        batch = self.batch([self.event(i) for i in range(30)])
        self.assertEqual(self.upload(batch).status_code, 201)
        url = f"/api/v1/sessions/{self.session_id}/complete/"
        first = self.client.post(url, {}, format="json", **self.auth)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["status"], "ready")
        self.assertEqual(first.data["decision"], "observe")
        self.assertNotIn("features", first.data)
        self.assertEqual(self.client.post(url, {}, format="json", **self.auth).data, first.data)
        self.assertEqual(self.upload(batch).status_code, 200)
        self.assertEqual(self.upload(self.batch([self.event(31)])).status_code, 409)

    def test_insufficient_data_and_protected_reads(self):
        response = self.client.post(f"/api/v1/sessions/{self.session_id}/complete/", {}, format="json", **self.auth)
        self.assertEqual(response.data["status"], "insufficient_data")
        self.assertEqual(self.client.get("/api/v1/sessions/").status_code, 403)
        self.assertEqual(self.client.get("/api/telemetry/").status_code, 410)
        self.assertEqual(self.client.get("/api/v1/sessions/?page=0", **self.analyst).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/sessions/", **self.analyst).data["count"], 1)

    def test_label_history_export_and_metrics(self):
        url = f"/api/v1/sessions/{self.session_id}/labels/"
        for label in ("human", "bot"):
            self.assertEqual(self.client.post(url, {"label": label, "source": "manual test run"}, format="json", **self.analyst).status_code, 200)
        self.assertEqual(LabelRevision.objects.count(), 2)
        Session.objects.filter(pk=self.session_id).update(assessment={"status": "ready", "risk_score": 85, "model_version": "rules-v1"})
        Session.objects.create(label="human", assessment={"status": "ready", "risk_score": 10, "model_version": "rules-v1"})
        Session.objects.create(label="human")
        metrics = self.client.get("/api/v1/evaluations/", **self.analyst).data
        self.assertEqual(metrics["confusion_matrix"], {"tp": 1, "tn": 1, "fp": 0, "fn": 0})
        self.assertEqual(metrics["excluded_insufficient"], 1)
        self.assertEqual(metrics["metrics"]["recall"], 1)
        exported = self.client.get(f"/api/v1/sessions/{self.session_id}/export/", **self.analyst).data
        self.assertEqual(exported["export_schema"], "humanproof-session-v1")
        self.assertEqual(len(exported["label_revisions"]), 2)

    def test_metrics_with_no_labeled_data_are_not_zero(self):
        response = self.client.get("/api/v1/evaluations/", **self.analyst)
        self.assertIsNone(response.data["metrics"]["recall"])
        self.assertEqual(response.data["labeled_assessed"], 0)
        self.assertEqual(self.client.get("/api/v1/evaluations/?threshold=nan", **self.analyst).status_code, 400)


class LegacyMigrationTests(TransactionTestCase):
    def test_existing_rows_receive_sessions_without_invented_timing(self):
        executor = MigrationExecutor(connection)
        old = [("tracker", "0001_initial")]
        current = [("tracker", "0002_session_pipeline")]
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            Event = apps.get_model("tracker", "TelemetryEvent")
            for index in range(2):
                Event.objects.create(session_id="old-browser-session", event_type="move", x=index, y=10, ts=1000 + index)
            executor = MigrationExecutor(connection)
            executor.migrate(current)
            session = Session.objects.get(pk="old-browser-session")
            self.assertEqual(session.schema_version, 0)
            self.assertEqual(session.event_count, 2)
            events = list(session.events.all())
            self.assertEqual(len({event.event_id for event in events}), 2)
            self.assertTrue(all(event.seq is None and event.elapsed_ms is None for event in events))
        finally:
            MigrationExecutor(connection).migrate(current)
