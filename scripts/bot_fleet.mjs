// Controlled automation runs against the HumanProof interaction lab, for the bot side of the dataset.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { delay, findBrowser, launch, waitFor } from "./lib/cdp.mjs";

const HELP = `Usage: node scripts/bot_fleet.mjs [--profiles a,b] [--runs N] [--transport http|edge|mixed]
                                [--page lab|sign-in] [--seed N] [--no-label]

Profiles:
  cdp-linear     Straight fixed-step paths, centre clicks, instant presses, automation flag (naive script)
  cdp-teleport   Clicks and wheel input with no pointer movement, automation flag
  cdp-stealth    Bezier paths with Fitts-like timing, jitter, overshoot, press dwell, human-like typing,
                 cleaned user agent, no automation flag (ghost-cursor + stealth plugin style)
  js-synthetic   Page-script events: dispatched pointer events, element.click(), scrollTop, value + input event
  puppeteer-chrome  Puppeteer with page.click, page.type, mouse.wheel, and page.select (needs: npm install)

Pages: lab (default) runs the interaction lab tasks; sign-in fills the protected sign-in demo.
Every completed session is labelled "bot" with source "bot-fleet: <profile> v1" and group <profile>,
unless --no-label is given (useful for a live demo before labelling).
The Django API must run with a raised HUMANPROOF_SESSION_RATE (scripts/start_demo.ps1 does this).

Environment: HUMANPROOF_API_URL (default http://127.0.0.1:8000), HUMANPROOF_EDGE_URL (default
https://127.0.0.1:8443), HUMANPROOF_ANALYST_KEY (else backend/.analyst-key), HUMANPROOF_BROWSER.`;

const PROFILES = ["cdp-linear", "cdp-teleport", "cdp-stealth", "js-synthetic", "puppeteer-chrome"];
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const resultDir = join(root, "test-results");

function parseArgs(argv) {
  const options = { profiles: PROFILES, runs: 1, transport: "http", page: "lab", label: true, seed: Date.now() % 100000 };
  for (let index = 0; index < argv.length; index += 1) {
    const [name, value] = [argv[index], argv[index + 1]];
    if (name === "--help") { console.log(HELP); process.exit(0); }
    if (name === "--no-label") { options.label = false; continue; }
    if (name === "--profiles") options.profiles = value.split(",").map((item) => item.trim()).filter(Boolean);
    else if (name === "--runs") options.runs = Number(value);
    else if (name === "--transport") options.transport = value;
    else if (name === "--seed") options.seed = Number(value);
    else if (name === "--page") options.page = value;
    else throw new Error(`Unknown option ${name}. Use --help.`);
    index += 1;
  }
  const unknown = options.profiles.filter((profile) => !PROFILES.includes(profile));
  if (unknown.length) throw new Error(`Unknown profile: ${unknown.join(", ")}`);
  if (!Number.isInteger(options.runs) || options.runs < 1 || options.runs > 100) throw new Error("--runs must be 1 to 100.");
  if (!["http", "edge", "mixed"].includes(options.transport)) throw new Error("--transport must be http, edge, or mixed.");
  if (!["lab", "sign-in"].includes(options.page)) throw new Error("--page must be lab or sign-in.");
  return options;
}

function localUrl(value, name) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
    throw new Error(`${name} must be a local HTTP(S) origin.`);
  }
  return url.origin;
}

