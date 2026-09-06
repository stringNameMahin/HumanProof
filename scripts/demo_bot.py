"""Submit synthetic API fixtures to exercise the local HumanProof pipeline."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


REPO_DIR = Path(__file__).resolve().parents[1]
FIXTURE_SOURCE = "synthetic-api-fixture: demo_bot v1"


class ApiError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def base_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("Use an HTTP or HTTPS base URL without credentials or query parameters.")
    return value.rstrip("/")


def analyst_key():
    value = os.environ.get("HUMANPROOF_ANALYST_KEY", "").strip()
    key_path = REPO_DIR / "backend" / ".analyst-key"
    if not value and key_path.is_file():
        value = key_path.read_text(encoding="ascii").strip()
    return value


class ApiClient:
    def __init__(self, root_url):
        self.root_url = root_url
        self.opener = build_opener(NoRedirects())

    def request(self, path, payload=None, headers=None, retries=0):
        body = json.dumps(payload, ensure_ascii=True).encode("ascii") if payload is not None else None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(self.root_url + path, data=body, headers=request_headers)
        for attempt in range(retries + 1):
            try:
                with self.opener.open(request, timeout=20) as response:
                    return json.load(response)
            except HTTPError as error:
                retryable = error.code in {429, 502, 503, 504}
                if not retryable or attempt == retries:
                    raise ApiError(f"API returned HTTP {error.code} for {path}.") from None
            except (URLError, TimeoutError, ConnectionError):
                if attempt == retries:
                    raise ApiError("Cannot reach the API. Check the base URL and start the backend.") from None
            except (ValueError, UnicodeError):
                raise ApiError("The API returned an unreadable JSON response.") from None
            time.sleep(0.5 * (2 ** attempt))


def fixture_events():
    start_ms = int(time.time() * 1000) - 6000
    events = []
    for index in range(121):
        events.append({
            "event_type": "move", "x": 80 + index * 6, "y": 240,
            "elapsed_ms": index * 50,
            "metadata": {"pointer_type": "mouse", "viewport_width": 1280, "viewport_height": 720},
        })
    for index in range(8):
        elapsed = 300 + index * 750
        events.append({
            "event_type": "click", "x": 80 + elapsed * 0.12, "y": 240,
            "elapsed_ms": elapsed,
            "metadata": {"pointer_type": "mouse", "target_width": 60, "target_height": 36},
        })
    events.sort(key=lambda event: event["elapsed_ms"])
    for index, event in enumerate(events):
        event.update({"event_id": str(uuid.uuid4()), "seq": index,
                      "ts": start_ms + event["elapsed_ms"]})
    return events


def submit_fixture(client, key):
    created = client.request("/api/v1/sessions/", {"task": "mixed-demo", "schema_version": 1})
    session_id = created["session_id"]
    token_headers = {"X-Collection-Token": created["collection_token"]}
    client.request("/api/v1/fingerprints/", {
        "session_id": session_id, "schema_version": 1,
        "signals": {
            "webdriver": True, "hardware_concurrency": 4, "language": "en-US",
            "timezone": "UTC", "viewport_width": 1280, "viewport_height": 720,
            "screen_width": 1280, "screen_height": 720, "pixel_ratio": 1,
            "max_touch_points": 0, "pointer_type": "fine",
        },
    }, token_headers, retries=2)
    events = fixture_events()
    for offset in range(0, len(events), 200):
        batch = {"session_id": session_id, "batch_id": str(uuid.uuid4()),
                 "schema_version": 1, "events": events[offset:offset + 200]}
        client.request("/api/v1/telemetry/", batch, token_headers, retries=2)
    assessment = client.request(f"/api/v1/sessions/{session_id}/complete/", {}, token_headers, retries=2)
    label = "unknown (no analyst key)"
    if key:
        try:
            client.request(f"/api/v1/sessions/{session_id}/labels/",
                           {"label": "bot", "source": FIXTURE_SOURCE}, {"X-Analyst-Key": key})
            label = "bot; source=" + FIXTURE_SOURCE
        except ApiError as error:
            label = "unknown (label request failed)"
            print(str(error), file=sys.stderr)
    print(f"Synthetic API fixture {session_id}: {len(events)} events; "
          f"status={assessment.get('status')}; score={assessment.get('risk_score')}; label={label}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Submit clearly synthetic automation fixtures to the local HumanProof API.",
        epilog="This tests ingestion, scoring, and the dashboard. It does not run a browser or measure bot detection accuracy. "
               "Labels use HUMANPROOF_ANALYST_KEY or backend/.analyst-key when available.",
    )
    parser.add_argument("--base-url", type=base_url, default="http://127.0.0.1:8000",
                        help="Backend address (default: http://127.0.0.1:8000).")
    parser.add_argument("--count", type=int, default=1, help="Number of synthetic sessions, from 1 to 20.")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 20:
        parser.error("--count must be between 1 and 20.")
    print("Synthetic API fixtures only. No real browser runs or human examples are generated.")
    try:
        client, key = ApiClient(args.base_url), analyst_key()
        for _ in range(args.count):
            submit_fixture(client, key)
    except (ApiError, OSError, UnicodeError, KeyError) as error:
        message = str(error) if isinstance(error, ApiError) else "Cannot read local configuration or an API response."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
