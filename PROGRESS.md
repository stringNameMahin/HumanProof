# HumanProof submission progress

This file is the single source of truth for finishing the HumanProof course
project (BCSE410L). It lists what remains, what is done, and how to resume after
a session reset. Update the checkboxes and the log at the end of every work block.

## How to resume

1. `git switch submission-ready` (all submission work lives on this branch; it was
   created from `origin/main` at `ece288b`). The `aaryav_changed` branch is ignored
   by decision of the team (see Decisions).
2. Set up the environment if `.venv` is missing:
   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-fleet.txt
   npm install
   ```
3. Run the checks: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify.ps1` (108 tests).
4. Start the demo: `powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1` (stop: `stop_demo.ps1`).
5. Read **Current focus** below, then continue with the first unchecked task.
6. Append a dated entry to the **Session log** when you stop.

All implementation phases (1-10) are done. What remains depends on genuine human data, which only the team
can provide (Phase 11). The local database (`backend/db.sqlite3`, gitignored) already holds the seeded bot
sessions from all fleet profiles.

**Current focus:** Phase 11 H1 (team records and labels human sessions), then H2-H5.

## Scope reference

The brief (`hpdets.pdf`, section 5.4) asks for a four-layer detector fused by a
trained ensemble called BotBiometricNet:

| Layer | Brief requirement |
| --- | --- |
| 1 Browser | Canvas, WebGL, AudioContext, font rendering, hardware concurrency, WebDriver |
| 2 Mouse | Velocity, curvature, Fitts' law compliance, micro-tremor frequency |
| 3 Timing | Inter-click latency distribution, scroll velocity profile, keyboard shortcut usage |
| 4 Network | TCP handshake timing, TLS fingerprint, HTTP/2 header ordering |
| Fusion | Ensemble classifier trained against bot frameworks (Playwright, Selenium, Puppeteer, anti-detection) |

The brief's research targets (1M sessions, 99.3% detection) are out of reach for
a course prototype. The submission demonstrates every layer, a trained and
evaluated ensemble on a pilot dataset, and honest metrics with stated limits.

## Baseline already on `main` (verified 2026-10-29: 51 tests pass)

- Server sessions, signed expiring collection tokens, bounded batches, dedup, limits
- Browser signals: WebDriver, hardware concurrency, language, timezone, screen, pointer
- Mouse: speed, acceleration, turn angle, straightness, pauses
- Timing: click intervals, burstiness, scroll speed, idle time, shortcut categories
- `rules-v1` explainable scoring with insufficient-data state
- Streamlit analyst dashboard over a protected API: charts, labels, export, evaluation
- Demo tooling: CDP Chrome bot (`scripts/browser_demo.mjs`), synthetic API bot, dataset export
- Tests: Django (12), features (18), dashboard + scripts (13), JS transport (8)

## Remaining work

Legend: `[ ]` todo, `[~]` in progress, `[x]` done (with the check that proved it).

### Phase 1: Browser layer v2 (Layer 1)
- [x] B1 Client probes in `frontend/js/browser.js`: canvas hash, WebGL vendor/renderer,
      offline audio hash, font probe, plugins/languages counts, `window.chrome`,
      user agent, permission consistency, CDP/devtools tell. Each with availability flag.
- [x] B2 Extend `BrowserSignalsSerializer` (strict) for the new fields.
- [x] B3 Server features in `ml/features.py`: headless UA, software renderer,
      UA mismatch, permission inconsistency, CDP tell, plugin/language anomalies.
- [x] B4 Tests: JS probe unit tests with stubs; Python feature tests.
  - Check: `node --test frontend/tests/*.test.js` (17 pass) and `python -m unittest ml.tests` (36 pass).
  - Note: the console Error.stack CDP probe does not fire in Chrome 156; kept as a weak signal.

### Phase 2: Mouse layer v2 (Layer 2)
- [x] M1 Sample pointer at ~60 Hz (16 ms throttle) so tremor analysis is possible.
- [x] M2 Click metadata: `press_ms` (pointerdown to click), `is_trusted`,
      target centre (`target_center_x/y`).
- [x] M3 Features: curvature, jerk, micro-jitter residual, tremor band ratio (6-14 Hz),
      Fitts' law fit (slope, intercept, r^2) from target clicks, click offset from
      target centre.
- [x] M4 Tests with synthetic human-like and bot-like traces.

### Phase 3: Timing layer v2 (Layer 3)
- [x] T1 Press-dwell stats, inter-click percentiles, time to first interaction.
- [x] T2 Scroll input source (`wheel`, `key`, `touch`, `programmatic`) to expose
      scripted scrolling; scroll velocity profile.
