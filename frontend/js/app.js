import { HumanProofSession } from "./sdk.js";
import { request } from "./transport.js";

const byId = (id) => document.getElementById(id);
const startButton = byId("start-session");
const finishButton = byId("finish-session");
const tasks = byId("tasks");
const target = byId("target");
const positions = [[22, 32], [74, 18], [46, 60], [12, 52], [70, 56], [42, 20]];
let session = null;
let timer = null;
let hitCount = 0;
let counts = { move: 0, click: 0, scroll: 0, focus: 0, shortcut: 0 };

function setStatus(message, error = false) {
  byId("status").textContent = message;
  byId("status").classList.toggle("error", error);
}

function setState(label, tone = "") {
  byId("session-state").textContent = label;
  byId("session-state").className = `pill ${tone}`;
}

function setLayer(name, available, waiting = "Waiting") {
  const element = byId(`${name}-layer`);
  element.textContent = available ? "Collected" : waiting;
  element.classList.toggle("available", available);
}

function updateTransport(state) {
  byId("stored-count").textContent = state.acknowledged.toLocaleString("en-US");
  byId("buffer-count").textContent = state.buffered.toLocaleString("en-US");
  byId("delivery-status").textContent = state.error || (state.sending ? "Sending a batch..." : state.buffered ? "Automatic upload every 3 seconds" : "All buffered events acknowledged");
  byId("delivery-status").classList.toggle("error", Boolean(state.error));
  byId("drop-warning").hidden = state.dropped === 0;
  byId("drop-warning").textContent = `${state.dropped} events dropped because the buffer filled. The session has incomplete evidence.`;
}

function updateEvent(event) {
  counts[event.event_type] += 1;
  if (["move", "click", "scroll"].includes(event.event_type)) byId(`${event.event_type}-count`).textContent = counts[event.event_type].toLocaleString("en-US");
  setLayer("mouse", counts.move > 0);
  setLayer("timing", counts.click + counts.scroll + counts.shortcut > 0);
}

