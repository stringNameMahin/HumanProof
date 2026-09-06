"""Dashboard smoke checks without a running API."""

import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import sys
from threading import Thread
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "dashboard"))

from api_client import AnalystClient, ApiError
from dashboard.app import default_analyst_key


def demo_session():
    return {
        "id": "demo-session",
        "started_at": "2026-09-06T00:00:00Z",
        "completed_at": None,
        "task": "demo",
        "state": "active",
        "label": "unknown",
        "label_source": "",
        "event_count": 30,
        "assessment": {
            "risk_score": 60,
            "status": "ready",
            "decision": "observe",
            "model_version": "rules-v1",
            "available_layers": ["browser", "mouse", "timing"],
            "reasons": [{"code": "webdriver", "message": "Automation flag", "points": 35}],
            "features": {"mouse": {"mean_speed_px_s": 10}, "browser": {"webdriver": True}},
        },
        "fingerprint": {"webdriver": True},
        "event_total": 30,
        "events": [
            {"seq": index, "event_type": "move", "elapsed_ms": index * 100,
             "x": index * 10, "y": index * 5, "metadata": {}}
            for index in range(30)
        ],
    }


class AnalystKeyTests(unittest.TestCase):
    def test_environment_key_takes_precedence(self):
        with patch.dict(os.environ, {"HUMANPROOF_ANALYST_KEY": "environment-key"}):
            with patch("dashboard.app.ANALYST_KEY_PATH") as key_path:
                self.assertEqual(default_analyst_key(), "environment-key")
                key_path.read_text.assert_not_called()

    def test_local_key_and_missing_file(self):
        with patch.dict(os.environ, {"HUMANPROOF_ANALYST_KEY": ""}):
            with patch("dashboard.app.ANALYST_KEY_PATH") as key_path:
                key_path.read_text.return_value = "local-key\n"
                self.assertEqual(default_analyst_key(), "local-key")
                key_path.read_text.assert_called_once_with(encoding="ascii")
                key_path.read_text.side_effect = FileNotFoundError
                self.assertEqual(default_analyst_key(), "")