function rng(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value ^= value + Math.imul(value ^ (value >>> 7), 61 | value);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

class Driver {
  constructor(cdp, style, random) {
    this.cdp = cdp;
    this.style = style;
    this.random = random;
    this.mouse = { x: 60 + random() * 300, y: 40 + random() * 120 };
  }

  between(low, high) { return low + (high - low) * this.random(); }

  gaussian() {
    const u = Math.max(this.random(), 1e-9);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * this.random());
  }

  async pause(low, high) { await delay(this.style.motion === "humanized" ? this.between(low, high) : low); }

  rect(id) {
    return this.cdp.evaluate(`(() => { const r = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height, viewport: window.innerHeight }; })()`);
  }

  async reveal(id) {
    if (this.style.scroll === "programmatic") {
      await this.cdp.evaluate(`document.getElementById(${JSON.stringify(id)}).scrollIntoView({ block: "center" })`);
      await delay(120);
      return this.rect(id);
    }
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const box = await this.rect(id);
      if (box.y >= 40 && box.y + box.height <= box.viewport - 40) return box;
      const deltaY = Math.max(-400, Math.min(400, box.y - box.viewport / 2));
      await this.wheel(this.mouse, deltaY * this.between(0.7, 1.0));
      await this.pause(150, 350);
    }
    return this.rect(id);
  }

  async dispatchMove(point) {
    this.mouse = point;
    await this.cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: point.x, y: point.y });
  }

  async moveTo(target, width = 40) {
    if (this.style.motion === "none") return;
    if (this.style.motion === "linear") {
      const start = { ...this.mouse };
      for (let step = 1; step <= 12; step += 1) {
        await this.dispatchMove({ x: start.x + (target.x - start.x) * step / 12, y: start.y + (target.y - start.y) * step / 12 });
        await delay(65);
      }
      return;
    }
    await this.humanMove(target, width, true);
  }

  async humanMove(target, width, allowOvershoot) {
    const start = { ...this.mouse };
    const dx = target.x - start.x;
    const dy = target.y - start.y;
    const distance = Math.hypot(dx, dy);
    if (distance < 2) return;
    const unit = { x: dx / distance, y: dy / distance };
    const normal = { x: -unit.y, y: unit.x };
    const overshoot = allowOvershoot && distance > 120 && this.random() < 0.3;
    const end = overshoot ? { x: target.x + unit.x * this.between(5, 14), y: target.y + unit.y * this.between(5, 14) } : target;
    const bend = this.gaussian() * distance * 0.12;
    const control1 = { x: start.x + dx * 0.3 + normal.x * bend, y: start.y + dy * 0.3 + normal.y * bend };
    const control2 = { x: start.x + dx * 0.7 + normal.x * bend * 0.6, y: start.y + dy * 0.7 + normal.y * bend * 0.6 };
    const difficulty = Math.log2(distance / Math.max(width, 8) + 1);
    const duration = Math.min(1800, Math.max(150, (180 + 140 * difficulty) * this.between(0.8, 1.2)));
    let elapsed = 0;
    while (elapsed < duration) {
      const step = this.between(14, 20);
      elapsed = Math.min(duration, elapsed + step);
      const tau = elapsed / duration;
      const p = 10 * tau ** 3 - 15 * tau ** 4 + 6 * tau ** 5;
      const q = 1 - p;
      const x = q ** 3 * start.x + 3 * q * q * p * control1.x + 3 * q * p * p * control2.x + p ** 3 * end.x;
      const y = q ** 3 * start.y + 3 * q * q * p * control1.y + 3 * q * p * p * control2.y + p ** 3 * end.y;
      await this.dispatchMove({ x: x + this.gaussian() * 0.5, y: y + this.gaussian() * 0.5 });
      await delay(step);
    }
    if (overshoot) {
      await delay(this.between(60, 160));
      await this.humanMove(target, width, false);
    }
  }

  async press(point) {
    const event = { x: point.x, y: point.y, button: "left", clickCount: 1 };
    await this.cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", ...event });
    if (this.style.motion === "humanized") await delay(this.between(60, 140));
    await this.cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...event });
  }

  async click(id) {
    const box = await this.reveal(id);
    let point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    if (this.style.motion === "humanized") {
      const spread = Math.min(box.width, box.height) / 5;
      point = { x: point.x + Math.max(-0.4, Math.min(0.4, this.gaussian() * spread / box.width)) * box.width,
                y: point.y + Math.max(-0.4, Math.min(0.4, this.gaussian() * spread / box.height)) * box.height };
    }
    await this.moveTo(point, Math.min(box.width, box.height));
    if (this.style.motion === "humanized") await delay(this.between(40, 160));
    await this.press(point);
  }

  async wheel(point, deltaY) {
    await this.cdp.send("Input.dispatchMouseEvent", { type: "mouseWheel", x: point.x, y: point.y, deltaX: 0, deltaY });
  }

  async key(name, code, virtualKey, text) {
    const base = { key: name, code, windowsVirtualKeyCode: virtualKey };
    await this.cdp.send("Input.dispatchKeyEvent", { type: "keyDown", ...base, ...(text ? { text } : {}) });
    if (this.style.typing === "human") await delay(this.between(55, 130));
    await this.cdp.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
  }

  async type(text) {
    if (this.style.typing === "insert") {
      await this.cdp.send("Input.insertText", { text });
      return;
    }
    for (const character of text) {
      const upper = character.toUpperCase();
      if (character === " ") await this.key(" ", "Space", 32, " ");
      else await this.key(character, `Key${upper}`, upper.charCodeAt(0), character);
      await delay(this.style.typing === "human" ? this.between(70, 240) : 30);
    }
  }
}