function updateElapsed() {
  if (session?.startedAt === null || !session) return;
  const seconds = Math.floor((performance.now() - session.startedAt) / 1000);
  byId("elapsed").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function placeTarget() {
  const position = positions[Math.min(hitCount, positions.length - 1)];
  target.style.left = `${position[0]}%`;
  target.style.top = `${position[1]}%`;
  target.textContent = hitCount < 6 ? String(hitCount + 1) : "OK";
  target.setAttribute("aria-label", hitCount < 6 ? `Select target ${hitCount + 1}` : "Target task complete");
  target.disabled = hitCount >= 6;
  byId("target-count").textContent = hitCount;
}

function resetView() {
  counts = { move: 0, click: 0, scroll: 0, focus: 0, shortcut: 0 };
  for (const name of ["move", "click", "scroll", "stored", "buffer"]) byId(`${name}-count`).textContent = "0";
  hitCount = 0;
  placeTarget();
  byId("target-hint").textContent = "Targets appear in different positions.";
  byId("reading-panel").scrollTop = 0;
  byId("reading-state").textContent = "Try scrolling";
  byId("choice-state").textContent = "Optional";
  byId("preference-form").reset();
  byId("elapsed").textContent = "00:00";
  byId("drop-warning").hidden = true;
  byId("assessment-empty").hidden = false;
  byId("assessment-result").hidden = true;
  for (const name of ["browser", "mouse", "timing"]) setLayer(name, false);
}

function readable(value) {
  return String(value || "unavailable").replaceAll("_", " ");
}

function showAssessment(result) {
  byId("assessment-empty").hidden = true;
  byId("assessment-result").hidden = false;
  const score = result.risk_score;
  byId("risk-score").textContent = typeof score === "number" && Number.isFinite(score) ? Math.round(score) : "--";
  byId("decision").textContent = readable(result.decision);
  byId("decision").className = `pill ${score >= 60 ? "risk" : score >= 30 ? "warning" : ""}`;
  byId("assessment-status").textContent = `Status: ${readable(result.status)}. Observation mode: no access is blocked.`;
  byId("model-version").textContent = result.model_version || "Unavailable";
  byId("reasons").replaceChildren();
  const reasons = Array.isArray(result.reasons) ? result.reasons : [];
  for (const reason of reasons) {
    const item = document.createElement("li");
    item.textContent = typeof reason === "string" ? readable(reason) : reason.message || reason.description || readable(reason.code);
    byId("reasons").append(item);
  }
  if (!reasons.length) {
    const item = document.createElement("li");
    item.textContent = "No additional reasons were returned for this session.";
    byId("reasons").append(item);
  }
  if (Array.isArray(result.available_layers)) {
    for (const layer of ["browser", "mouse", "timing"]) setLayer(layer, result.available_layers.includes(layer), "Insufficient");
  }
}

startButton.addEventListener("click", async () => {
  startButton.disabled = true;
  finishButton.disabled = true;
  session?.dispose();
  clearInterval(timer);
  resetView();
  setState("Starting");
  setStatus("Creating a session and connecting the collectors...");
  session = new HumanProofSession({
    onEvent: updateEvent,
    onTransport: updateTransport,
    onBrowser: (available) => setLayer("browser", available, "Upload pending"),
  });
  try {
    const details = await session.start();
    byId("session-id").textContent = details.session_id;
    tasks.disabled = false;
    finishButton.disabled = false;
    finishButton.textContent = "Finish and assess";
    setState("Collecting", "active");
    byId("live-label").textContent = "LIVE";
    byId("live-label").classList.add("active");
    setStatus("Session active. Try the targets, scroll the reading panel, and make a choice.");
    timer = setInterval(updateElapsed, 500);
    target.focus();
  } catch (error) {
    session.dispose();
    tasks.disabled = true;
    setState("Connection error", "warning");
    setStatus(error.message, true);
    startButton.disabled = false;
    startButton.textContent = "Try starting again";
  }
});

finishButton.addEventListener("click", async () => {
  if (!session) return;
  finishButton.disabled = true;
  tasks.disabled = true;
  clearInterval(timer);
  updateElapsed();
  setState("Assessing");
  byId("live-label").textContent = "PAUSED";
  byId("live-label").classList.remove("active");
  setStatus("Stopping collection, saving remaining events, and assessing the session...");
  try {
    const result = await session.finish();
    showAssessment(result);
    setState("Complete", "active");
    byId("live-label").textContent = "SAVED";
    const warning = session.transport.dropped ? " Some events were dropped; inspect the data quality flags." : "";
    setStatus(result.browserError ? `Assessment saved. Browser signals could not be saved: ${result.browserError}` : `Session saved. Review the assessment and inspect this session in the analyst dashboard.${warning}`, Boolean(result.browserError));
    startButton.disabled = false;
    startButton.textContent = "Start new session";
  } catch (error) {
    setState("Save pending", "warning");
    if (error.retryable === false) {
      setStatus(`${error.message} This session could not be finalized. Starting a new session will discard any events still buffered in this tab.`, true);
      startButton.disabled = false;
      startButton.textContent = "Start new session";
    } else {
      setStatus(`${error.message} Buffered events are retained in this tab. Select Retry finish after restoring the connection.`, true);
      finishButton.disabled = false;
      finishButton.textContent = "Retry finish";
    }
  }
});

target.addEventListener("click", () => {
  if (tasks.disabled || hitCount >= 6) return;
  hitCount += 1;
  placeTarget();
  if (hitCount === 6) {
    byId("target-hint").textContent = "Target task complete. Continue with the reading panel.";
    byId("reading-panel").focus({ preventScroll: true });
  }
});

byId("reading-panel").addEventListener("scroll", (event) => {
  if (tasks.disabled) return;
  const panel = event.currentTarget;
  byId("reading-state").textContent = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 8 ? "Complete" : "In progress";
}, { passive: true });

byId("preference-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (tasks.disabled) return;
  if (!byId("focus-area").value) {
    byId("choice-state").textContent = "Choose an option";
    byId("focus-area").focus();
    return;
  }
  byId("choice-state").textContent = "Saved locally";
});

request("/health/").then(() => {
  byId("health").textContent = "API connected";
  byId("health-dot").classList.add("online");
}).catch(() => {
  byId("health").textContent = "API unavailable";
  byId("health-dot").classList.add("offline");
});
