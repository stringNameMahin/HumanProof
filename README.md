# HumanProof

HumanProof is a local bot-detection demonstration with three client-side signal
layers, explainable rule scoring, and an analyst dashboard.

```text
Browser demo -> Django API -> SQLite sessions -> Python features and rules
                                   |                       |
                                   +---- Analyst API <-----+
                                              |
                                          Streamlit
```

The browser records basic environment signals, pointer movement, click timing,
scroll activity, focus changes, and coarse shortcut categories. It does not
collect typed text, passwords, or microphone audio.

This release is an observation-only prototype. The network layer, trained
BotBiometricNet ensemble, and research-scale evaluation remain planned work.
Scores are experimental risk points, not probabilities or proof of humanity.

## Setup

Use Python 3.12 or newer. This checkout was verified with Python 3.14 and the
direct dependency versions pinned in requirements.txt. Node.js 22 or newer is
needed only for collector tests and the optional Chrome demonstration runner.

Run from the project directory:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe backend\manage.py migrate
.venv\Scripts\python.exe backend\manage.py init_demo
```

The last command creates an ignored local analyst key in backend/.analyst-key.
Both Django and Streamlit load it automatically at startup. Existing keys are
preserved. Restart the API after creating or changing the key.

For an existing database, make a backup before migration. The migration
preserves legacy events, creates related sessions, and marks their schema as 0;
it does not invent missing coordinates, timing precision, or assessments.

## Run

In the first terminal:

```powershell
.venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000. Select **Start session**, move the pointer and use the
targets, scroll the reading panel, and try the optional preference form.
Interact for at least 10 seconds, then select **Finish and assess**. Uploads
happen automatically every 3 seconds or when a batch reaches 200 events.

In a second terminal:

```powershell
.venv\Scripts\python.exe -m streamlit run dashboard\app.py --server.address 127.0.0.1 --server.headless true
```

Open http://127.0.0.1:8501. Refresh the session list to inspect the latest
assessment. Labels require a human-entered source description and keep revision
history. Evaluation includes only labeled, ready assessments and reports
excluded sessions and missing denominators.

See [the presentation guide](docs/demo-guide.md) for a five-minute walkthrough.

## Demo automation and exports

Run a controlled Chrome session through the actual browser collectors:

```powershell
node scripts/browser_demo.mjs
```

The runner uses an isolated headless Chrome profile, interacts with the demo,
finishes the session, and labels it as a controlled bot run. It saves screenshots
and a report under ignored test-results. It does not use your personal browser
profile. Chrome must be installed; use the runner's --help for options.

For a small API pipeline fixture without a browser:

```powershell
.venv\Scripts\python.exe scripts\demo_bot.py --count 1
```

This generates explicitly synthetic bot telemetry and labels its provenance.
It never generates or labels human examples. These fixtures are not evidence
of real-world detection accuracy.

Export sessions and a checksum manifest:

```powershell
.venv\Scripts\python.exe scripts\export_dataset.py
```

Exports stay under ignored data/exports. Pause collection and finish sessions
before exporting a stable snapshot. The manifest includes session/event counts,
label sources, and synthetic counts. Grouped train/validation/test splitting and
model training are not yet implemented.

## Features delivered

| Area | Behavior |
| --- | --- |
| Session ingestion | Server IDs, expiring scoped tokens, bounded batches, strict validation, atomic writes and duplicate prevention |
| Browser | WebDriver, concurrency, language/timezone, viewport/screen, pixel ratio, input capabilities and availability |
| Mouse | Paths, speed, acceleration, turning angles, pauses and straightness |
| Timing | Click intervals, burstiness, scroll speed/acceleration, idle periods and shortcut counts |
| Assessment | Versioned shared feature code, rule reasons, missing-layer handling and insufficient-data outcomes |
| Dashboard | Protected API access, pagination, charts, labels, JSON export and threshold comparisons |
| Demo tooling | Controlled browser run, synthetic fixture, dataset export and automated verification |

The baseline needs at least 20 interaction events spanning 5 seconds. It adds 35
points for a reported WebDriver signal, 25 for a long nearly straight path with
uniform speed, and 25 for very regular clicks. Missing mouse activity does not
automatically increase risk. The default evaluation threshold of 60 has not
been validated against a representative dataset.

## Verification

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify.ps1
```

Checks include migrations, legacy data preservation, token scope, invalid
input, retry deduplication, completion, deterministic features, missing data,
dashboard flows, exports, and collector transport behavior. The browser
runner additionally exercises the live collection-to-assessment pipeline.

## Configuration and limits

The default bind addresses are local only. Optional environment variables:

- DJANGO_DEBUG: defaults to 1 for the local demo.
- DJANGO_ALLOWED_HOSTS: defaults to localhost addresses.
- DJANGO_SECRET_KEY: required when disabling debug mode.
- HUMANPROOF_ANALYST_KEY: overrides the local key file for API, dashboard and CLI.
- DJANGO_CORS_ORIGINS: optional comma-separated allowed origins.

.env.example lists names; it is not automatically loaded. Set variables in
your shell when overriding defaults.

Requests are limited to 256 KB and 200 events per batch. A session holds up to
20,000 events and 1,000 batches; a browser buffer holds up to 2,000 events.
Creation is throttled to 60 sessions per IP per hour and ingestion to 240 requests
per IP per minute. These in-process limits support a local demo, not a
distributed production deployment.

The browser retains unacknowledged batches in memory and retries transient
failures. On a failed finish, keep that tab open and select **Retry finish**.
Uploads at tab closure are best effort; a durable offline queue is not provided.

Raw evidence is protected by the analyst key and excluded from Git. Production
hosting, stronger multi-user authorization, retention jobs, backup automation,
load testing, network instrumentation, and validated models remain future work.

See [the API contract](docs/api-contract.md) for routes and error behavior.