class AnalystClientTests(unittest.TestCase):
    def test_invalid_addresses_raise_api_errors(self):
        for address in ["http://[broken", "http://localhost:wrong", "http://:", "file:///tmp", "http://localhost:0"]:
            with self.subTest(address=address):
                with self.assertRaises(ApiError):
                    AnalystClient(address, "test-only-key")

    def test_redirects_do_not_forward_the_analyst_key(self):
        received = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get("X-Analyst-Key")))
                if self.path.startswith("/redirect/"):
                    self.send_response(int(self.path.split("/")[-1]))
                    self.send_header("Location", f"http://localhost:{self.server.server_port}/destination/")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), RedirectHandler) as server:
            worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            worker.start()
            try:
                client = AnalystClient(f"http://127.0.0.1:{server.server_port}", "test-only-key")
                for code in [301, 302, 303, 307, 308]:
                    with self.subTest(code=code):
                        with self.assertRaisesRegex(ApiError, "redirected"):
                            client.request(f"/redirect/{code}")
                self.assertEqual(received, [
                    (f"/redirect/{code}", "test-only-key") for code in [301, 302, 303, 307, 308]
                ])
            finally:
                server.shutdown()
                worker.join(timeout=2)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "HUMANPROOF_ANALYST_KEY": "demo-key",
            "HUMANPROOF_API_URL": "http://127.0.0.1:8000",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.session = demo_session()
        self.calls = []
        self.empty = False
        request = patch.object(AnalystClient, "request", autospec=True, side_effect=self.api_request)
        self.request = request.start()
        self.addCleanup(request.stop)
        self.app = AppTest.from_file(str(PROJECT_ROOT / "dashboard" / "app.py"), default_timeout=30)

    def api_request(self, client, route, params=None, payload=None):
        self.calls.append((route, params, payload))
        if route == "/api/v1/sessions/":
            return {"count": 0, "results": []} if self.empty else {"count": 1, "results": [copy.deepcopy(self.session)]}
        if route.endswith("/labels/"):
            self.session["label"] = payload["label"]
            self.session["label_source"] = payload["source"]
            return payload
        if route == "/api/v1/evaluations/":
            return {
                "threshold": params["threshold"], "total_labeled": 1, "labeled_assessed": 1,
                "excluded_insufficient": 0,
                "confusion_matrix": {"tp": 1, "tn": 0, "fp": 0, "fn": 0},
                "metrics": {"precision": 1, "recall": 1, "accuracy": 1, "false_positive_rate": None},
                "model_version": "rules-v1", "limitations": ["Small demonstration sample."],
            }
        return copy.deepcopy(self.session)

    def field(self, kind, label):
        return next(item for item in getattr(self.app, kind) if item.label == label)

    def run_app(self):
        self.app.run()
        self.assertFalse(self.app.exception)

    def test_empty_sessions_missing_key_and_api_failure(self):
        self.empty = True
        self.run_app()
        self.assertTrue(any("No sessions" in item.value for item in self.app.info))
        self.field("text_input", "Analyst key").set_value("")
        self.request.reset_mock()
        self.run_app()
        self.request.assert_not_called()
        self.assertTrue(any("Enter the analyst key" in item.value for item in self.app.info))
        self.field("text_input", "Analyst key").set_value("bad-key")
        self.request.side_effect = ApiError("Access denied.")
        self.run_app()
        self.assertTrue(any("Access denied" in item.value for item in self.app.error))

    def test_charts_filters_and_event_pagination(self):
        self.run_app()
        self.assertEqual(len(self.app.get("vega_lite_chart")), 3)
        self.assertTrue(any(item.label == "Bot risk score (0-100)" and item.value == "60" for item in self.app.metric))
        self.assertFalse(any(route.endswith("/export/") for route, _, _ in self.calls))
        self.field("number_input", "Session page").set_value(2)
        self.run_app()
        self.field("selectbox", "Filter label").set_value("bot")
        self.run_app()
        listing_calls = [params for route, params, _ in self.calls if route == "/api/v1/sessions/"]
        self.assertEqual(listing_calls[-1]["label"], "bot")
        self.assertEqual(listing_calls[-1]["page"], 1)
        self.field("number_input", "Event page").set_value(2)
        self.run_app()
        detail_calls = [params for route, params, _ in self.calls if route == "/api/v1/sessions/demo-session/"]
        self.assertEqual(detail_calls[-1], {"page": 2, "page_size": 500})

    def test_label_export_and_evaluation(self):
        self.run_app()
        self.field("selectbox", "Label").set_value("bot")
        self.field("button", "Save label").click()
        self.run_app()
        self.assertTrue(any("Enter how this label" in item.value for item in self.app.error))
        self.field("text_input", "Label source").set_value("Controlled local browser")
        self.field("button", "Save label").click()
        self.run_app()
        self.assertEqual(self.session["label"], "bot")
        self.assertEqual(self.session["label_source"], "Controlled local browser")
        self.field("button", "Prepare JSON export").click()
        self.run_app()
        self.assertTrue(any(route.endswith("/export/") for route, _, _ in self.calls))
        self.assertEqual(len(self.app.get("download_button")), 1)
        self.field("radio", "View").set_value("Evaluation")
        self.run_app()
        self.assertTrue(any(item.label == "Human false positive rate" and item.value == "Unavailable" for item in self.app.metric))
        self.assertTrue(any("not a frozen test set" in item.value for item in self.app.warning))
        self.field("slider", "Bot risk threshold").set_value(75)
        self.run_app()
        self.assertEqual(self.calls[-1], ("/api/v1/evaluations/", {"threshold": 75}, None))


if __name__ == "__main__":
    unittest.main()
