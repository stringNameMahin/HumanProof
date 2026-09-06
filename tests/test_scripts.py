import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
import uuid

from ml import score_session
from scripts.demo_bot import FIXTURE_SOURCE, REPO_DIR, fixture_events, submit_fixture
from scripts.export_dataset import ApiError, export_sessions, output_directory


class FixtureTests(unittest.TestCase):
    def test_fixture_is_ordered_unique_and_reaches_the_demo_threshold(self):
        identifiers = [uuid.UUID(int=index + 1) for index in range(129)]
        with patch("scripts.demo_bot.time.time", return_value=1700000000):
            with patch("scripts.demo_bot.uuid.uuid4", side_effect=identifiers):
                events = fixture_events()
        self.assertEqual(len(events), 129)
        self.assertLessEqual(len(events), 200)
        self.assertEqual(sum(event["event_type"] == "move" for event in events), 121)
        self.assertEqual(sum(event["event_type"] == "click" for event in events), 8)
        self.assertEqual([event["seq"] for event in events], list(range(129)))
        self.assertEqual(len({event["event_id"] for event in events}), 129)
        times = [event["elapsed_ms"] for event in events]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[-1] - times[0], 6000)
        for event in events:
            self.assertEqual(event["ts"], 1699999994000 + event["elapsed_ms"])
            self.assertIsInstance(uuid.UUID(event["event_id"]), uuid.UUID)
        assessment = score_session(events, {"webdriver": True})
        self.assertEqual(assessment["status"], "ready")
        self.assertGreaterEqual(assessment["risk_score"], 60)
        self.assertEqual(assessment["decision"], "observe")

    def test_fixture_label_preserves_synthetic_provenance_without_printing_keys(self):
        client = Mock()
        client.request.side_effect = [
            {"session_id": "fixture-session", "collection_token": "private-token"},
            {}, {}, {"status": "ready", "risk_score": 85}, {},
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            submit_fixture(client, "private-analyst-key")
        label_call = client.request.call_args_list[-1]
        self.assertEqual(label_call.args[0], "/api/v1/sessions/fixture-session/labels/")
        self.assertEqual(label_call.args[1], {"label": "bot", "source": FIXTURE_SOURCE})
        self.assertIn("Synthetic API fixture", output.getvalue())
        self.assertIn(FIXTURE_SOURCE, output.getvalue())
        self.assertNotIn("private-token", output.getvalue())
        self.assertNotIn("private-analyst-key", output.getvalue())

    def test_missing_analyst_key_does_not_create_a_label(self):
        client = Mock()
        client.request.side_effect = [
            {"session_id": "fixture-session", "collection_token": "private-token"},
            {}, {}, {"status": "ready", "risk_score": 85},
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            submit_fixture(client, "")
        self.assertEqual(client.request.call_count, 4)
        self.assertIn("unknown (no analyst key)", output.getvalue())


class DatasetExportTests(unittest.TestCase):
    def setUp(self):
        parent = output_directory("data/exports")
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="test-scripts-", dir=parent)
        self.destination = Path(self.temporary.name).resolve()
        self.assertTrue(self.destination.is_relative_to(REPO_DIR))
        self.addCleanup(self.temporary.cleanup)

    def test_pagination_checksum_and_label_provenance(self):
        pages = []

        def response(path, **kwargs):
            self.assertEqual(kwargs["headers"], {"X-Analyst-Key": "test-secret"})
            if "?" in path:
                page = int(parse_qs(urlsplit(path).query)["page"][0])
                pages.append(page)
                indices = range(100) if page == 1 else (99, 100)
                return {"count": 102, "results": [{"id": str(index)} for index in indices]}
            index = int(path.split("/")[-3])
            return {
                "id": str(index), "label": "bot" if index == 0 else "human" if index == 1 else "unknown",
                "label_source": FIXTURE_SOURCE if index == 0 else "reviewed-test-record" if index == 1 else "",
                "events": [{"event_type": "click"}], "truncated": False,
            }

        client = Mock()
        client.request.side_effect = response
        captured = io.StringIO()
        with redirect_stdout(captured):
            manifest = export_sessions(client, "test-secret", self.destination)
        data_path = next(self.destination.glob("*/sessions.jsonl"))
        data = data_path.read_bytes()
        saved_manifest = json.loads((data_path.parent / "manifest.json").read_text(encoding="ascii"))
        self.assertEqual(pages, [1, 2])
        self.assertEqual(manifest, saved_manifest)
        self.assertEqual(manifest["session_count"], 101)
        self.assertEqual(manifest["event_count"], 101)
        self.assertEqual(manifest["label_counts"], {"human": 1, "bot": 1, "unknown": 99})
        self.assertEqual(manifest["synthetic_labeled_session_count"], 1)
        self.assertEqual(manifest["label_source_counts"][FIXTURE_SOURCE], 1)
        self.assertEqual(manifest["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(len(data.splitlines()), 101)
        self.assertFalse(list(self.destination.glob("*/*.partial")))
        self.assertNotIn(b"test-secret", data)
        self.assertNotIn("test-secret", captured.getvalue())
        self.assertNotIn("test-secret", json.dumps(manifest))

    def test_failed_export_remains_partial_without_a_manifest(self):
        client = Mock()
        client.request.side_effect = ApiError("Test API failure.")
        with self.assertRaisesRegex(ApiError, "Test API failure"):
            export_sessions(client, "test-secret", self.destination)
        self.assertEqual(len(list(self.destination.glob("*/sessions.jsonl.partial"))), 1)
        self.assertFalse(list(self.destination.glob("*/manifest.json")))
        self.assertFalse(list(self.destination.glob("*/sessions.jsonl")))

    def test_export_path_cannot_escape_the_ignored_directory(self):
        for value in ("..", "data/exports/../../outside", str(REPO_DIR.parent / "outside")):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    output_directory(value)
        self.assertEqual(output_directory("data/exports/example"),
                         (REPO_DIR / "data" / "exports" / "example").resolve())


if __name__ == "__main__":
    unittest.main()
