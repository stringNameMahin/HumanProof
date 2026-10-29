import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from ml.inference import current_model_version

from .api import TOKEN_SALT
from .models import LabelRevision, NetworkObservation, Session, TelemetryBatch, TelemetryEvent


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
        Session.objects.filter(pk=self.session_id).update(assessment={"status": "ready", "risk_score": 85, "model_version": current_model_version()})
        Session.objects.create(label="human", assessment={"status": "ready", "risk_score": 10, "model_version": current_model_version()})
        Session.objects.create(label="human")
        metrics = self.client.get("/api/v1/evaluations/", **self.analyst).data
        self.assertEqual(metrics["confusion_matrix"], {"tp": 1, "tn": 1, "fp": 0, "fn": 0})
        self.assertEqual(metrics["excluded_insufficient"], 1)
        self.assertEqual(metrics["metrics"]["recall"], 1)
        exported = self.client.get(f"/api/v1/sessions/{self.session_id}/export/", **self.analyst).data
        self.assertEqual(exported["export_schema"], "humanproof-session-v2")
        self.assertEqual(len(exported["label_revisions"]), 2)

    def test_metrics_with_no_labeled_data_are_not_zero(self):
        response = self.client.get("/api/v1/evaluations/", **self.analyst)
        self.assertIsNone(response.data["metrics"]["recall"])
        self.assertEqual(response.data["labeled_assessed"], 0)
        self.assertEqual(self.client.get("/api/v1/evaluations/?threshold=nan", **self.analyst).status_code, 400)