const STYLES = {
  "cdp-linear": { launch: { automationFlag: true }, motion: "linear", scroll: "programmatic", typing: "insert" },
  "cdp-teleport": { launch: { automationFlag: true }, motion: "none", scroll: "programmatic", typing: "instant" },
  "cdp-stealth": { launch: { automationFlag: false, disableGpu: false }, motion: "humanized", scroll: "wheel", typing: "human" },
};

async function prepareStealth(cdp) {
  const version = await cdp.send("Browser.getVersion");
  const userAgent = version.userAgent.replace("HeadlessChrome", "Chrome");
  const major = (version.product.match(/\/(\d+)/) || [])[1] || "0";
  await cdp.send("Network.setUserAgentOverride", {
    userAgent, acceptLanguage: "en-US,en;q=0.9", platform: "Win32",
    userAgentMetadata: {
      brands: [{ brand: "Chromium", version: major }, { brand: "Google Chrome", version: major }, { brand: "Not=A?Brand", version: "24" }],
      fullVersion: `${major}.0.0.0`, platform: "Windows", platformVersion: "15.0.0", architecture: "x86", model: "", mobile: false,
    },
  });
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: 1350, height: 780, deviceScaleFactor: 1, mobile: false, screenWidth: 1920, screenHeight: 1080,
  });
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
    source: "Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false });",
  });
}

async function runLabTasks(driver) {
  const { cdp } = driver;
  await driver.pause(400, 900);
  await driver.click("start-session");
  await waitFor(() => cdp.evaluate('document.getElementById("session-state").textContent === "Collecting"'), "session collection");
  const total = Number(await cdp.evaluate('document.getElementById("target-total").textContent'));
  for (let target = 0; target < total; target += 1) {
    await driver.click("target");
    await driver.pause(150, 600);
  }
  const panel = await driver.reveal("reading-panel");
  const inside = { x: panel.x + panel.width * driver.between(0.3, 0.7), y: panel.y + panel.height * driver.between(0.3, 0.7) };
  await driver.moveTo(inside, panel.width);
  for (let scroll = 0; scroll < 6; scroll += 1) {
    await driver.wheel(inside, driver.style.motion === "humanized" ? driver.between(80, 220) : 180);
    await delay(driver.style.motion === "humanized" ? driver.between(120, 420) : 250);
  }
  await driver.pause(300, 900);
  await driver.click("focus-area");
  await driver.key("ArrowDown", "ArrowDown", 40);
  await driver.key("Enter", "Enter", 13);
  await driver.pause(200, 600);
  await driver.click("note");
  await driver.type("trying the demo");
  await driver.pause(200, 500);
  await driver.click("show-reasons");
  await cdp.evaluate('document.querySelector("#preference-form button").id = "save-preference"');
  await driver.click("save-preference");
  await driver.pause(300, 700);
  await driver.click("finish-session");
}

async function runSyntheticTasks(cdp) {
  await cdp.evaluate(`(async () => {
    const wait = (ms) => new Promise((done) => setTimeout(done, ms));
    const byId = (id) => document.getElementById(id);
    const started = performance.now();
    const fire = (x, y) => document.dispatchEvent(new PointerEvent("pointermove",
      { clientX: x, clientY: y, isPrimary: true, pointerType: "mouse", bubbles: true }));
    byId("start-session").click();
    while (byId("session-state").textContent !== "Collecting") await wait(50);
    const total = Number(byId("target-total").textContent);
    let last = { x: 50, y: 50 };
    for (let index = 0; index < total; index += 1) {
      const target = byId("target");
      target.scrollIntoView({ block: "center" });
      const box = target.getBoundingClientRect();
      const end = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      for (let step = 1; step <= 10; step += 1) {
        fire(last.x + (end.x - last.x) * step / 10, last.y + (end.y - last.y) * step / 10);
        await wait(30);
      }
      last = end;
      target.click();
      await wait(220);
    }
    const panel = byId("reading-panel");
    for (let step = 0; step < 8; step += 1) { panel.scrollTop += 120; await wait(120); }
    const select = byId("focus-area");
    select.value = "mouse";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    const note = byId("note");
    note.value = "scripted run";
    note.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "scripted run" }));
    byId("show-reasons").click();
    document.querySelector("#preference-form button").click();
    while (performance.now() - started < 7000) await wait(100);
    byId("finish-session").click();
    return true;
  })()`);
}

