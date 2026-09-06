# HumanProof implementation report

Verified September 6, 2026. This release implements the six core demo feature
areas and partial dataset/evaluation tools. It does not claim a measured
percentage of the full research effort.

## Completed features

| Feature | Implementation | Evidence |
| --- | --- | --- |
| Reliable session tracking | Server sessions, signed expiring collection tokens, versioned batches, bounded uploads, atomic insertion and database deduplication | Retry, token mismatch, payload conflict, limits, completion and legacy migration tests |
| Browser environment collection | Basic environment signals with null and availability handling | Live Chrome fingerprint stored and shown in dashboard |
| Mouse behavior analysis | Shared deterministic paths, speeds, acceleration, turns, pauses and straightness | Synthetic geometry, timing gap and numerical edge-case tests |
| Interaction timing | Click spacing, burstiness, idle periods, coarse shortcuts and separate page/panel scroll features | Timing tests and live scrolling/target tasks |
| Explainable assessment | Versioned observation-only rules, reasons, quality flags and insufficient-data state | Backend completion tests, restart check and controlled browser run |
| Analyst dashboard | Protected API client, pagination, trajectory/timing charts, evidence, labels and evaluation | Seven dashboard regression tests and live API rendering |

## Partial dataset and evaluation tools

- Manual human/bot/unknown labels include source text and revision history.
- The Chrome runner creates a real controlled browser session labeled bot.
- The API fixture creates explicitly synthetic bot events, never human data.
- Export tooling produces JSONL and a SHA256 manifest with actual counts.
- Evaluation shows confusion-matrix counts, precision, recall, false positives,
  and accuracy only where denominators exist. It reports excluded assessments.
- Genuine-human recruitment, grouped frozen dataset splits, model training,
  confidence intervals, and adversarial research rounds are still pending.

## Verification results

| Check | Result |
| --- | --- |
| Backend and legacy migration tests | 12 passed |
| Feature and baseline tests | 18 passed |
| Dashboard tests | 7 passed |
| Fixture and export tests | 6 passed |
| JavaScript collector/transport tests | 8 passed |
| Django system and migration consistency checks | Passed |
| Installed dependency consistency | Passed |
| ASCII scan of implementation and new documentation | Passed |
| Live Chrome collection | 120 events, all three client layers, ready assessment |
| Chrome restart and insufficient-data behavior | Passed |
| Desktop/mobile horizontal overflow | None at tested widths of 1365 and 390 pixels |
| Browser JavaScript errors and failed requests | None in the successful run |
| Live Streamlit rendering against Django | Passed |

The controlled Chrome run scored 35. At the demonstration threshold of 60,
that run would be a false negative. The deliberately regular synthetic API
fixture scored 85. These two examples validate pipeline behavior and expose
the rule baseline's limits; they do not establish a detection rate.

No human sessions were generated or labeled by the implementation tools.
Collect and label your own demonstration session before presenting comparisons.

Screenshots and the browser-run report are stored locally under test-results.
Dataset exports and the pre-migration backup are under ignored data directories.
Neither credentials nor raw telemetry is committed.

## Remaining project milestones

Network edge instrumentation, four-layer ensemble fusion, representative data,
calibration, robust known/unseen automation evaluation, and production operations
remain future work. Start with the network feasibility prototype and a diverse
labeled pilot after presenting this collection-to-assessment demo.
