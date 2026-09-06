# HumanProof presentation guide

## Five-minute walkthrough

1. Start Django and Streamlit using the README commands.
2. Open http://127.0.0.1:8000 and select Start session.
3. Move the pointer naturally for at least 10 seconds. Select all six targets,
   scroll the reading panel, and submit the optional local preference form.
4. Select Finish and assess. Show the saved event count, available layers,
   risk score, and explanation. A low score does not prove someone is human.
5. Open http://127.0.0.1:8501 and refresh the session list. Select the session,
   inspect its path, timing, browser signals, and data-quality fields.
6. Label your own session human, with a source such as
   `manual participant demo-01, desktop mouse, September 7`.
7. Run `node scripts/browser_demo.mjs` for a controlled Chrome automation run.
   Refresh the dashboard and compare the bot session with your own session.
8. Open Evaluation. Explain that these are preliminary demo examples, not
   independent test-set accuracy. Show the false-positive denominator and
   excluded sessions along with the confusion matrix.

If Chrome automation cannot run, use the synthetic API fixture:
`.venv\Scripts\python.exe scripts\demo_bot.py`. Its label explicitly identifies
it as synthetic. It demonstrates the pipeline, not real browser detection.

## What to explain

HumanProof currently joins three kinds of client evidence: browser environment,
mouse movement, and interaction timing. Django validates and stores session
batches, Python computes shared features, and an explainable rule baseline
produces an observation-only assessment. Streamlit lets an analyst inspect and
label the evidence through a protected API.

The score is on a 0-100 scale, not a probability. Current rules add 35 points for
WebDriver, 25 for a long straight uniform-speed mouse path, and 25 for very
regular click intervals. The dashboard's default threshold of 60 is an
unvalidated demonstration choice. The baseline can miss automation and flag
legitimate behavior. No action is blocked.

## Implementation status

| Area | Implemented now | Remaining |
| --- | --- | --- |
| Collection | Server sessions, scoped tokens, automatic uploads, retry identity, validation and limits | Persistent browser queue and larger-scale concurrency |
| Browser | Basic environment and availability signals | Canvas, WebGL, font and offline audio probes |
| Mouse | Path length, speed, acceleration, turns, straightness and pauses | Controlled target acquisition and higher-rate sampling studies |
| Timing | Click intervals, scroll speeds, idle periods and coarse shortcuts | Broader input-device validation |
| Assessment | Shared deterministic features, rules, reasons and insufficient-data state | Trained models, calibration and ensemble fusion |
| Dashboard | Session charts, evidence, labels, export and preliminary metrics | Research evaluation views and live score updates |
| Dataset | Manual labels with history, bot examples, JSONL export and manifests | Diverse genuine-human recruitment and frozen grouped splits |
| Network | Marked unavailable | Instrumented edge, TCP/TLS and supported HTTP/2 metadata |
| Operations | Local access key, bounded requests, migrations and automated checks | Production hosting, retention jobs, monitoring and load study |

## Before presenting

- Rehearse once with the same machine, browser, and display size.
- Collect your own human session. Do not label generated data as human.
- Keep the API running during collection and dashboard use.
- Finish sessions before exporting the dataset for a stable snapshot.
- For an insufficient-data result, start a new session and interact longer.
- If saving fails, restore the connection and select Retry finish in that tab.
- Keep screenshots and an exported example as a backup for the live demo.

The working application covers the six core feature areas selected for this
demo, plus partial dataset and evaluation tooling. This is a functional scope
description, not a measured percentage of the full research project.
