"""HumanProof analyst dashboard."""

import json
import math
import os
from pathlib import Path
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st

from api_client import AnalystClient, ApiError


LABELS = ["unknown", "human", "bot"]
LAYERS = ["browser", "mouse", "timing", "network"]
EVENT_PAGE_SIZE = 500
ANALYST_KEY_PATH = Path(__file__).resolve().parent.parent / "backend" / ".analyst-key"
# Categorical slots 1-2 of the validated reference palette, in fixed order: human, then bot.
CLASS_COLORS = alt.Scale(domain=["human", "bot"], range=["#2a78d6", "#eb6834"])
SERIES_COLOR = "#2a78d6"
DECISION_BADGES = {"allow": ("green", "Allow"), "challenge": ("orange", "Challenge"), "block": ("red", "Block"),
                   "observe": ("gray", "Observe only")}
COMPARE_FEATURES = [
    ("browser", "webdriver"), ("browser", "headless_user_agent"), ("browser", "anomaly_count"),
    ("mouse", "mean_speed_px_s"), ("mouse", "speed_cv"), ("mouse", "straightness"), ("mouse", "micro_jitter_px"),
    ("mouse", "tremor_band_ratio"), ("mouse", "fitts_slope_ms_per_bit"), ("mouse", "movement_time_cv"),
    ("mouse", "mean_click_offset_ratio"), ("timing", "median_press_ms"), ("timing", "click_interval_cv"),
    ("timing", "time_to_first_interaction_ms"), ("timing", "mean_key_press_ms"), ("timing", "untrusted_event_count"),
    ("network", "user_agent_family"), ("network", "ja4"), ("network", "tls_user_agent_mismatch"),
]


def default_analyst_key():
    configured = os.environ.get("HUMANPROOF_ANALYST_KEY", "").strip()
    if configured:
        return configured
    try:
        return ANALYST_KEY_PATH.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def format_number(value, digits=2):
    if value is None:
        return "Unavailable"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def feature_rows(values, prefix=""):
    rows = []
    for key, value in values.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.extend(feature_rows(value, name))
        else:
            if isinstance(value, list):
                if key in {"path", "speed_samples", "fitts_points"}:
                    display = f"{len(value)} samples (included in JSON export)"
                else:
                    display = json.dumps(value, ensure_ascii=True)
            else:
                display = str(value)
            rows.append({"Feature": name, "Value": display})
    return rows


def render_layer_scores(assessment):
    scores = assessment.get("layer_scores") or {}
    rows = [{"Layer": layer, "Score": scores[layer]} for layer in LAYERS if isinstance(scores.get(layer), (int, float))]
    if not rows:
        return
    st.write("Layer scores (0-100)")
    chart = alt.Chart(pd.DataFrame(rows)).mark_bar(color=SERIES_COLOR, cornerRadiusEnd=4, height=14).encode(
        x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 100]), title="Risk score"),
        y=alt.Y("Layer:N", sort=LAYERS, title=None),
        tooltip=["Layer:N", alt.Tooltip("Score:Q", format=".1f")],
    ).properties(height=34 * len(rows) + 30)
    st.altair_chart(chart, width="stretch")
    missing = [layer for layer in LAYERS if layer not in {row["Layer"] for row in rows}]
    if missing:
        st.caption("No score for unavailable layers: " + ", ".join(missing) + ".")


