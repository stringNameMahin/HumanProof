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
EVENT_PAGE_SIZE = 500
ANALYST_KEY_PATH = Path(__file__).resolve().parent.parent / "backend" / ".analyst-key"


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
                if key in {"path", "speed_samples"}:
                    display = f"{len(value)} samples (included in JSON export)"
                else:
                    display = json.dumps(value, ensure_ascii=True)
            else:
                display = str(value)
            rows.append({"Feature": name, "Value": display})
    return rows


def render_assessment(assessment):
    st.subheader("Assessment")
    if not assessment:
        st.info("No assessment is available for this session yet.")
        return
    score = assessment.get("risk_score")
    columns = st.columns(3)
    columns[0].metric("Bot risk score (0-100)", format_number(score, 0))
    columns[1].metric("Status", str(assessment.get("status", "Unavailable")))
    columns[2].metric("Decision", str(assessment.get("decision", "Unavailable")))
    st.caption(
        "This rule score is not a probability or a validated detection rate. "
        f"Model: {assessment.get('model_version', 'Unavailable')}."
    )
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
    st.caption("The network layer and trained ensemble are not implemented in this demo.")
    reasons = assessment.get("reasons", [])
    if reasons:
        st.write("Reasons")
        for reason in reasons:
            if isinstance(reason, dict):
                message = reason.get("message") or reason.get("code", "Recorded signal")
                st.write(f"- {message} (risk points: {reason.get('points', 0)})")
            else:
                st.write("- " + str(reason))
    else:
        st.info("No risk reasons were recorded.")
    features = assessment.get("features", {})
    with st.expander("Computed features", expanded=True):
        if isinstance(features, dict) and features:
            st.dataframe(pd.DataFrame(feature_rows(features)), hide_index=True, width="stretch")
        else:
            st.info("No computed features are available.")


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
        trajectory = alt.Chart(pointer).mark_line(point=True).encode(
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
                speed = alt.Chart(moving).mark_line(point=True).encode(
                    x="Time (s):Q", y="Speed (px/s):Q", tooltip=["Time (s):Q", "Speed (px/s):Q"]
                ).properties(height=220)
                st.altair_chart(speed, width="stretch")
    else:
        st.info("This event page has no pointer path to draw.")
    timed = frame.dropna(subset=["Time (s)"])
    if len(timed):
        timeline = alt.Chart(timed).mark_tick(thickness=2, size=20).encode(
            x="Time (s):Q", y=alt.Y("event_type:N", title="Event type"),
            color=alt.Color("event_type:N", legend=None), tooltip=["Time (s):Q", "event_type:N", "seq:Q"],
        ).properties(height=220)
        st.altair_chart(timeline, width="stretch")
    with st.expander("Raw events"):
        raw = frame.drop(columns=["Time (s)"]).copy()
        if "metadata" in raw:
            raw["metadata"] = raw["metadata"].map(lambda value: json.dumps(value, ensure_ascii=True))
        st.dataframe(raw, hide_index=True, width="stretch")


def render_label_form(client, session, path):
    st.subheader("Dataset label")
    st.caption("Labels are analyst annotations. Do not infer a ground-truth label from the risk score.")
    current = session.get("label", "unknown")
    st.write(f"Current label: **{current}**")
    st.write(f"Source: {session.get('label_source') or 'Not recorded'}")
    session_id = session["id"]
    with st.form(f"label-{session_id}"):
        label = st.selectbox("Label", LABELS, index=LABELS.index(current) if current in LABELS else 0)
        source = st.text_input("Label source", value=session.get("label_source") or "", max_chars=200,
                               placeholder="Example: consenting human demo, run 1")
        submitted = st.form_submit_button("Save label")
    if submitted:
        if not source.strip():
            st.error("Enter how this label was established.")
        else:
            try:
                client.request(path + "labels/", payload={"label": label, "source": source.strip()})
                st.session_state["label_saved"] = True
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))


def render_export(client, session, path):
    st.subheader("Session export")
    st.caption("Fetches up to 20,000 events with the session, browser signals, label, and assessment.")
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
            "Label": session.get("label"), "Events": session.get("event_count", 0),
            "Risk score": assessment.get("risk_score"), "Assessment": assessment.get("status"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    by_id = {session["id"]: session for session in sessions}
    selected = st.selectbox("Inspect session", list(by_id),
                            format_func=lambda value: f"{value} | {by_id[value].get('task', 'demo')} | {by_id[value].get('label', 'unknown')}")
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
    with st.expander("Browser signals"):
        fingerprint = session.get("fingerprint")
        if fingerprint:
            st.json(fingerprint)
        else:
            st.info("No browser signals were recorded.")
    st.subheader("Interaction traces")
    event_total = session.get("event_total", session.get("event_count", 0))
    st.caption(f"Event page {event_page} of {max(1, math.ceil(event_total / EVENT_PAGE_SIZE))}; {event_total} total events.")
    render_charts(session.get("events", []))
    render_label_form(client, session, path)
    render_export(client, session, path)


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
    for limitation in evaluation.get("limitations", []):
        st.write("- " + str(limitation))


def main():
    st.set_page_config(page_title="HumanProof Analyst", layout="wide")
    st.title("HumanProof Analyst")
    st.write("Inspect browser and interaction evidence, review explainable risk scores, and label demo sessions.")
    with st.sidebar:
        st.header("Connection")
        base_url = st.text_input("API base URL", value=os.environ.get("HUMANPROOF_API_URL", "http://127.0.0.1:8000"))
        api_key = st.text_input("Analyst key", value=default_analyst_key(), type="password")
        st.caption("Use the analyst key configured on the Django server.")
        view = st.radio("View", ["Sessions", "Evaluation"])
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
    if view == "Sessions":
        render_sessions(client)
    else:
        render_evaluation(client)


if __name__ == "__main__":
    main()
