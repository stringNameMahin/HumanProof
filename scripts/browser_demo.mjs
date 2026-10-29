import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

if (process.argv.includes("--help")) {
  console.log("Usage: node scripts/browser_demo.mjs\n\nRun a controlled browser session against the local HumanProof demo.\nStart the Django API and run init_demo first. Requires Node 22+ and Chrome or Edge.\n\nEnvironment:\n  HUMANPROOF_API_URL       Local API origin (default http://127.0.0.1:8000)\n  HUMANPROOF_BROWSER       Optional browser executable path\n  HUMANPROOF_ANALYST_KEY   Optional key; otherwise reads backend/.analyst-key\n  HUMANPROOF_BROWSER_DEBUG  Print browser and protocol diagnostics\n\nSaves screenshots and a report in test-results and labels the completed run bot.\nUses an isolated browser profile and closes only the browser it starts.");
  process.exit(0);
}
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const parsedUrl = new URL(process.env.HUMANPROOF_API_URL || "http://127.0.0.1:8000");
if (!["http:", "https:"].includes(parsedUrl.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(parsedUrl.hostname)
    || parsedUrl.username || parsedUrl.password || parsedUrl.search || parsedUrl.hash || parsedUrl.pathname !== "/") {
  throw new Error("The browser demo requires a local HTTP(S) origin without credentials, paths, or query parameters.");
}
const baseUrl = parsedUrl.origin;
const browserPath = process.env.HUMANPROOF_BROWSER || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find(existsSync);
if (!browserPath) throw new Error("Chrome or Edge was not found. Set HUMANPROOF_BROWSER to its executable.");
const resultDir = join(root, "test-results");
const profile = join(resultDir, `browser-profile-${Date.now()}`);
await mkdir(profile, { recursive: true });
const delay = (ms) => new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
const errors = [];
const failedRequests = [];
let connection;
let browser;
let sequence = 0;
let mouse = { x: 0, y: 0 };
const pending = new Map();

async function waitFor(check, description, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${description}.`);
}

function send(method, params = {}) {
  if (process.env.HUMANPROOF_BROWSER_DEBUG) console.log(`CDP send ${method}`);
  return new Promise((resolveMessage, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP command timed out: ${method}`));
    }, 15000);
    pending.set(id, { resolve: resolveMessage, reject, timeout });
    connection.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const data = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (data.exceptionDetails) throw new Error(data.exceptionDetails.text);
  return data.result.value;
}