def render_assessment(assessment):
    st.subheader("Assessment")
    if not assessment:
        st.info("No assessment is available for this session yet.")
        return
    score = assessment.get("risk_score")
    decision = str(assessment.get("decision", "observe"))
    columns = st.columns(4)
    columns[0].metric("Bot risk score (0-100)", format_number(score, 0))
    columns[1].metric("Rule points", format_number(assessment.get("rule_score"), 0))
    columns[2].metric("Status", str(assessment.get("status", "Unavailable")))
    color, text = DECISION_BADGES.get(decision, ("gray", decision))
    with columns[3]:
        st.write("Decision")
        st.badge(text, color=color)
    model = str(assessment.get("model_version", "Unavailable"))
    st.caption(f"Scored by {model}. " + ("A trained BotBiometricNet probability; rule points are shown for explanation."
                                         if model.startswith("botbiometricnet") else
                                         "Rule points, not a probability or a validated detection rate."))
    if assessment.get("status") == "insufficient_data":
        st.warning("There is not enough evidence to assess this session reliably.")
    layers = assessment.get("available_layers", [])
    if isinstance(layers, dict):
        st.write("Layer availability")
        st.dataframe(pd.DataFrame(feature_rows(layers)), hide_index=True, width="stretch")
    elif layers:
        st.write("Available layers: " + ", ".join(str(layer) for layer in layers))
    else:
        st.write("Available layers: none")
    render_layer_scores(assessment)
    reasons = assessment.get("reasons", [])
    if reasons:
        st.write("Reasons")
        for reason in reasons:
            if isinstance(reason, dict):
                message = reason.get("message") or reason.get("code", "Recorded signal")
                layer = f"[{reason['layer']}] " if reason.get("layer") else ""
                st.write(f"- {layer}{message} (risk points: {reason.get('points', 0)})")
            else:
                st.write("- " + str(reason))
    else:
        st.info("No risk reasons were recorded.")
    explanation = assessment.get("model_explanation")
    if isinstance(explanation, dict) and explanation.get("top_contributions"):
        st.write("Model explanation: largest feature contributions (positive pushes toward bot)")
        st.dataframe(pd.DataFrame([{
            "Layer": item.get("layer"), "Feature": item.get("feature"), "Value": str(item.get("value")),
            "Contribution (log-odds)": item.get("contribution"),
        } for item in explanation["top_contributions"]]), hide_index=True, width="stretch")
    features = assessment.get("features", {})
    with st.expander("Computed features", expanded=False):
        if isinstance(features, dict) and features:
            st.dataframe(pd.DataFrame(feature_rows(features)), hide_index=True, width="stretch")
        else:
            st.info("No computed features are available.")


def render_fitts(mouse):
    points = (mouse or {}).get("fitts_points") or []
    if len(points) < 2:
        return
    frame = pd.DataFrame(points).rename(columns={"id_bits": "Index of difficulty (bits)",
                                                 "movement_time_ms": "Movement time (ms)"})
    dots = alt.Chart(frame).mark_circle(size=80, color=SERIES_COLOR, stroke="white", strokeWidth=2).encode(
        x="Index of difficulty (bits):Q", y="Movement time (ms):Q",
        tooltip=["Index of difficulty (bits):Q", "Movement time (ms):Q"])
    layers = [dots]
    slope, intercept = mouse.get("fitts_slope_ms_per_bit"), mouse.get("fitts_intercept_ms")
    if slope is not None and intercept is not None:
        low, high = frame["Index of difficulty (bits)"].min(), frame["Index of difficulty (bits)"].max()
        line = pd.DataFrame({"Index of difficulty (bits)": [low, high],
                             "Movement time (ms)": [intercept + slope * low, intercept + slope * high]})
        layers.append(alt.Chart(line).mark_line(color="#52514e", strokeDash=[4, 4], strokeWidth=2).encode(
            x="Index of difficulty (bits):Q", y="Movement time (ms):Q"))
    st.write("Fitts' law: movement time against target difficulty")
    st.altair_chart(alt.layer(*layers).properties(height=260), width="stretch")
    st.caption(f"Fit MT = {format_number(intercept, 0)} + {format_number(slope, 1)} x ID ms, "
               f"r2 = {format_number(mouse.get('fitts_r2'))}. People usually show a positive slope; "
               "scripts often move in constant time regardless of difficulty.")


