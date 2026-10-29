# HumanProof

**An adversarially tested bot and scraping detector built on four layers of behavioural and environment
evidence, fused by an explainable ensemble (BotBiometricNet).**

BCSE410L Cyber Security project. Team: Aaryav Rastogi (23BCE0083), Mahin Pramod Dhoke (23BCE0014),
Shivam Chandra (23BCE0081).

CAPTCHAs interrupt people and are increasingly solved by automation. HumanProof instead observes *how* a page
is used and *what* is using it, then explains its decision. It runs as a local, self-contained prototype:

```text
 Browser (lab or protected sign-in)                        Bot fleet (CDP, Selenium, Playwright, HTTP)
   collectors.js  browser.js  sdk.js                           |
        | batches, fingerprint                                 |
        v                                                      v
 [ TLS edge :8443 ]--- JA3/JA4, handshake timing (HMAC-signed) --->  Django REST API :8000
                                                                     |  sessions, events, network observations
                                                                     v
                                                              SQLite  --->  ml/  features -> rules-v2 + BotBiometricNet
                                                                     |                     |
                                                                     +---- analyst API <---+--- policy (allow/challenge/block)
                                                                                 |
                                                                          Streamlit dashboard :8501
```

## What it detects

| Layer | Evidence collected | Example signals |
| --- | --- | --- |
| 1 Browser environment | Canvas, WebGL, offline-audio and font probes (hashed), hardware, screen, permissions, plugins | `navigator.webdriver`, headless user agent, software renderer, impossible window geometry, permission contradictions |
| 2 Mouse dynamics | 60 Hz pointer samples, click positions with target geometry | Speed, acceleration, jerk, curvature, sideways micro-jitter, tremor-band power (6-14 Hz), **Fitts' law fit**, click offset from target centre, clicks with no approach |
| 3 Interaction timing | Click press duration, click/scroll/key timing, input source | Instant press-release, metronomic clicks or typing, scrolling without any input device, text appearing without key presses, script-generated (untrusted) events |
| 4 Network | Request headers on every collection call; ClientHello through the TLS edge | HTTP-library user agents, missing browser headers or client hints, user agent disagreeing with the page, **JA4/JA3 TLS fingerprints**, Chromium user agent without GREASE |

Two scorers share the same features:

- **rules-v2**: 22 transparent rules, each tagged with its layer. Always computed, always shown as reasons.
- **BotBiometricNet**: one logistic model per layer plus a stacked fusion layer trained on out-of-fold layer
  outputs. Saved as JSON coefficients, so it loads without executing code and every score decomposes exactly
  into per-feature contributions. Used automatically once trained.

The **protected sign-in** applies a policy to the score: allow below 40, challenge from 40, block from 75, and
challenge whenever there is not enough evidence. The interaction lab only observes.

Privacy: typed characters, passwords, audio and camera are never collected. Key events record only the *kind* of
key (character, space, navigation...) and timing; password fields are skipped entirely. Fingerprint probes are
sent as SHA-256 hashes.

## Quick start (Windows, PowerShell)