@override_settings(HUMANPROOF_ANALYST_KEY="test-analyst-key", HUMANPROOF_EDGE_KEY="test-edge-key")
class SignalLayerApiTests(TestCase):
    browser_headers = {
        "HTTP_USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/156.0.0.0 Safari/537.36",
        "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9", "HTTP_SEC_FETCH_MODE": "cors",
        "HTTP_SEC_CH_UA": '"Chromium";v="156"',
    }

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        created = self.client.post("/api/v1/sessions/", {"task": "sign-in", "schema_version": 1},
                                   format="json", **self.browser_headers).data
        self.session_id = created["session_id"]
        self.token = created["collection_token"]
        self.auth = {"HTTP_X_COLLECTION_TOKEN": self.token, **self.browser_headers}
        self.analyst = {"HTTP_X_ANALYST_KEY": "test-analyst-key"}

    def event(self, seq, event_type="move", **metadata):
        return {"event_id": str(uuid.uuid4()), "seq": seq, "event_type": event_type, "x": 10 + seq, "y": 20,
                "ts": 1800000000000 + seq * 250, "elapsed_ms": seq * 250, "metadata": metadata}

    def upload(self, events, **headers):
        batch = {"session_id": self.session_id, "batch_id": str(uuid.uuid4()), "schema_version": 1, "events": events}
        return self.client.post("/api/v1/telemetry/", batch, format="json", **{**self.auth, **headers})

    def signed_edge(self, data, key="test-edge-key"):
        encoded = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
        signature = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        return {"HTTP_X_HUMANPROOF_EDGE": encoded, "HTTP_X_HUMANPROOF_EDGE_SIGNATURE": signature}

    def test_new_event_types_and_metadata_are_validated(self):
        events = [
            self.event(0, "click", pointer_type="mouse", press_ms=92.5, target_kind="target",
                       target_center_x=40, target_center_y=22, target_width=30, target_height=30),
            {**self.event(1, "key", key_class="char", press_ms=80), "x": None, "y": None},
            {**self.event(2, "input", input_kind="typed"), "x": None, "y": None},
            {**self.event(3, "scroll", scroll_input="wheel", scroll_y=40), "x": None, "y": None},
            self.event(4, "move", is_trusted=False),
        ]
        self.assertEqual(self.upload(events).status_code, 201)
        for private in ({"key": "a"}, {"value": "secret"}, {"key_class": "letter-a"}, {"input_kind": "password"}):
            with self.subTest(private=private):
                event = {**self.event(10, "key", **private), "x": None, "y": None}
                self.assertEqual(self.upload([event]).status_code, 400)

    def test_extended_browser_signals(self):
        signals = {"user_agent": "Mozilla/5.0", "webgl_renderer": "ANGLE", "canvas_hash": "a" * 64,
                   "plugins_count": 5, "has_chrome_object": True, "notification_permission": "default",
                   "permission_query_state": "prompt", "cdp_detected": False, "availability": {"canvas_hash": True}}
        body = {"session_id": self.session_id, "schema_version": 1, "signals": signals}
        self.assertEqual(self.client.post("/api/v1/fingerprints/", body, format="json", **self.auth).status_code, 200)
        signals["canvas_hash"] = "not-a-digest"
        self.assertEqual(self.client.post("/api/v1/fingerprints/", body, format="json", **self.auth).status_code, 400)

    def test_requests_are_observed_without_storing_secrets(self):
        self.upload([self.event(index) for index in range(25)])
        self.client.post(f"/api/v1/sessions/{self.session_id}/complete/", {}, format="json", **self.auth)
        observations = list(NetworkObservation.objects.filter(session_id=self.session_id))
        self.assertEqual([item.endpoint for item in observations], ["session", "telemetry", "complete"])
        stored = json.dumps([item.data for item in observations])
        self.assertNotIn(self.token, stored)
        self.assertIn("x-collection-token", observations[1].data["header_names"])
        self.assertEqual(observations[1].data["accept_language"], "en-US,en;q=0.9")
        self.assertIsNone(observations[1].data["tls"])
        assessment = Session.objects.get(pk=self.session_id).assessment
        self.assertTrue(assessment["features"]["network"]["available"])
        self.assertEqual(assessment["features"]["network"]["request_count"], 3)
        self.assertIn("layer_scores", self.client.get(f"/api/v1/sessions/{self.session_id}/assessment/", **self.auth).data)
        detail = self.client.get(f"/api/v1/sessions/{self.session_id}/", **self.analyst).data
        self.assertEqual(detail["network_observation_total"], 3)

    def test_only_signed_edge_metadata_is_trusted(self):
        tls = {"ja4": "t13i1516h1_aaaaaaaaaaaa_bbbbbbbbbbbb", "grease": True, "handshake_ms": 3.2}
        self.upload([self.event(0)], **self.signed_edge(tls))
        self.upload([self.event(1)], **self.signed_edge(tls, key="forged-key"))
        forged = self.signed_edge(tls)
        forged["HTTP_X_HUMANPROOF_EDGE"] = base64.urlsafe_b64encode(b'{"ja4": "spoofed"}').decode()
        self.upload([self.event(2)], **forged)
        stored = [item.data for item in NetworkObservation.objects.filter(endpoint="telemetry")]
        self.assertEqual(stored[0]["tls"], tls)
        self.assertIsNone(stored[1]["tls"])
        self.assertIsNone(stored[2]["tls"])
        self.assertFalse(any(name.startswith("x-humanproof-edge") for name in stored[0]["header_names"]))

    def test_label_groups_support_grouped_splits(self):
        url = f"/api/v1/sessions/{self.session_id}/labels/"
        body = {"label": "bot", "source": "bot-fleet: cdp-linear", "group": "cdp-linear"}
        self.assertEqual(self.client.post(url, body, format="json", **self.analyst).data["group"], "cdp-linear")
        self.assertEqual(LabelRevision.objects.get().group, "cdp-linear")
        self.assertEqual(Session.objects.get(pk=self.session_id).label_group, "cdp-linear")
        body["group"] = "spaces are not allowed"
        self.assertEqual(self.client.post(url, body, format="json", **self.analyst).status_code, 400)
        listing = self.client.get("/api/v1/sessions/", **self.analyst).data
        self.assertEqual(listing["results"][0]["label_group"], "cdp-linear")

    @override_settings(HUMANPROOF_SESSION_RATE="2/hour")
    def test_session_rate_is_configurable(self):
        cache.clear()
        statuses = [self.client.post("/api/v1/sessions/", {}, format="json").status_code for _ in range(3)]
        self.assertEqual(statuses, [201, 201, 429])


