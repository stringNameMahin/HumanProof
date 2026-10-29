import { HumanProofSession } from "./sdk.js";
import { renderLayerBars } from "./render.js";
import { request } from "./transport.js";

const byId = (id) => document.getElementById(id);
const HOLD_MS = 2000;
let session = null;

function setStatus(message, error = false) {
  byId("status").textContent = message;
  byId("status").classList.toggle("error", error);
}

async function protect() {
  session = new HumanProofSession({ task: "sign-in" });
  try {
    const details = await session.start();
    byId("session-id").textContent = details.session_id.slice(0, 8);
    byId("session-id").dataset.sessionId = details.session_id;
    byId("sign-in").disabled = false;
    setStatus("Protected. Fill in the form as you normally would.");
  } catch (error) {
    setStatus(`${error.message} Reload the page to try again.`, true);
  }
}

function showAssessment(result) {
  byId("assessment-empty").hidden = true;
  byId("assessment-result").hidden = false;
  const score = result.risk_score;
  byId("risk-score").textContent = typeof score === "number" ? Math.round(score) : "--";
  byId("decision").textContent = result.decision;
  byId("decision").className = `pill ${result.decision === "block" ? "risk" : result.decision === "challenge" ? "warning" : "active"}`;
  byId("model-version").textContent = result.model_version || "unavailable";
  renderLayerBars(byId("layer-bars"), result.layer_scores);
  const reasons = (result.reasons || []).filter((reason) => reason.points > 0 || reason.code === "insufficient_activity");
  byId("reasons").replaceChildren(...(reasons.length ? reasons : [{ message: "No rule fired for this session." }]).map((reason) => {
    const item = document.createElement("li");
    item.textContent = reason.points ? `${reason.message} (+${reason.points})` : reason.message;
    return item;
  }));
}

function showOutcome(kind, title, copy) {
  byId("challenge").hidden = true;
  byId("outcome").hidden = false;
  byId("outcome").className = `outcome ${kind}`;
  byId("outcome-label").textContent = kind === "block" ? "BLOCKED" : "SIGNED IN";
  byId("outcome-title").textContent = title;
  byId("outcome-copy").textContent = copy;
  byId("again").focus();
}

function startChallenge(result) {
  byId("challenge").hidden = false;
  byId("challenge-reason").textContent = result.status === "ready"
    ? "Some signals in this session look unusual, so we ask for one quick confirmation."
    : "There was not enough interaction to verify this session. Try interacting a little longer next time.";
  const button = byId("hold");
  const fill = byId("hold-fill");
  let started = null;
  let frame = null;
  const reset = () => {
    started = null;
    cancelAnimationFrame(frame);
    fill.style.width = "0%";
  };
  const tick = () => {
    if (started === null) return;
    const progress = Math.min(1, (performance.now() - started) / HOLD_MS);
    fill.style.width = `${progress * 100}%`;
    if (progress < 1) frame = requestAnimationFrame(tick);
    else showOutcome("challenge", "Signed in after a challenge",
      "The challenge is a demo step shown in this page only; a deployment would verify it on the server.");
  };
  const begin = (event) => {
    if (event.type === "keydown" && (event.repeat || ![" ", "Enter"].includes(event.key))) return;
    event.preventDefault();
    if (started === null) {
      started = performance.now();
      frame = requestAnimationFrame(tick);
    }
  };
  button.addEventListener("pointerdown", begin);
  button.addEventListener("keydown", begin);
  for (const type of ["pointerup", "pointerleave", "keyup", "blur"]) button.addEventListener(type, reset);
  button.focus();
}

byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!session) return;
  if (!byId("email").value.trim() || !byId("password").value) {
    setStatus("Enter any email and password; they are not sent.", true);
    return;
  }
  byId("sign-in").disabled = true;
  setStatus("Checking this session...");
  try {
    const result = await session.finish();
    showAssessment(result);
    byId("login-form").hidden = true;
    if (result.decision === "allow") showOutcome("allow", "Signed in", "No challenge was needed for this session.");
    else if (result.decision === "block") {
      showOutcome("block", "Sign-in blocked", "This session looks automated. A deployment would refuse or rate limit the attempt.");
    } else startChallenge(result);
  } catch (error) {
    setStatus(error.message, true);
    byId("sign-in").disabled = false;
  }
});

byId("again").addEventListener("click", () => window.location.reload());

request("/health/").then(() => {
  byId("health").textContent = "API connected";
  byId("health-dot").classList.add("online");
}).catch(() => {
  byId("health").textContent = "API unavailable";
  byId("health-dot").classList.add("offline");
});

protect();