def render_charts(events):
    if not events:
        st.info("No events were recorded on this page.")
        return
    frame = pd.DataFrame(events)
    for column in ["x", "y", "elapsed_ms", "ts", "seq"]:
        if column not in frame:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "event_type" not in frame:
        frame["event_type"] = "unknown"
    frame = frame.sort_values(["elapsed_ms", "seq"], na_position="last")
    frame["Time (s)"] = frame["elapsed_ms"] / 1000
    st.caption("Charts show the current event page. Coordinates are CSS pixels with screen Y increasing downward.")
    pointer = frame[frame["event_type"].isin(["move", "mousemove", "pointermove", "touchmove", "click"])].dropna(subset=["x", "y"])
    if len(pointer):
        trajectory = alt.Chart(pointer).mark_line(point=True, color=SERIES_COLOR, strokeWidth=2).encode(
            x=alt.X("x:Q", title="X (px)"),
            y=alt.Y("y:Q", title="Y (px)", scale=alt.Scale(reverse=True)),
            order=alt.Order("seq:Q"),
            tooltip=["event_type:N", "x:Q", "y:Q", "Time (s):Q"],
        ).properties(height=340)
        st.altair_chart(trajectory, width="stretch")
        moving = pointer[pointer["event_type"].isin(["move", "mousemove", "pointermove", "touchmove"])].copy()
        if len(moving) > 1:
            delta = moving["elapsed_ms"].diff() / 1000
            distance = (moving["x"].diff() ** 2 + moving["y"].diff() ** 2) ** 0.5
            moving["Speed (px/s)"] = distance / delta.where(delta > 0)
            moving = moving.dropna(subset=["Time (s)", "Speed (px/s)"])
            if len(moving):
                speed = alt.Chart(moving).mark_line(color=SERIES_COLOR, strokeWidth=2).encode(
                    x="Time (s):Q", y="Speed (px/s):Q", tooltip=["Time (s):Q", "Speed (px/s):Q"]
                ).properties(height=220)
                st.altair_chart(speed, width="stretch")
    else:
        st.info("This event page has no pointer path to draw.")
    timed = frame.dropna(subset=["Time (s)"])
    if len(timed):
        timeline = alt.Chart(timed).mark_tick(thickness=2, size=20, color=SERIES_COLOR).encode(
            x="Time (s):Q", y=alt.Y("event_type:N", title="Event type"),
            tooltip=["Time (s):Q", "event_type:N", "seq:Q"],
        ).properties(height=220)
        st.altair_chart(timeline, width="stretch")
    with st.expander("Raw events"):
        raw = frame.drop(columns=["Time (s)"]).copy()
        if "metadata" in raw:
            raw["metadata"] = raw["metadata"].map(lambda value: json.dumps(value, ensure_ascii=True))
        st.dataframe(raw, hide_index=True, width="stretch")


def render_browser(session):
    fingerprint = session.get("fingerprint")
    browser = ((session.get("assessment") or {}).get("features") or {}).get("browser") or {}
    with st.expander("Browser environment (layer 1)"):
        checks = {name: browser.get(name) for name in (
            "webdriver", "headless_user_agent", "software_renderer", "cdp_detected", "permission_inconsistent",
            "window_geometry_inconsistent", "zero_outer_window", "empty_languages", "chromium_without_chrome_object",
            "chromium_without_plugins") if name in browser}
        if checks:
            st.dataframe(pd.DataFrame([{"Check": name, "Result": "Not available" if value is None else str(value)}
                                       for name, value in checks.items()]), hide_index=True, width="stretch")
        if fingerprint:
            st.json(fingerprint)
        else:
            st.info("No browser signals were recorded.")