Requires Python 3.12+ (tested on 3.14), Node.js 22+ (tested on 24), and Chrome or Edge for the bot fleet.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
```

`start_demo.ps1` migrates the database, creates the local analyst and edge keys (`backend/.analyst-key`,
`backend/.edge-key`, both gitignored), and starts three minimised windows:

| Service | URL |
| --- | --- |
| Interaction lab | http://127.0.0.1:8000/ |
| Protected sign-in | http://127.0.0.1:8000/login/ |
| Same pages through the TLS edge | https://127.0.0.1:8443/ (self-signed; accept the warning once) |
| Analyst dashboard | http://127.0.0.1:8501/ |

Stop everything with `scripts\stop_demo.ps1`. On macOS/Linux run the same three commands by hand:
`python backend/manage.py runserver`, `python edge/tls_edge.py`, `python -m streamlit run dashboard/app.py`.

## Building the dataset and training BotBiometricNet

1. **Bots.** With the demo running:
   ```powershell
   .venv\Scripts\python.exe -m pip install -r requirements-fleet.txt   # Selenium + Playwright, optional
   powershell -ExecutionPolicy Bypass -File scripts\seed_demo.ps1 -Runs 4
   ```
   This runs eight automation profiles against the lab and the sign-in page and labels every run `bot`, grouped
   by profile: `cdp-linear`, `cdp-teleport`, `cdp-stealth` (Bezier paths, Fitts-like timing, jitter, cleaned
   user agent: an evasive bot), `js-synthetic`, `selenium-chrome`, `playwright-chrome`, `http-naive`, and
   `http-forged` (fakes every client signal).
2. **Humans.** Each team member completes the lab (and the sign-in page) about ten times, ideally through
   https://127.0.0.1:8443 so the TLS layer is present, then labels their own sessions:
   ```powershell
   .venv\Scripts\python.exe backend\manage.py label_recent --count 10 --label human --group participant-mp --source "team member, desktop mouse" --yes
   ```
   Generated data is never labelled human by any tool in this repository.
3. **Train, evaluate, deploy:**
   ```powershell
   .venv\Scripts\python.exe backend\manage.py train_model
   .venv\Scripts\python.exe backend\manage.py rescore
   ```
   `train_model` refuses to run with fewer than 8 human and 8 bot sessions. It writes
   `ml/models/botbiometricnet.json` and `docs/model-report.md`, which report grouped cross-validation (no
   participant on both sides), leave-one-framework-out results, per-layer ablation, the rules baseline, and
   95% confidence intervals. The running API picks up the new model without a restart.

## Demo

See [docs/demo-guide.md](docs/demo-guide.md) for a timed walkthrough. The short version:

- Open the sign-in page, sign in normally: **allowed**. Run
  `node scripts/bot_fleet.mjs --profiles cdp-linear --page sign-in`: **blocked**, with reasons.
- In the dashboard, open **Compare** to put the two sessions side by side, then **Model** for the trained
  ensemble's held-out results.

## Verification

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify.ps1
```

Runs Django checks and migration consistency, backend API tests, feature/model tests, dashboard, script and TLS
edge tests, JavaScript collector tests, and syntax checks of the browser scripts (108 automated tests). The same
suite runs in GitHub Actions (`.github/workflows/ci.yml`). `node scripts/browser_demo.mjs` additionally drives
real Chrome through the whole pipeline and saves screenshots under `test-results/`.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, every feature and rule, the fusion maths, the TLS edge |
| [docs/api-contract.md](docs/api-contract.md) | Endpoints, payloads, limits, errors |
| [docs/demo-guide.md](docs/demo-guide.md) | Presentation walkthrough for three presenters |
| [docs/implementation-report.md](docs/implementation-report.md) | Brief-to-implementation mapping, measured results, limitations |
| [docs/model-report.md](docs/model-report.md) | Generated by `train_model` |
| [PROGRESS.md](PROGRESS.md) | Work log and remaining items |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_DEBUG` | `1` | Local demo mode; set `DJANGO_SECRET_KEY` when disabling |
| `DJANGO_ALLOWED_HOSTS` | localhost addresses | Allowed host names |
| `HUMANPROOF_ANALYST_KEY` | `backend/.analyst-key` | Key for the analyst API, dashboard and scripts |
| `HUMANPROOF_EDGE_KEY` | `backend/.edge-key` | HMAC key shared by the TLS edge and Django |
| `HUMANPROOF_SESSION_RATE` / `HUMANPROOF_INGEST_RATE` | `60/hour`, `240/min` | Per-IP throttles (the launcher raises them for the fleet) |
| `HUMANPROOF_MODEL_PATH` | `ml/models/botbiometricnet.json` | Model artifact location |

## Limitations

This is a research prototype on a pilot dataset, not a production defence. Client-side signals can be forged
(the `http-forged` profile shows how far, and that the network layer still catches it). The TLS edge sees the
ClientHello but not raw TCP SYN timing or TTLs, which need packet capture, nor HTTP/2 frame fingerprints, which
need an h2 server. Commercial anti-detect browsers were not available to test. Metrics come from a small number
of volunteers and local automation profiles and carry wide confidence intervals; the brief's research targets
(1M sessions, 99.3% detection) are out of scope. The edge certificate is self-signed and the analyst key is a
single shared secret, both suitable only for a local demo.
