"""Streamlit view over the telemetry table with Session List and Session Detail views."""
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "backend" / "db.sqlite3"
TABLE = "tracker_telemetryevent"

st.set_page_config(page_title="Mouse Telemetry Dashboard", layout="wide")

# Reusable SVG helper strings
SVG_REFRESH = """<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:4px;"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>"""
SVG_LIST = """<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:6px;"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>"""
SVG_SEARCH = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:6px;"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>"""
SVG_BACK = """<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:4px;"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>"""
SVG_ARROW_RIGHT = """<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-left:4px;"><polyline points="9 18 15 12 9 6"></polyline></svg>"""

# Manage selected session via st.session_state
if "selected_session" not in st.session_state:
    st.session_state["selected_session"] = None


@st.cache_data(ttl=5)
def load_events() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        return pd.read_sql_query(f"SELECT * FROM {TABLE} ORDER BY ts", conn)


def clear_cache():
    st.cache_data.clear()


# Header actions
col_title, col_btn = st.columns([8, 2])
with col_title:
    st.markdown(
        """<h1 style="display:flex; align-items:center; gap:10px; margin:0 0 10px 0;">
        <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 0 0-10 10c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.69c-2.77.6-3.36-1.34-3.36-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.87 1.52 2.34 1.07 2.91.83.1-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.92 0-1.11.38-2 1.03-2.71-.1-.25-.45-1.29.1-2.64 0 0 .84-.27 2.75 1.02.79-.22 1.65-.33 2.5-.33.85 0 1.71.11 2.5.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.35.2 2.39.1 2.64.65.71 1.03 1.6 1.03 2.71 0 3.82-2.34 4.66-4.57 4.91.36.31.69.92.69 1.85V21c0 .27.16.59.67.5C19.14 20.16 22 16.42 22 12A10 10 0 0 0 12 2z"></path></svg>
        <span>HumanProof — Telemetry Lab</span>
        </h1>""",
        unsafe_allow_html=True
    )
with col_btn:
    st.markdown(
        f"""<div style="display:flex; justify-content:flex-end; align-items:center; height:100%;">
        <span style="font-size:12px; color:#64748b; margin-right:8px;">{SVG_REFRESH} Live Data</span>
        </div>""",
        unsafe_allow_html=True
    )
    if st.button("Refresh Data", use_container_width=True):
        clear_cache()
        st.rerun()

try:
    df = load_events()
except (sqlite3.OperationalError, pd.errors.DatabaseError):
    df = pd.DataFrame()

if df.empty:
    st.info(
        "No events yet. Run the Django server, open http://127.0.0.1:8000/, "
        "move the pointer, solve the CAPTCHA, and hit Sign in."
    )
    st.stop()

df["client_time"] = pd.to_datetime(df["ts"], unit="ms")


