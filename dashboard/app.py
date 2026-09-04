"""Streamlit view over the telemetry table."""
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "backend" / "db.sqlite3"
TABLE = "tracker_telemetryevent"

st.set_page_config(page_title="Mouse Telemetry", layout="wide")
st.title("Mouse Telemetry")


@st.cache_data(ttl=5)
def load_events() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        return pd.read_sql_query(f"SELECT * FROM {TABLE} ORDER BY ts", conn)


if st.button("Refresh"):
    st.cache_data.clear()

try:
    df = load_events()
except (sqlite3.OperationalError, pd.errors.DatabaseError):
    df = pd.DataFrame()

if df.empty:
    st.info(
        "No events yet. Run the Django server, open http://127.0.0.1:8000/, "
        "wave the mouse around and hit **Sign in**."
    )
    st.stop()

df["client_time"] = pd.to_datetime(df["ts"], unit="ms")

sessions = sorted(df["session_id"].unique())
picked = st.sidebar.multiselect("Sessions", sessions, default=sessions)
kinds = st.sidebar.multiselect(
    "Event types", sorted(df["event_type"].unique()),
    default=sorted(df["event_type"].unique()),
)
view = df[df["session_id"].isin(picked) & df["event_type"].isin(kinds)]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Events", len(view))
c2.metric("Sessions", view["session_id"].nunique())
c3.metric("Clicks", int((view["event_type"] == "click").sum()))
span = (view["ts"].max() - view["ts"].min()) / 1000 if len(view) > 1 else 0
c4.metric("Span", f"{span:.1f}s")

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
    width="stretch",
    hide_index=True,
)

st.download_button(
    "Download as JSON",
    view.to_json(orient="records", indent=2, date_format="iso"),
    file_name="telemetry.json",
    mime="application/json",
)