async function runSignInTasks(driver) {
  const { cdp } = driver;
  await waitFor(() => cdp.evaluate('!document.getElementById("sign-in").disabled'), "sign-in protection");
  await driver.pause(600, 1500);
  await driver.click("email");
  await driver.type("alex@example.com");
  await driver.pause(250, 700);
  await driver.click("password");
  await driver.type("correct horse");
  await driver.pause(400, 900);
  if (driver.style.motion !== "humanized") await delay(2500);
  await driver.click("sign-in");
}

async function runSyntheticSignIn(cdp) {
  await cdp.evaluate(`(async () => {
    const wait = (ms) => new Promise((done) => setTimeout(done, ms));
    const byId = (id) => document.getElementById(id);
    while (byId("sign-in").disabled) await wait(50);
    for (let step = 0; step < 30; step += 1) {
      document.dispatchEvent(new PointerEvent("pointermove", { clientX: 200 + step * 8, clientY: 300, isPrimary: true, bubbles: true }));
      await wait(40);
    }
    for (const [id, value] of [["email", "alex@example.com"], ["password", "correct horse"]]) {
      byId(id).value = value;
      byId(id).dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    }
    await wait(5200);
    byId("sign-in").click();
    return true;
  })()`);
}

async function runPuppeteer({ pageUrl, edge, page: pageKind, seed }) {
  let puppeteer;
  try {
    ({ default: puppeteer } = await import("puppeteer-core"));
  } catch {
    throw new Error("puppeteer-core is not installed. Run: npm install");
  }
  const random = rng(seed);
  const browser = await puppeteer.launch({
    executablePath: findBrowser(), headless: true,
    args: ["--window-size=1366,1000", ...(edge ? ["--ignore-certificate-errors"] : [])],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1350, height: 900 });
    if (pageKind === "sign-in") {
      await page.goto(`${pageUrl}/login/`, { waitUntil: "load" });
      await page.waitForFunction('!document.getElementById("sign-in").disabled');
      await page.type("#email", "alex@example.com", { delay: 40 + random() * 40 });
      await page.type("#password", "correct horse", { delay: 50 });
      await delay(2500);
      await page.click("#sign-in");
      await page.waitForFunction('!document.getElementById("outcome").hidden || !document.getElementById("challenge").hidden', { timeout: 30000 });
      return { sessionId: await page.$eval("#session-id", (element) => element.dataset.sessionId), version: await browser.version() };
    }
    await page.goto(pageUrl, { waitUntil: "load" });
    await page.click("#start-session");
    await page.waitForFunction('document.getElementById("session-state").textContent === "Collecting"');
    const total = Number(await page.$eval("#target-total", (element) => element.textContent));
    for (let index = 0; index < total; index += 1) {
      await page.click("#target");
      await delay(200 + random() * 300);
    }
    const panel = await page.$eval("#reading-panel", (element) => {
      element.scrollIntoView({ block: "center" });
      const box = element.getBoundingClientRect();
      return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    });
    await page.mouse.move(panel.x, panel.y, { steps: 10 });
    for (let index = 0; index < 6; index += 1) {
      await page.mouse.wheel({ deltaY: 160 });
      await delay(250);
    }
    await page.select("#focus-area", "mouse");
    await page.type("#note", "puppeteer run", { delay: 60 });
    await page.click("#show-reasons");
    await page.click("#preference-form button");
    await delay(800);
    await page.click("#finish-session");
    await page.waitForFunction('document.getElementById("session-state").textContent === "Complete"', { timeout: 30000 });
    return { sessionId: await page.$eval("#session-id", (element) => element.textContent), version: await browser.version() };
  } finally {
    await browser.close();
  }
}

async function analystKey() {
  return (process.env.HUMANPROOF_ANALYST_KEY || await readFile(join(root, "backend", ".analyst-key"), "utf8")).trim();
}

async function labelAndFetch({ apiUrl, key, sessionId, profile, page, product, edge, shouldLabel }) {
  const headers = { "Content-Type": "application/json", "X-Analyst-Key": key };
  if (shouldLabel) {
    const label = await fetch(`${apiUrl}/api/v1/sessions/${sessionId}/labels/`, {
      method: "POST", headers,
      body: JSON.stringify({ label: "bot", source: `bot-fleet: ${profile} v1; ${page}; ${product}; ${edge ? "tls-edge" : "http"}`, group: profile }),
    });
    if (!label.ok) throw new Error(`Labelling failed with HTTP ${label.status}.`);
  }
  const details = await (await fetch(`${apiUrl}/api/v1/sessions/${sessionId}/`, { headers })).json();
  return { profile, page, transport: edge ? "edge" : "http", session_id: sessionId, decision: details.assessment?.decision,
    status: details.assessment?.status, risk_score: details.assessment?.risk_score, model_version: details.assessment?.model_version,
    reasons: (details.assessment?.reasons || []).map((reason) => reason.code), events: details.event_count };
}

