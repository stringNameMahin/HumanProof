# Mouse Telemetry Lab

A four-part pipeline: a login page that records pointer behaviour, a Django REST
Framework endpoint that receives it, a SQLite table that stores it, and a
Streamlit dashboard that shows it.

```
frontend/index.html  ->  POST /api/telemetry/  ->  SQLite  ->  Streamlit
     (tracker)             (DRF, backend/)      (db.sqlite3) (dashboard/)
```

## Setup (once)

```powershell
python -m venv .venv
# Activate virtual environment
.venv\Scripts\activate

pip install -r requirements.txt
python backend\manage.py migrate
```

## Run

Two terminals.

**1. API and tracker page**

After venv activation:
```powershell
python backend\manage.py runserver
```

Open <http://127.0.0.1:8000/>, move the mouse around the form, click a few
times, solve the CAPTCHA, then press **Sign in** to flush the buffered array to the server.

**2. Dashboard**

For streamlit (after venv activation):
```powershell
python -m streamlit run dashboard/app.py
```

Opens on <http://localhost:8501>.

## What each piece does

**Step 1: `frontend/index.html`.** A plain login form. `mousemove` (throttled to
~20 samples/sec, since the raw event fires ~60x/sec) and `click` listeners push
`{session_id, event_type, x, y, ts}` objects into an in-memory array. The submit
handler `fetch()`es that array to the endpoint as JSON. Nothing is authenticated;
the button is just the flush trigger. On a failed send the batch is pushed back
into the buffer so no samples are lost.

**Step 2: `backend/tracker/views.py`.** One `APIView`. `POST /api/telemetry/`
accepts either a bare JSON array or `{"events": [...]}`, validates it through a
`ModelSerializer` with `many=True`, and bulk-saves. Returns `{"stored": n}`.
`GET` on the same URL dumps the last 50 rows, which is useful for checking the
pipe without opening the dashboard.

**Step 3: storage.** SQLite via the Django ORM (`backend/db.sqlite3`), so there
is no daemon to install. The model is `tracker.TelemetryEvent`; `ts` is the
client clock in epoch milliseconds and `received_at` is the server's own
timestamp, so clock skew between the two is visible. To move to Postgres or
MySQL, only the `DATABASES` block in `telemetry_project/settings.py` changes.

**Step 4: `dashboard/app.py`.** Streamlit reads the table directly with
`pandas.read_sql_query` (read-only connection), filters by session and event
type in the sidebar, and renders a `st.scatter_chart` of the coordinates plus
the raw records. Screen `y` grows downward, so the plot negates it to match what
the pointer actually looked like on screen.

## Notes

- `DEBUG = True`, a hardcoded `SECRET_KEY`, `ALLOWED_HOSTS = ["*"]` and
  `CORS_ALLOW_ALL_ORIGINS = True` are all local-lab settings. None of them are
  safe on anything reachable.
- The endpoint is unauthenticated and unthrottled, so anyone who can reach it can
  write rows. Fine on localhost, not beyond it.
- Coordinates are `clientX`/`clientY`, i.e. relative to the viewport, not the
  page or the screen. Scrolling changes their meaning.
