# HumanProof API v1

The demo page and API share http://127.0.0.1:8000. All write bodies are JSON.
The original /api/telemetry/ route returns 410 with migration instructions.

## Collection

- POST /api/v1/sessions/ accepts task and schema_version=1. Tasks are mixed-demo,
  click-target, reading, or form-demo. It returns session_id, collection_token,
  and expires_in=7200. Creation is limited to 60 sessions per IP per hour.
- Send X-Collection-Token on all subsequent collection requests.
- POST /api/v1/telemetry/ accepts session_id, batch_id (UUID), schema_version=1,
  and 1-200 events. Each event has a UUID event_id, unique nonnegative seq,
  event_type, ts (epoch milliseconds), elapsed_ms (monotonic milliseconds),
  optional x/y coordinates, and allowlisted metadata.
- Events are move, click, scroll, focus, or shortcut. Move and pointer click
  require coordinates. Keyboard clicks may omit them. Metadata accepts viewport
  dimensions, pointer_type, target dimensions, focused, shortcut_category,
  scroll_y, delta_y, and scroll_source (page or panel).
- Each response acknowledges stored and duplicate counts plus batch_id. A retry
  must reuse the complete original batch. Changed content with a reused ID is
  rejected. All events in a batch succeed or the entire batch rolls back.
- POST /api/v1/fingerprints/ accepts session_id, schema_version=1 and signals.
  Signals are the browser fields collected in frontend/js/browser.js. Capability
  failures are null with explicit availability flags. Unknown fields are rejected.
- POST /api/v1/sessions/{id}/complete/ freezes collection and calculates the
  final assessment. Repeating completion returns the saved result. Retries of
  already acknowledged batches still work after completion.
- GET /api/v1/sessions/{id}/assessment/ returns a minimal scoped assessment,
  with status pending, ready, or insufficient_data. Detailed features stay in
  the analyst API. Assessments include rule and feature version identifiers.

Ingestion and completion are limited to 240 requests per IP per minute. Bodies
are limited to 256 KB, sessions to 20,000 events and 1,000 batches. Client buffers
hold at most 2,000 events; any dropped samples are shown in the demo. Final tab
closure uploads are best effort. Tokens and client signals do not prove humanity.

## Analyst access

Send X-Analyst-Key, configured by HUMANPROOF_ANALYST_KEY or backend/.analyst-key.
Run manage.py init_demo to generate the ignored local key, then restart Django.

| Method and path | Response or action |
| --- | --- |
| GET /api/v1/sessions/ | Paginated sessions; optional label and state filters |
| GET /api/v1/sessions/{id}/ | Session evidence, fingerprint, assessment and event page |
| POST /api/v1/sessions/{id}/labels/ | Set label human/bot/unknown and source; append revision |
| GET /api/v1/sessions/{id}/export/ | Session export, capped at 20,000 events with truncation flag |
| GET /api/v1/evaluations/?threshold=60 | Labeled demo confusion matrix and nullable metrics |
| GET /health/ | Public service liveness only |

Session pages accept page and page_size (default 20, maximum 100). Event pages
default to 500 and allow up to 1,000. Evaluation counts only ready rules-v1
assessments with human/bot labels, reports excluded cases, and returns null
when a metric has no denominator. It is not a frozen test-set evaluation.

Errors use HTTP 400 for invalid fields, 403 for missing/invalid scope, 404 for an
unknown session,409 for identity conflicts or completed/limited collection,
413 for oversized bodies, 429 for throttling, and 503 for missing analyst
configuration or busy SQLite storage. Clients may retry transient failures.

Schema 0 sessions were migrated from the original telemetry table. They retain
unknown labels, missing monotonic timestamps, and no fabricated assessments.