- [x] T3 New `key` events (key class + dwell only, never the key value) and `input`
      events (typed / paste / untyped fill) for the sign-in demo.
- [x] T4 Tests (key/input events are exercised by the lab's optional note field).

### Phase 4: Network layer (Layer 4)
- [x] N1 `NetworkObservation` model + capture on every collection request: header
      order hash, browser-header presence, client hints, UA family.
- [x] N2 Optional TLS edge (`edge/tls_edge.py`): TLS-terminating proxy that parses the
      ClientHello, computes JA3 and JA4, times accept->hello and handshake, and
      forwards HMAC-signed metadata to Django. Self-signed cert via `cryptography`.
- [x] N3 Network features + consistency rules (client-library UA, missing browser
      headers, client-hint mismatch, Chromium UA without GREASE, language mismatch).
- [x] N4 Tests: header capture, signature check, ClientHello/JA3/JA4 against spec strings, proxy round trip
      (`tests/test_edge.py`). Live: real Chrome 156 gives JA4 `t13i1517h2_8daaf6152771_...`; the
      `8daaf6152771` cipher hash matches Chrome's published JA4.
  - Limitation to document: raw TCP SYN timing/TTL needs packet capture; HTTP/2
    frame fingerprinting needs an h2 server. Both out of scope.

### Phase 5: BotBiometricNet ensemble
- [x] E1 `ml/vectorize.py`: fixed numeric vector per layer with missing indicators.
- [x] E2 `ml/model.py`: per-layer logistic models + stacked fusion; JSON artifact
      (no pickle), per-feature contributions for explanations.
- [x] E3 `manage.py train_model`: build dataset from labeled sessions, grouped splits,
      known vs unseen-framework evaluation, write model JSON + report.
- [x] E4 Inference in `CompleteView` (model when present, `rules-v2` fallback) and
      `manage.py rescore`; evaluation endpoint follows the deployed model version.
- [x] E5 Label `group` (framework or participant) stored on the session for grouped splits.
- [x] E6 Tests: tiny synthetic training run, artifact round trip, fallback behaviour.

### Phase 6: Bot fleet (adversarial data)
- [x] F1 Configurable throttle rates so the fleet can create many sessions locally.
- [x] F2 `scripts/bot_fleet.mjs` CDP profiles: `cdp-linear`, `cdp-teleport`,
      `cdp-stealth` (webdriver patched, UA cleaned, Bezier paths, jitter, dwell),
      `js-synthetic` (untrusted dispatched events).
- [x] F3 `scripts/bot_fleet.py`: `selenium-chrome`, `playwright-chrome`, and API-level `http-naive` /
      `http-forged` profiles; plus `puppeteer-chrome` in `bot_fleet.mjs`.
- [x] F4 Each run labelled `bot` with source `bot-fleet:<profile>` and group `<profile>`.

### Phase 7: Dashboard
- [x] D1 Session view: per-layer scores, fused score, top feature contributions,
      network/TLS evidence, Fitts' law chart, click offsets.
- [x] D2 Model view: model card, metrics, known vs unseen results, layer ablation,
      threshold curve.
- [x] D3 Compare view: two sessions side by side.
- [x] D4 Dashboard tests updated (12 dashboard tests).

### Phase 8: Protected sign-in demo (policy)
- [x] P1 Policy in `ml/inference.py`: observe in the lab; for task `sign-in` allow < 40 <= challenge < 75 <= block;
      insufficient evidence on a protected action is challenged.
- [x] P2 `frontend/login.html`: silent collection on a sign-in form (no credential
      values sent), decision shown on submit, simple challenge for the middle band.

### Phase 9: Verification
- [x] V1 `scripts/verify.ps1` covers all suites; `.github/workflows/ci.yml` (not yet run on GitHub).
- [~] V2 End-to-end run: fleet -> labels -> dashboard done live with screenshots; train -> rescore is
      covered by `ModelCommandTests` on fixtures and waits for real human data (H2).

### Phase 10: Demo and documentation
- [x] X1 `scripts/start_demo.ps1` (Django, Streamlit, optional edge) and
      `scripts/seed_demo.ps1` (fleet + train).
- [x] X2 README, docs/architecture.md, docs/api-contract.md, docs/demo-guide.md,
      docs/implementation-report.md (measured fleet results), placeholder docs/model-report.md, docs/images/.

### Phase 11: Needs the team (cannot be done by tooling)
- [ ] H1 Each member records about 10 lab sessions and a few sign-ins, preferably via https://127.0.0.1:8443/,
      then labels them: `manage.py label_recent --count 10 --label human --group participant-XX --source "..."`
      (it previews first; add `--yes` to apply). Target: at least 20 human sessions in total, 8 minimum.
- [ ] H2 `manage.py train_model` then `manage.py rescore`. Check `docs/model-report.md` (overwritten).
      Commit `ml/models/botbiometricnet.json` and the report so the demo works from a fresh clone.
- [ ] H3 `node scripts/screenshots.mjs` to refresh `docs/images/` with the trained model views.
- [ ] H4 Update the measured numbers in `docs/implementation-report.md` and the slides from the model report.
- [ ] H5 Push the branch, check the GitHub Actions run passes, and open a PR into `main`.

## Action needed from the team

See Phase 11. The trained model needs real human examples; generated data is never labelled human.

## Possible future work (not required for submission)

- HTTP/2 support in the edge (SETTINGS and pseudo-header order fingerprints).
- Packet-capture sensor for TCP SYN timing, TTL, and window size.
- Commercial anti-detect browsers and residential proxies in the fleet.
- Server-side verification of the sign-in challenge.

## Decisions

- 2026-10-29: Build on `main` (`ece288b`). The `aaryav_changed` branch replaced main's
  pipeline with the initial prototype; the team chose to ignore it.
- 2026-10-29: Model artifacts are JSON (coefficients and scaling), not pickles, so
  they are safe to load and easy to explain.
- 2026-10-29: Generated sessions are only ever labelled `bot`. Human examples must come
  from real people.
- 2026-10-29: `--transport mixed` alternates per profile across runs so transport is never a proxy for the
  automation family; TLS-only network flags have no missing indicator in the model for the same reason.
- 2026-10-29: `label_recent` previews and needs `--yes`, so a bot session is not mislabelled by accident.

## Session log

### 2026-10-29 (session 1)
- Audited repo: `main` has the full pipeline; `aaryav_changed` (current checkout at the
  time) had dropped it. Created branch `submission-ready` from `origin/main` (no upstream).
- Created `.venv` (Python 3.14.0) and installed pinned deps; all 51 baseline tests pass.
- Wrote this plan.
- Phases 1-3 done: browser probes (canvas/WebGL/audio/fonts/consistency), 60 Hz pointer sampling,
  curvature, jerk, lateral micro-jitter, tremor band ratio, Fitts' law fit, click offsets, approachless
  clicks, press dwell, key cadence (class only), untyped input, scroll input source.
- `rules-v2` (`ml/scoring.py`) is table-driven with per-layer scores; `ml/inference.py` is the single
  scoring entry point used by `CompleteView`.
- Network layer server side: `NetworkObservation` model (migration 0004), capture on session/telemetry/
  fingerprint/complete, HMAC-verified edge metadata (`backend/.edge-key` from `init_demo`), `ml/network.py`.
- Lab page now has 8 targets of varying size and an optional note field (never sent).
- Live check: `node scripts/browser_demo.mjs` passes; the CDP bot scores 100 (rules-v1 gave 35).
- Tests: features 36, backend 18, dashboard/scripts 13, JS 17, all passing.
- Phase 4 N2: `edge/tls_edge.py` TLS-terminating proxy (127.0.0.1:8443 -> 8000): MSG_PEEK ClientHello,
  JA3 + JA4, hello/handshake timing, HMAC-signed `X-HumanProof-Edge` header, self-signed cert in
  `edge/certs/` (gitignored). Run: `.venv\Scripts\python.exe edge\tls_edge.py`.
- Phase 5: `ml/vectorize.py`, `ml/model.py` (JSON artifact, exact contributions), `ml/training.py`
  (grouped CV, leave-one-family-out, layer ablation, rules baseline, Wilson CIs, FPR-constrained threshold),
  `ml/inference.py` (model when present else rules-v2, policy), commands `train_model` and `rescore`.
  Model path: `ml/models/botbiometricnet.json` (env `HUMANPROOF_MODEL_PATH` overrides). No model is trained
  yet: it needs real human sessions (see Action needed).
- Phase 6: `scripts/bot_fleet.mjs` + `scripts/lib/cdp.mjs`. Live results (rules-v2): cdp-linear 100,
  cdp-teleport 100, js-synthetic 100, **cdp-stealth 0** (evades every rule; this is what the trained model must
  catch). Run with Django started with `HUMANPROOF_SESSION_RATE=1000/hour`.
- Tests now: features+model 44, backend 20, dashboard/scripts/edge 18, JS 17, all passing.
- Phase 6 F3: `scripts/bot_fleet.py` (selenium-chrome, playwright-chrome, http-naive, http-forged; optional deps
  in `requirements-fleet.txt`). `http-forged` fakes every client signal; only the network layer catches it (55).
- Phase 8: `/login/` protected sign-in (`frontend/login.html`, `js/login.js`, shared `js/render.js`); fleet
  `--page sign-in`. Live: cdp-linear and js-synthetic blocked, cdp-stealth allowed by rules.
- Phase 7: dashboard views Sessions (layer bars, decision badge, model contributions, browser and network
  expanders, Fitts chart), Compare, Evaluation (score histogram + ROC), Model (card, metrics, unseen families,
  calibration, fusion weights, coefficients, rule catalogue). `?view=` URL parameter. API `GET /api/v1/model/`.
- Phase 10 X1: `scripts/start_demo.ps1` / `stop_demo.ps1` / `seed_demo.ps1`, `scripts/screenshots.mjs`,
  `manage.py label_recent` for bulk-labelling a participant's own sessions.
- requirements.txt now pins numpy, scikit-learn, cryptography.
- Tests: features+model 44, backend 21, tests/ 25 (dashboard 12, scripts 8, edge 5), JS 17.
- Arms race (brief objective 3): the model report and dashboard now show each bot family's detection while
  unseen next to its detection after retraining (`family_detection` in the known-framework CV).
- Puppeteer: root `package.json` (optional `puppeteer-core` 25.14.0, `npm install`), profile `puppeteer-chrome`
  in `scripts/bot_fleet.mjs` (lab and sign-in). Live: lab 100; sign-in finished too fast -> insufficient ->
  challenged by policy.
- Model features added: `shortcut_count` (timing), `header_count_mean` (network).
- Docs written: README, docs/architecture.md, docs/api-contract.md, docs/demo-guide.md, placeholder
  docs/model-report.md (overwritten by train_model). Pending: docs/implementation-report.md with fleet numbers.
- `scripts/verify.ps1` passes: 107 tests + script syntax checks.
- Seeded the local database with two fleet rounds: 80 labelled bot sessions from 10 sources (all 9 profiles plus
  the verification runner), both transports per profile. rules-v2 flags 54 of 73 scorable sessions (74%) at 60;
  misses are cdp-stealth (0 of 12) and http-forged (score 50-55, challenged on sign-in). Table in
  docs/implementation-report.md.
- Fixed: `--transport mixed` now alternates per profile across runs (was confounded with the family);
  `.gitignore` had no trailing newline (node_modules entry was glued to test.md); `label_recent` now previews
  and needs `--yes`; dashboard Compare defaults to a scored human vs a scored bot and never the same session.
- Screenshots committed in docs/images/ (lab, sign-in, dashboard sessions/compare/evaluation/model).
- Final: `scripts/verify.ps1` passes with 108 tests; `node scripts/browser_demo.mjs` passes (4 layers, no JS
  errors, no overflow at 1365 and 390 px). Nothing is committed yet: all work is uncommitted on
  `submission-ready`. Remaining work is Phase 11 (needs the team's human sessions).
- Presentation material (claude.ai, private until shared): slide deck "HumanProof — Project Presentation"
  https://claude.ai/artifact/UHgNNrcazogpbGYHid3zxw (15 slides, speaker notes; slide 12 has [bracket] blanks to fill
  from docs/model-report.md after H2) and presenter guide doc https://claude.ai/artifact/Dr82TfSJWg1DDD8QiGkKKf
  (scripts per presenter, demo commands, Q&A, fallbacks). Demo processes were stopped (`scripts/stop_demo.ps1`).

- Submission report doc "HumanProof — Project Report" https://claude.ai/artifact/Xc3oWVScZThQXt2iUQFktC (abstract,
  sections 1-8, 7 figures incl. architecture and model diagrams and the rules-v2 chart, 37 IEEE references,
  appendix). Credits are a single "<Name 1> <Name 2> <Name 3>" line under the title for the team to fill.
  Section 6.5 has "[to fill]" cells for the BotBiometricNet results from docs/model-report.md after H2.

- 2026-10-30: team labelled 10 sessions human (`participant-XX`; all 12 human sessions are one volunteer, one
  laptop) and trained. Grouped CV needs >=2 participants, so `ml/training.py` now falls back to session-level
  holdout for a single participant and flags it as optimistic in the report and console (new test in `ml/tests.py`,
  45 ML tests pass). Current model botbiometricnet-v1-202610291939-846029a8, rescored 99 sessions. Results:
  73/73 bots detected held-out, 0/12 humans flagged (CI 0-24%), rules-v2 alone 74%; stealth bot 33% while unseen
  -> 100% after retraining. Deck slide 12 filled with these (version 9). To get true participant-held-out
  numbers: relabel the 12 as participant-01, record sessions from 2+ more people as participant-02/03, retrain.
  Still open: report Section 6.5 "[to fill]" cells; deck slide 14 says "a few volunteers" (now one).