async function runOnce({ profile, pageUrl, apiUrl, edge, seed, key, index, page, shouldLabel }) {
  if (profile === "puppeteer-chrome") {
    const { sessionId, version } = await runPuppeteer({ pageUrl, edge, page, seed });
    return labelAndFetch({ apiUrl, key, sessionId, profile, page, product: version, edge, shouldLabel });
  }
  const style = STYLES[profile] || { launch: { automationFlag: true } };
  const cdp = await launch({ profileDir: join(resultDir, `fleet-profile-${process.pid}-${index}`),
    ignoreCertificates: edge, ...style.launch });
  const errors = [];
  cdp.onEvent((data) => {
    if (data.method === "Runtime.exceptionThrown") errors.push(data.params.exceptionDetails.text);
  });
  try {
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    if (profile === "cdp-stealth") await prepareStealth(cdp);
    else await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1365, height: 1000, deviceScaleFactor: 1, mobile: false });
    const signIn = page === "sign-in";
    await cdp.send("Page.navigate", { url: signIn ? `${pageUrl}/login/` : pageUrl });
    const ready = signIn ? 'Boolean(document.getElementById("sign-in"))' : 'Boolean(document.getElementById("start-session"))';
    await waitFor(() => cdp.evaluate(`${ready} && document.readyState === "complete"`), "the page");
    if (signIn) {
      if (profile === "js-synthetic") await runSyntheticSignIn(cdp);
      else await runSignInTasks(new Driver(cdp, style, rng(seed)));
      await waitFor(() => cdp.evaluate('!document.getElementById("outcome").hidden || !document.getElementById("challenge").hidden'), "the decision", 30000);
    } else {
      if (profile === "js-synthetic") await runSyntheticTasks(cdp);
      else await runLabTasks(new Driver(cdp, style, rng(seed)));
      await waitFor(() => cdp.evaluate('document.getElementById("session-state").textContent === "Complete"'), "the assessment", 30000);
    }
    const sessionId = await cdp.evaluate(signIn ? 'document.getElementById("session-id").dataset.sessionId'
      : 'document.getElementById("session-id").textContent');
    const version = await cdp.send("Browser.getVersion");
    return { ...await labelAndFetch({ apiUrl, key, sessionId, profile, page, product: version.product, edge, shouldLabel }),
      page_errors: errors };
  } finally {
    await cdp.close();
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const apiUrl = localUrl(process.env.HUMANPROOF_API_URL || "http://127.0.0.1:8000", "HUMANPROOF_API_URL");
  const edgeUrl = localUrl(process.env.HUMANPROOF_EDGE_URL || "https://127.0.0.1:8443", "HUMANPROOF_EDGE_URL");
  const health = await fetch(`${apiUrl}/health/`).catch(() => null);
  if (!health?.ok) throw new Error("Start the Django API first (scripts/start_demo.ps1).");
  const key = await analystKey();
  await mkdir(resultDir, { recursive: true });
  const results = [];
  let index = 0;
  for (let run = 0; run < options.runs; run += 1) {
    for (const [position, profile] of options.profiles.entries()) {
      // Alternate each profile between transports across runs, so transport never stands in for the family.
      const edge = options.transport === "edge" || (options.transport === "mixed" && (run + position) % 2 === 1);
      const seed = options.seed + index * 7919;
      index += 1;
      try {
        const result = await runOnce({ profile, pageUrl: edge ? edgeUrl : apiUrl, apiUrl, edge, seed, key, index,
          page: options.page, shouldLabel: options.label });
        results.push(result);
        console.log(JSON.stringify(result));
      } catch (error) {
        results.push({ profile, error: error.message });
        console.error(JSON.stringify({ profile, error: error.message }));
      }
    }
  }
  await writeFile(join(resultDir, "bot-fleet.json"), `${JSON.stringify(results, null, 2)}\n`);
  const failed = results.filter((item) => item.error).length;
  console.log(`Completed ${results.length - failed} of ${results.length} runs. Report: test-results/bot-fleet.json`);
  process.exitCode = failed === results.length ? 1 : 0;
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