@override_settings(HUMANPROOF_ANALYST_KEY="test-analyst-key")
class ModelCommandTests(TestCase):
    """train_model -> rescore -> evaluation, on test-only fixture sessions in the test database."""

    def store(self, item, label, group):
        from ml.tests import browser_profile, observation

        session = Session.objects.create(state="complete", label=label, label_group=group,
                                         label_source=f"test fixture {group}", fingerprint=item["fingerprint"] or browser_profile())
        TelemetryEvent.objects.bulk_create([
            TelemetryEvent(session=session, seq=index, event_type=event["event_type"], x=event.get("x"), y=event.get("y"),
                           ts=1800000000000 + int(event["elapsed_ms"]), elapsed_ms=event["elapsed_ms"],
                           metadata=event.get("metadata", {}))
            for index, event in enumerate(item["events"])])
        NetworkObservation.objects.create(session=session, endpoint="telemetry", data=observation())
        return session

    def setUp(self):
        import random

        from ml.tests import acquisition_session, browser_profile

        generator = random.Random(5)
        for index in range(8):
            events = acquisition_session(lambda bits: 150 + 120 * bits + generator.uniform(-30, 30),
                                         offset=(generator.uniform(-6, 6), 4), press_ms=generator.uniform(70, 130))
            self.store({"events": events, "fingerprint": None}, "human", f"participant-{index % 4}")
        for index in range(8):
            events = acquisition_session(lambda bits: 900, offset=(0, 0), press_ms=1)
            self.store({"events": events, "fingerprint": browser_profile(webdriver=True)}, "bot", f"family-{index % 2}")
        Session.objects.create(state="complete", label="human")
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.model_path = Path(self.folder.name) / "model.json"
        environment = patch.dict(os.environ, {"HUMANPROOF_MODEL_PATH": str(self.model_path)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_train_rescore_and_evaluate(self):
        from ml.inference import current_model_version

        output = io.StringIO()
        report = Path(self.folder.name) / "report.md"
        call_command("train_model", "--min-humans", "4", "--min-bots", "4", "--output", str(self.model_path),
                     "--report", str(report), stdout=output)
        self.assertIn("8 human, 8 bot (1 skipped", output.getvalue())
        self.assertTrue(report.read_text().startswith("# BotBiometricNet model report"))
        version = current_model_version()
        self.assertTrue(version.startswith("botbiometricnet-v1-"))
        call_command("rescore", stdout=io.StringIO())
        versions = {session.assessment["model_version"] for session in Session.objects.exclude(label_group="")}
        self.assertEqual(versions, {version})
        evaluation = APIClient().get("/api/v1/evaluations/?threshold=50", HTTP_X_ANALYST_KEY="test-analyst-key").data
        self.assertEqual(evaluation["model_version"], version)
        self.assertEqual(evaluation["labeled_assessed"], 16)
        self.assertEqual(evaluation["excluded_insufficient"], 1)
        self.assertEqual(evaluation["confusion_matrix"]["tp"] + evaluation["confusion_matrix"]["fn"], 8)

    def test_training_refuses_without_enough_humans(self):
        with self.assertRaisesMessage(CommandError, "genuine human sessions"):
            call_command("train_model", "--min-humans", "50", "--output", str(self.model_path), stdout=io.StringIO())
        self.assertFalse(self.model_path.exists())


class LabelRecentTests(TestCase):
    def test_labels_only_recent_unlabelled_completed_sessions(self):
        from django.utils import timezone

        now = timezone.now()
        old = Session.objects.create(state="complete", completed_at=now - timezone.timedelta(hours=1))
        recent = [Session.objects.create(state="complete", completed_at=now - timezone.timedelta(minutes=index))
                  for index in range(3)]
        Session.objects.create(state="active")
        Session.objects.create(state="complete", label="bot", completed_at=now)
        arguments = ["label_recent", "--count", "3", "--label", "human", "--group", "participant-mp",
                     "--source", "team member, desktop mouse"]
        preview = io.StringIO()
        call_command(*arguments, stdout=preview)
        self.assertIn("repeat the command with --yes", preview.getvalue())
        self.assertFalse(Session.objects.filter(label="human").exists())
        output = io.StringIO()
        call_command(*arguments, "--yes", stdout=output)
        self.assertIn("Labelled 3 session(s) human in group participant-mp.", output.getvalue())
        for session in recent:
            session.refresh_from_db()
            self.assertEqual((session.label, session.label_group), ("human", "participant-mp"))
        old.refresh_from_db()
        self.assertEqual(old.label, "unknown")
        self.assertEqual(LabelRevision.objects.count(), 3)
        with self.assertRaisesMessage(CommandError, "Invalid label"):
            call_command("label_recent", "--count", "1", "--label", "human", "--source", "x", "--yes", stdout=io.StringIO())

class LegacyMigrationTests(TransactionTestCase):
    def test_existing_rows_receive_sessions_without_invented_timing(self):
        executor = MigrationExecutor(connection)
        old = [("tracker", "0001_initial")]
        current = [node for node in executor.loader.graph.leaf_nodes() if node[0] == "tracker"]
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