def render_network(session):
    network = ((session.get("assessment") or {}).get("features") or {}).get("network") or {}
    observations = session.get("network_observations") or []
    with st.expander("Network evidence (layer 4)"):
        if not network.get("available") and not observations:
            st.info("No network observations were recorded for this session.")
            return
        summary = {key: network.get(key) for key in (
            "transport", "user_agent_family", "request_count", "sec_fetch_share", "client_hint_share",
            "missing_browser_headers", "client_library_user_agent", "user_agent_mismatch", "language_mismatch",
            "ja4", "ja3_hash", "tls_version", "alpn", "tls_grease", "tls_handshake_ms", "tls_user_agent_mismatch")}
        st.dataframe(pd.DataFrame([{"Signal": key, "Value": "Not available" if value is None else str(value)}
                                   for key, value in summary.items()]), hide_index=True, width="stretch")
        if network.get("transport") == "http":
            st.caption("TLS fingerprints require the session to be collected through the TLS edge (https://127.0.0.1:8443).")
        if observations:
            first = observations[0].get("data", {})
            st.write(f"First of {session.get('network_observation_total', len(observations))} observed requests "
                     f"({observations[0].get('endpoint')}): header order")
            st.code(", ".join(first.get("header_names", [])) or "none")


def render_label_form(client, session, path):
    st.subheader("Dataset label")
    st.caption("Labels are analyst annotations. Do not infer a ground-truth label from the risk score. "
               "Use the group for the participant or automation family so training splits keep them together.")
    current = session.get("label", "unknown")
    st.write(f"Current label: **{current}**")
    st.write(f"Source: {session.get('label_source') or 'Not recorded'}")
    session_id = session["id"]
    with st.form(f"label-{session_id}"):
        label = st.selectbox("Label", LABELS, index=LABELS.index(current) if current in LABELS else 0)
        source = st.text_input("Label source", value=session.get("label_source") or "", max_chars=200,
                               placeholder="Example: consenting human demo, run 1")
        group = st.text_input("Label group", value=session.get("label_group") or "", max_chars=64,
                              placeholder="Example: participant-mp or cdp-linear")
        submitted = st.form_submit_button("Save label")
    if submitted:
        if not source.strip():
            st.error("Enter how this label was established.")
        else:
            try:
                payload = {"label": label, "source": source.strip()}
                if group.strip():
                    payload["group"] = group.strip()
                client.request(path + "labels/", payload=payload)
                st.session_state["label_saved"] = True
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))


def render_export(client, session, path):
    st.subheader("Session export")
    st.caption("Fetches up to 20,000 events with the session, browser signals, network observations, label, and assessment.")
    export_key = "session_export"
    if st.button("Prepare JSON export"):
        try:
            exported = client.request(path + "export/")
            st.session_state[export_key] = {
                "id": session["id"],
                "base_url": client.base_url,
                "data": json.dumps(exported, indent=2, ensure_ascii=True),
            }
        except ApiError as exc:
            st.error(str(exc))
    saved = st.session_state.get(export_key, {})
    if saved.get("id") == session["id"] and saved.get("base_url") == client.base_url:
        st.download_button("Download session JSON", saved["data"], file_name=f"humanproof-{session['id']}.json",
                           mime="application/json")
        st.caption("This is a snapshot from the last export request. Prepare it again after making changes.")


def session_option(item):
    assessment = item.get("assessment") or {}
    return (f"{item['id'][:8]} | {item.get('task', 'demo')} | {item.get('label', 'unknown')}"
            f"{' / ' + item['label_group'] if item.get('label_group') else ''} | score {format_number(assessment.get('risk_score'), 0)}")