# -------------------------------------------------------------
# VIEW 1: SESSIONS LIST VIEW
# -------------------------------------------------------------
if st.session_state["selected_session"] is None:
    st.markdown(
        f"""<h3 style="display:flex; align-items:center; margin-top:12px;">
        {SVG_LIST}
        <span>Recorded Telemetry Sessions</span>
        </h3>""",
        unsafe_allow_html=True
    )
    st.caption("Click View Details on any session to inspect trajectory coordinates, timing metrics, and records.")

    # Calculate summary per session
    session_summary = []
    for session_id, group in df.groupby("session_id", sort=False):
        num_events = len(group)
        num_clicks = int((group["event_type"] == "click").sum())
        span = (group["ts"].max() - group["ts"].min()) / 1000 if num_events > 1 else 0.0
        first_seen = group["client_time"].min()

        session_summary.append({
            "session_id": session_id,
            "events": num_events,
            "clicks": num_clicks,
            "span_sec": f"{span:.1f}s",
            "first_seen": first_seen.strftime("%Y-%m-%d %H:%M:%S") if pd.notnull(first_seen) else "N/A",
            "_span_num": span
        })

    summary_df = pd.DataFrame(session_summary)

    # Top overall stats
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Sessions", len(summary_df))
    m2.metric("Total Events", len(df))
    m3.metric("Total Clicks", int((df["event_type"] == "click").sum()))
    total_span = (df["ts"].max() - df["ts"].min()) / 1000 if len(df) > 1 else 0
    m4.metric("Overall Time Span", f"{total_span:.1f}s")

    st.markdown("---")

    # Render list cards / rows
    for row in session_summary:
        with st.container():
            c1, c2, c3, c4, c5, c6 = st.columns([3, 1.2, 1.2, 1.2, 2.4, 1.8])
            with c1:
                st.markdown(f"**Session:** `{row['session_id'][:16]}...`")
                st.caption(f"Full: `{row['session_id']}`")
            with c2:
                st.metric("Events", row["events"])
            with c3:
                st.metric("Clicks", row["clicks"])
            with c4:
                st.metric("Span", row["span_sec"])
            with c5:
                st.write("**Started at:**")
                st.caption(row["first_seen"])
            with c6:
                st.write("")
                st.markdown(
                    f"""<div style="display:flex; align-items:center; gap:4px; margin-bottom:4px; font-size:12px; color:#475569;">
                    {SVG_SEARCH}
                    <span>Inspect</span>
                    </div>""",
                    unsafe_allow_html=True
                )
                if st.button("View Details", key=f"btn_{row['session_id']}", use_container_width=True):
                    st.session_state["selected_session"] = row["session_id"]
                    st.rerun()
            st.markdown("<hr style='margin: 8px 0; border-color: #f1f5f9;' />", unsafe_allow_html=True)


# -------------------------------------------------------------
# VIEW 2: SESSION DETAILS VIEW (Full Interactive Drilldown)
# -------------------------------------------------------------
else:
    selected_sess = st.session_state["selected_session"]

    # Back button to return to the sessions list
    col_back, _ = st.columns([2, 8])
    with col_back:
        if st.button("Back to Sessions List", use_container_width=True):
            st.session_state["selected_session"] = None
            st.rerun()

    st.markdown(
        f"""<h3 style="display:flex; align-items:center; margin-top:10px;">
        {SVG_SEARCH}
        <span>Session Details: <code>{selected_sess}</code></span>
        </h3>""",
        unsafe_allow_html=True
    )

    # Filter by selected session first
    session_df = df[df["session_id"] == selected_sess]

    # Sidebar Filters
    st.sidebar.markdown(
        f"""<div style="display:flex; align-items:center; gap:6px; font-weight:600; margin-bottom:6px;">
        {SVG_SEARCH} Active Session
        </div>""",
        unsafe_allow_html=True
    )
    st.sidebar.code(selected_sess)
    
    available_sessions = sorted(df["session_id"].unique())
    selected_from_sidebar = st.sidebar.selectbox(
        "Switch Session",
        available_sessions,
        index=available_sessions.index(selected_sess) if selected_sess in available_sessions else 0
    )
    if selected_from_sidebar != selected_sess:
        st.session_state["selected_session"] = selected_from_sidebar
        st.rerun()

    available_types = sorted(session_df["event_type"].unique())
    kinds = st.sidebar.multiselect(
        "Filter Event Types",
        available_types,
        default=available_types
    )

    # Applied filter view
    view = session_df[session_df["event_type"].isin(kinds)]

    # Metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Events", len(view))
    c2.metric("Clicks", int((view["event_type"] == "click").sum()))
    span = (view["ts"].max() - view["ts"].min()) / 1000 if len(view) > 1 else 0
    c3.metric("Span", f"{span:.1f}s")
    c4.metric("Active Types", len(kinds))

    st.subheader("Pointer positions")
    if view.empty:
        st.warning("Nothing matches the current filters.")
    else:
        # Screen y grows downward, so negate it to plot what the user saw.
        plot = view.assign(**{"y (screen down)": -view["y"]})
        st.scatter_chart(
            plot, x="x", y="y (screen down)", color="event_type", height=460
        )

    st.subheader("Raw records")
    st.dataframe(
        view[["id", "session_id", "event_type", "x", "y", "ts", "client_time", "received_at"]],
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "Download Session JSON",
        view.to_json(orient="records", indent=2, date_format="iso"),
        file_name=f"telemetry_{selected_sess[:8]}.json",
        mime="application/json",
    )