async function center(id) {
  return evaluate(`(() => { const element = document.getElementById(${JSON.stringify(id)}); element.scrollIntoView({ block: "center" }); const r = element.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
}

async function moveTo(point, steps = 12) {
  const start = { ...mouse };
  for (let step = 1; step <= steps; step += 1) {
    mouse = { x: start.x + (point.x - start.x) * step / steps, y: start.y + (point.y - start.y) * step / steps };
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", ...mouse });
    await delay(65);
  }
}

async function click(id, steps = 12) {
  const point = await center(id);
  await moveTo(point, steps);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", ...point, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", ...point, button: "left", clickCount: 1 });
}

async function screenshot(name) {
  await evaluate("window.scrollTo(0, 0)");
  await delay(150);
  const metrics = await send("Page.getLayoutMetrics");
  const dimensions = metrics.cssContentSize || metrics.contentSize;
  const result = await send("Page.captureScreenshot", {
    format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: dimensions.width, height: dimensions.height, scale: 1 },
  });
  await writeFile(join(resultDir, name), Buffer.from(result.data, "base64"));
}

try {
  const health = await fetch(`${baseUrl}/health/`);
  if (!health.ok) throw new Error("Start the local Django server before running the browser demo.");
  browser = spawn(browserPath, [
    "--headless=new", "--disable-gpu", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
    `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-extensions", "--disable-sync", "--enable-automation", "about:blank",
  ], { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
  browser.stderr.on("data", (data) => {
    if (process.env.HUMANPROOF_BROWSER_DEBUG) process.stderr.write(data);
  });
  let launchError;
  browser.on("error", (error) => { launchError = error; });
  const port = await waitFor(async () => {
    if (launchError) throw launchError;
    if (browser.exitCode !== null) throw new Error(`Browser exited with code ${browser.exitCode}.`);
    try { return (await readFile(join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]; } catch { return null; }
  }, "the browser debugging port");
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = pages.find((item) => item.type === "page");
  connection = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolveOpen, reject) => {
    connection.addEventListener("open", resolveOpen, { once: true });
    connection.addEventListener("error", reject, { once: true });
  });
  connection.addEventListener("message", (message) => {
    const data = JSON.parse(message.data);
    if (process.env.HUMANPROOF_BROWSER_DEBUG) console.log(`CDP receive ${data.method || data.id}`);
    if (data.id) {
      const command = pending.get(data.id);
      if (!command) return;
      clearTimeout(command.timeout);
      pending.delete(data.id);
      if (data.error) command.reject(new Error(data.error.message));
      else command.resolve(data.result);
      return;
    }
    if (data.method === "Runtime.exceptionThrown") errors.push(data.params.exceptionDetails.exception?.description || data.params.exceptionDetails.text);
    if (data.method === "Runtime.consoleAPICalled" && data.params.type === "error") errors.push(data.params.args.map((item) => item.value || item.description).join(" "));
    if (data.method === "Network.loadingFailed" && !data.params.canceled) failedRequests.push(data.params.errorText);
    if (data.method === "Network.responseReceived" && data.params.response.status >= 400 && !data.params.response.url.endsWith("/favicon.ico")) {
      failedRequests.push(`${data.params.response.status} ${data.params.response.url}`);
    }
  });
  connection.addEventListener("close", (event) => {
    for (const command of pending.values()) {
      clearTimeout(command.timeout);
      command.reject(new Error(`Browser debugging connection closed (${event.code}).`));
    }
    pending.clear();
  });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1365, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: baseUrl });
  await waitFor(() => evaluate('Boolean(document.getElementById("start-session")) && document.readyState === "complete"'), "the collection page");
  assert.equal(await evaluate('document.getElementById("move-count").textContent'), "0");
  await click("start-session", 3);
  await waitFor(() => evaluate('document.getElementById("session-state").textContent === "Collecting"'), "session collection");
  const sessionId = await evaluate('document.getElementById("session-id").textContent');
  const targetTotal = Number(await evaluate('document.getElementById("target-total").textContent'));
  for (let target = 0; target < targetTotal; target += 1) await click("target");
  assert.equal(await evaluate('document.getElementById("target-count").textContent'), String(targetTotal));
  const reading = await center("reading-panel");
  await moveTo(reading, 8);
  for (let scroll = 0; scroll < 5; scroll += 1) {
    await send("Input.dispatchMouseEvent", { type: "mouseWheel", ...reading, deltaX: 0, deltaY: 180 });
    await delay(250);
  }
  assert.equal(await evaluate('document.getElementById("reading-state").textContent'), "Complete");
  await click("focus-area", 5);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 });
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await click("show-reasons", 5);
  await evaluate('document.querySelector("#preference-form button").id = "save-preference"');
  await click("save-preference", 5);
  assert.equal(await evaluate('document.getElementById("choice-state").textContent'), "Saved locally");
  await click("finish-session", 5);
  await waitFor(() => evaluate('document.getElementById("session-state").textContent === "Complete"'), "the completed assessment", 30000);
  assert.equal(await evaluate('document.getElementById("buffer-count").textContent'), "0");
  assert.equal(await evaluate('document.getElementById("assessment-result").hidden'), false);
  assert.equal(await evaluate('document.documentElement.scrollWidth > window.innerWidth'), false);
  await screenshot("demo.png");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await evaluate('document.documentElement.scrollWidth > window.innerWidth'), false);
  await screenshot("demo-mobile.png");

  const version = await send("Browser.getVersion");
  const key = (process.env.HUMANPROOF_ANALYST_KEY || await readFile(join(root, "backend", ".analyst-key"), "utf8")).trim();
  const analystHeaders = { "Content-Type": "application/json", "X-Analyst-Key": key };
  const labelResponse = await fetch(`${baseUrl}/api/v1/sessions/${sessionId}/labels/`, {
    method: "POST", headers: analystHeaders,
    body: JSON.stringify({ label: "bot", source: `controlled-browser-run: ${version.product}; CDP target and scroll task` }),
  });
  assert.equal(labelResponse.ok, true, "The analyst API must accept the browser-run label.");
  const detailsResponse = await fetch(`${baseUrl}/api/v1/sessions/${sessionId}/`, { headers: analystHeaders });
  assert.equal(detailsResponse.ok, true);
  const details = await detailsResponse.json();
  assert.equal(details.state, "complete");
  assert.equal(details.label, "bot");
  assert.equal(details.fingerprint.webdriver, true);
  assert.equal(details.assessment.status, "ready");
  assert.ok(details.event_count >= 20);
  const storedBeforeRestart = await evaluate('document.getElementById("stored-count").textContent');
  await moveTo({ x: 100, y: 100 }, 3);
  assert.equal(await evaluate('document.getElementById("stored-count").textContent'), storedBeforeRestart);
  await click("start-session", 3);
  await waitFor(async () => (await evaluate('document.getElementById("session-id").textContent')) !== sessionId, "a new session ID");
  await click("finish-session", 3);
  await waitFor(() => evaluate('document.getElementById("session-state").textContent === "Complete"'), "the short session assessment");
  assert.ok((await evaluate('document.getElementById("assessment-status").textContent')).includes("insufficient data"));
  assert.equal(await evaluate('document.getElementById("risk-score").textContent'), "--");
  const report = {
    session_id: sessionId, browser: version.product, label: "bot", source: "controlled-browser-run",
    event_count: details.event_count, risk_score: details.assessment.risk_score,
    status: details.assessment.status, available_layers: details.assessment.available_layers,
    restart_and_insufficient_data: "passed", desktop_and_mobile_overflow: "passed",
    javascript_errors: errors, failed_requests: failedRequests,
    screenshots: ["test-results/demo.png", "test-results/demo-mobile.png"],
  };
  await writeFile(join(resultDir, "browser-demo.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
  assert.deepEqual(errors, [], "Browser JavaScript errors occurred.");
  assert.deepEqual(failedRequests, [], "Browser requests failed.");
} catch (error) {
  let pageState = null;
  if (connection?.readyState === WebSocket.OPEN) {
    try {
      pageState = await evaluate('({ state: document.getElementById("session-state")?.textContent, status: document.getElementById("status")?.textContent, upload: document.getElementById("delivery-status")?.textContent })');
      await screenshot("demo-failure.png");
    } catch {}
  }
  console.error(JSON.stringify({ error: error.message, page: pageState, javascript_errors: errors, failed_requests: failedRequests }, null, 2));
  throw error;
} finally {
  if (connection?.readyState === WebSocket.OPEN) {
    try { await send("Browser.close"); } catch {}
    connection.close();
  }
  for (const command of pending.values()) clearTimeout(command.timeout);
  if (browser && browser.exitCode === null) {
    await Promise.race([new Promise((resolveExit) => browser.once("exit", resolveExit)), delay(3000)]);
    if (browser.exitCode === null) browser.kill();
  }
  const resolvedProfile = resolve(profile);
  if (resolvedProfile.startsWith(`${resolve(resultDir)}${sep}`) && resolvedProfile !== resolve(resultDir)) {
    await rm(resolvedProfile, { recursive: true, force: true, maxRetries: 3, retryDelay: 250 }).catch(() => {});
  }
}