def render_sessions(client):
    controls = st.columns(4)
    label = controls[0].selectbox("Filter label", ["All", *LABELS])
    state = controls[1].selectbox("Filter state", ["All", "active", "complete"])
    page_size = controls[2].selectbox("Sessions per page", [20, 50, 100])
    filter_signature = (label, state, page_size, client.base_url)
    if st.session_state.get("session_filters") != filter_signature:
        st.session_state["session_page"] = 1
        st.session_state["session_filters"] = filter_signature
    page = controls[3].number_input("Session page", min_value=1, step=1, key="session_page")
    try:
        listing = client.request("/api/v1/sessions/", params={
            "page": page, "page_size": page_size,
            "label": None if label == "All" else label,
            "state": None if state == "All" else state,
        })
    except ApiError as exc:
        st.error(str(exc))
        return
    sessions = listing.get("results", [])
    total = listing.get("count", 0)
    columns = st.columns(3)
    columns[0].metric("Matching sessions", total)
    columns[1].metric("Events on listed sessions", sum(item.get("event_count", 0) for item in sessions))
    assessed = sum((item.get("assessment") or {}).get("status") == "ready" for item in sessions)
    columns[2].metric("Assessed on this page", assessed)
    st.caption(f"Session page {page} of {max(1, math.ceil(total / page_size))}.")
    if not sessions:
        st.info("No sessions on this page. Adjust the filters or page, or collect a session in the demo.")
        return
    rows = []
    for session in sessions:
        assessment = session.get("assessment") or {}
        rows.append({
            "Session": session["id"], "Started": session.get("started_at"),
            "Task": session.get("task"), "State": session.get("state"),
            "Label": session.get("label"), "Group": session.get("label_group") or "",
            "Events": session.get("event_count", 0),
            "Risk score": assessment.get("risk_score"), "Decision": assessment.get("decision"),
            "Assessment": assessment.get("status"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    by_id = {session["id"]: session for session in sessions}
    completed = [index for index, session in enumerate(sessions) if session.get("state") == "complete"]
    selected = st.selectbox("Inspect session", list(by_id), index=completed[0] if completed else 0,
                            format_func=lambda value: session_option(by_id[value]))
    path = f"/api/v1/sessions/{quote(str(selected), safe='')}/"
    event_page = st.number_input("Event page", min_value=1, step=1, value=1, key=f"event-page-{selected}")
    try:
        session = client.request(path, params={"page": event_page, "page_size": EVENT_PAGE_SIZE})
    except ApiError as exc:
        st.error(str(exc))
        return
    st.subheader("Session details")
    st.caption(f"Started: {session.get('started_at', 'Unknown')} | Completed: {session.get('completed_at') or 'Active'}")
    render_assessment(session.get("assessment"))
    render_browser(session)
    render_network(session)
    st.subheader("Interaction traces")
    event_total = session.get("event_total", session.get("event_count", 0))
    st.caption(f"Event page {event_page} of {max(1, math.ceil(event_total / EVENT_PAGE_SIZE))}; {event_total} total events.")
    render_charts(session.get("events", []))
    render_fitts(((session.get("assessment") or {}).get("features") or {}).get("mouse"))
    render_label_form(client, session, path)
    render_export(client, session, path)


def render_compare(client):
    st.subheader("Compare two sessions")
    st.caption("Place a human session beside an automated one to see which evidence differs.")
    try:
        listing = client.request("/api/v1/sessions/", params={"page": 1, "page_size": 100, "state": "complete"})
    except ApiError as exc:
        st.error(str(exc))
        return
    sessions = listing.get("results", [])
    if len(sessions) < 2:
        st.info("Complete at least two sessions to compare them.")
        return
    by_id = {item["id"]: item for item in sessions}
    ids = list(by_id)
    first, second = default_comparison(sessions)
    columns = st.columns(2)
    chosen = [columns[0].selectbox("First session", ids, index=first, format_func=lambda value: session_option(by_id[value])),
              columns[1].selectbox("Second session", ids, index=second, format_func=lambda value: session_option(by_id[value]))]
    details = []
    for column, session_id in zip(columns, chosen):
        try:
            session = client.request(f"/api/v1/sessions/{quote(session_id, safe='')}/",
                                     params={"page": 1, "page_size": EVENT_PAGE_SIZE})
        except ApiError as exc:
            column.error(str(exc))
            return
        details.append(session)
        assessment = session.get("assessment") or {}
        with column:
            st.metric("Bot risk score (0-100)", format_number(assessment.get("risk_score"), 0))
            path = (assessment.get("features") or {}).get("mouse", {}).get("path") or []
            if path:
                chart = alt.Chart(pd.DataFrame(path)).mark_line(color=SERIES_COLOR, strokeWidth=2).encode(
                    x=alt.X("x:Q", title="X (px)"), y=alt.Y("y:Q", title="Y (px)", scale=alt.Scale(reverse=True)),
                    order="elapsed_ms:Q", tooltip=["x:Q", "y:Q", "elapsed_ms:Q"]).properties(height=260)
                st.altair_chart(chart, width="stretch")
            else:
                st.info("No pointer path.")
    rows = []
    for layer, name in COMPARE_FEATURES:
        values = [((session.get("assessment") or {}).get("features") or {}).get(layer, {}).get(name) for session in details]
        rows.append({"Layer": layer, "Feature": name, "First": compare_value(values[0]), "Second": compare_value(values[1])})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def default_comparison(sessions):
    """Prefer the newest scored human beside the newest scored bot; the two are always different sessions."""
    ready = [index for index, item in enumerate(sessions) if (item.get("assessment") or {}).get("status") == "ready"]
    candidates = ready + [index for index in range(len(sessions)) if index not in ready]
    first = next((index for index in ready if sessions[index].get("label") == "human"), candidates[0])
    second = next((index for index in ready if sessions[index].get("label") == "bot" and index != first),
                  next(index for index in candidates if index != first))
    return first, second


def compare_value(value):
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return format_number(value)


def roc_points(scores):
    humans = sum(item["label"] == "human" for item in scores)
    bots = len(scores) - humans
    if not humans or not bots:
        return []
    points = [{"False positive rate": 0.0, "Detection rate": 0.0, "Threshold": 101.0}]
    for threshold in sorted({item["risk_score"] for item in scores}, reverse=True):
        flagged = [item for item in scores if item["risk_score"] >= threshold]
        points.append({"False positive rate": sum(item["label"] == "human" for item in flagged) / humans,
                       "Detection rate": sum(item["label"] == "bot" for item in flagged) / bots,
                       "Threshold": threshold})
    return points


def render_score_charts(scores):
    frame = pd.DataFrame(scores)
    if frame.empty or frame["label"].nunique() < 2:
        return
    columns = st.columns(2)
    histogram = alt.Chart(frame).mark_bar(opacity=0.85, cornerRadiusEnd=4).encode(
        x=alt.X("risk_score:Q", bin=alt.Bin(extent=[0, 100], step=10), title="Risk score"),
        y=alt.Y("count():Q", title="Sessions", stack=None),
        color=alt.Color("label:N", scale=CLASS_COLORS, title="Label"),
        xOffset="label:N", tooltip=["label:N", "count():Q"],
    ).properties(height=260, title="Score distribution by label")
    columns[0].altair_chart(histogram, width="stretch")
    points = roc_points(scores)
    if points:
        roc = alt.Chart(pd.DataFrame(points)).mark_line(color=SERIES_COLOR, strokeWidth=2, point=True).encode(
            x=alt.X("False positive rate:Q", scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("Detection rate:Q", scale=alt.Scale(domain=[0, 1])),
            order="False positive rate:Q", tooltip=["Threshold:Q", "False positive rate:Q", "Detection rate:Q"],
        ).properties(height=260, title="ROC curve over thresholds")
        columns[1].altair_chart(roc, width="stretch")


def render_evaluation(client):
    st.subheader("Preliminary demo evaluation")
    st.warning("These are results on the currently labeled sessions. They are not a frozen test set or a validated detection claim.")
    threshold = st.slider("Bot risk threshold", min_value=0, max_value=100, value=60, step=5)
    st.caption("Scores at or above the threshold count as predicted bots. Insufficient evidence is excluded.")
    try:
        evaluation = client.request("/api/v1/evaluations/", params={"threshold": threshold})
    except ApiError as exc:
        st.error(str(exc))
        return
    counts = st.columns(3)
    counts[0].metric("Labeled sessions", evaluation.get("total_labeled", 0))
    counts[1].metric("Labeled and assessed", evaluation.get("labeled_assessed", 0))
    counts[2].metric("Excluded: insufficient evidence", evaluation.get("excluded_insufficient", 0))
    matrix = evaluation.get("confusion_matrix", {})
    st.dataframe(pd.DataFrame([
        {"Actual label": "Bot", "Predicted bot": matrix.get("tp", 0), "Predicted human": matrix.get("fn", 0)},
        {"Actual label": "Human", "Predicted bot": matrix.get("fp", 0), "Predicted human": matrix.get("tn", 0)},
    ]), hide_index=True, width="stretch")
    metrics = evaluation.get("metrics", {})
    columns = st.columns(4)
    for column, key, label in zip(columns, ["precision", "recall", "false_positive_rate", "accuracy"],
                                  ["Bot precision", "Bot recall", "Human false positive rate", "Accuracy"]):
        value = metrics.get(key)
        column.metric(label, f"{value:.1%}" if isinstance(value, (int, float)) else "Unavailable")
    st.caption("A metric is unavailable when its denominator is zero. Model: " + str(evaluation.get("model_version", "Unavailable")))
    render_score_charts(evaluation.get("scores") or [])
    for limitation in evaluation.get("limitations", []):
        st.write("- " + str(limitation))


def percent(value):
    return f"{value:.1%}" if isinstance(value, (int, float)) else "n/a"


def render_model(client):
    st.subheader("BotBiometricNet")
    try:
        info = client.request("/api/v1/model/")
    except ApiError as exc:
        st.error(str(exc))
        return
    st.write(f"Active scorer: **{info.get('active_version', 'unknown')}**")
    model = info.get("model")
    if not isinstance(model, dict):
        st.info("No trained model is deployed, so sessions are scored by the explainable rules below. Label genuine "
                "human sessions and bot-fleet runs, then run `python backend/manage.py train_model` and `rescore`.")
    else:
        training = model.get("training", {})
        columns = st.columns(4)
        columns[0].metric("Training sessions", training.get("sessions", 0))
        columns[1].metric("Human sessions", training.get("humans", 0))
        columns[2].metric("Bot sessions", training.get("bots", 0))
        columns[3].metric("Operating threshold", format_number(100 * (model.get("operating_threshold") or 0.5), 0))
        known = (model.get("evaluation") or {}).get("known_frameworks")
        if known:
            st.write("Known frameworks: grouped cross-validation")
            operating = known["at_operating_threshold"]
            metric_columns = st.columns(4)
            metric_columns[0].metric("ROC AUC", format_number(operating.get("auc"), 3))
            metric_columns[1].metric("Detection rate", percent(operating.get("detection_rate")))
            metric_columns[2].metric("Human false positives", percent(operating.get("false_positive_rate")))
            metric_columns[3].metric("Brier score", format_number(operating.get("brier"), 3))
            rows = [{"Evidence": f"{layer} layer alone", "Sessions": values.get("sessions"), "AUC": values.get("auc"),
                     "Detection @50": percent(values.get("detection_rate")),
                     "False positives @50": percent(values.get("false_positive_rate"))}
                    for layer, values in (known.get("layers_alone") or {}).items()]
            rules = known.get("rules_baseline_at_60")
            if rules:
                rows.append({"Evidence": "rules-v2 (score >= 60)", "Sessions": sum(rules["confusion_matrix"].values()),
                             "AUC": rules.get("auc"), "Detection @50": percent(rules.get("detection_rate")),
                             "False positives @50": percent(rules.get("false_positive_rate"))})
            fused = known.get("at_0_5", {})
            rows.append({"Evidence": "BotBiometricNet fused", "Sessions": training.get("sessions"), "AUC": fused.get("auc"),
                         "Detection @50": percent(fused.get("detection_rate")),
                         "False positives @50": percent(fused.get("false_positive_rate"))})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            calibration = known.get("calibration") or []
            if calibration:
                chart = alt.Chart(pd.DataFrame(calibration)).mark_line(point=True, color=SERIES_COLOR, strokeWidth=2).encode(
                    x=alt.X("mean_predicted:Q", scale=alt.Scale(domain=[0, 1]), title="Mean predicted bot probability"),
                    y=alt.Y("observed_bot_rate:Q", scale=alt.Scale(domain=[0, 1]), title="Observed bot rate"),
                    tooltip=["bin:N", "count:Q", "mean_predicted:Q", "observed_bot_rate:Q"],
                ).properties(height=240, title="Calibration of out-of-fold predictions")
                st.altair_chart(chart, width="stretch")
        unseen = (model.get("evaluation") or {}).get("unseen_frameworks")
        if unseen:
            st.write("Arms race: detection of each family while unseen, and after retraining with it")
            recovered = (known or {}).get("family_detection", {})
            st.dataframe(pd.DataFrame([{"Family": family, "Sessions": result.get("sessions"),
                                        "Detected while unseen": percent(result.get("detection_rate")),
                                        "Detected after retraining": percent(recovered.get(family, {}).get("detection_rate"))}
                                       for family, result in unseen.get("families", {}).items()]),
                         hide_index=True, width="stretch")
        fusion = model.get("fusion", {})
        weights = pd.DataFrame([{"Layer": layer, "Fusion weight": fusion.get("weights", {}).get(layer, 0)} for layer in LAYERS])
        st.altair_chart(alt.Chart(weights).mark_bar(color=SERIES_COLOR, cornerRadiusEnd=4, height=14).encode(
            x=alt.X("Fusion weight:Q"), y=alt.Y("Layer:N", sort=LAYERS, title=None),
            tooltip=["Layer:N", alt.Tooltip("Fusion weight:Q", format=".3f")],
        ).properties(height=170, title="How much each layer counts in the fused score"), width="stretch")
        layer = st.selectbox("Layer coefficients", LAYERS)
        layer_spec = model.get("layers", {}).get(layer, {})
        if layer_spec.get("trained"):
            table = pd.DataFrame({"Feature": layer_spec["features"], "Coefficient": layer_spec["coef"]})
            table = table.reindex(table["Coefficient"].abs().sort_values(ascending=False).index)
            st.dataframe(table.head(12), hide_index=True, width="stretch")
            st.caption("Coefficients apply to standardised features; positive values push toward bot.")
        else:
            st.info("This layer was not trained (too few sessions with both labels).")
    st.write("Rule catalogue")
    st.dataframe(pd.DataFrame(info.get("rules") or []), hide_index=True, width="stretch")
    policy = info.get("policy") or {}
    st.caption(f"Sign-in policy: allow below {policy.get('allow_below', 40)}, challenge from {policy.get('allow_below', 40)}, "
               f"block from {policy.get('block_at', 75)}; the interaction lab only observes.")


def main():
    st.set_page_config(page_title="HumanProof Analyst", layout="wide")
    st.title("HumanProof Analyst")
    st.write("Inspect browser, pointer, timing, and network evidence, review explainable risk scores, and label sessions.")
    with st.sidebar:
        st.header("Connection")
        base_url = st.text_input("API base URL", value=os.environ.get("HUMANPROOF_API_URL", "http://127.0.0.1:8000"))
        api_key = st.text_input("Analyst key", value=default_analyst_key(), type="password")
        st.caption("Use the analyst key configured on the Django server.")
        views = ["Sessions", "Compare", "Evaluation", "Model"]
        requested = st.query_params.get("view", "Sessions")
        view = st.radio("View", views, index=views.index(requested) if requested in views else 0)
        st.button("Refresh")
    connection = (base_url, api_key)
    if st.session_state.get("connection") != connection:
        st.session_state.pop("session_export", None)
        st.session_state["connection"] = connection
    if not api_key:
        st.info("Enter the analyst key in the sidebar to connect to the API.")
        return
    try:
        client = AnalystClient(base_url.strip(), api_key)
    except ApiError as exc:
        st.error(str(exc))
        return
    if st.session_state.pop("label_saved", False):
        st.success("The label and its source were saved.")
    views = {"Sessions": render_sessions, "Compare": render_compare, "Evaluation": render_evaluation, "Model": render_model}
    views[view](client)


if __name__ == "__main__":
    main()
