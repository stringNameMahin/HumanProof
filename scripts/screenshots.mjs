// Capture the demo pages and dashboard views for the documentation (docs/images).
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { delay, launch, waitFor } from "./lib/cdp.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = join(root, process.env.HUMANPROOF_SCREENSHOT_DIR || "docs/images");
const api = process.env.HUMANPROOF_API_URL || "http://127.0.0.1:8000";
const dashboard = process.env.HUMANPROOF_DASHBOARD_URL || "http://127.0.0.1:8501";
const only = process.argv.slice(2);

const shots = [
  { name: "lab.png", url: `${api}/`, width: 1365, height: 1180, ready: 'document.getElementById("health").textContent === "API connected"' },
  { name: "sign-in.png", url: `${api}/login/`, width: 1365, height: 860, ready: '!document.getElementById("sign-in").disabled' },
  { name: "dashboard-sessions.png", url: `${dashboard}/?view=Sessions`, width: 1440, height: 2600, settle: 9000 },
  { name: "dashboard-compare.png", url: `${dashboard}/?view=Compare`, width: 1440, height: 1500, settle: 9000 },
  { name: "dashboard-evaluation.png", url: `${dashboard}/?view=Evaluation`, width: 1440, height: 1500, settle: 8000 },
  { name: "dashboard-model.png", url: `${dashboard}/?view=Model`, width: 1440, height: 2200, settle: 8000 },
].filter((shot) => !only.length || only.includes(shot.name));

await mkdir(output, { recursive: true });
const cdp = await launch({ profileDir: join(root, "test-results", `screenshot-profile-${process.pid}`) });
try {
  await cdp.send("Page.enable");
  for (const shot of shots) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: shot.width, height: shot.height, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: shot.url });
    await waitFor(() => cdp.evaluate('document.readyState === "complete"'), shot.url, 20000);
    if (shot.ready) await waitFor(() => cdp.evaluate(shot.ready), `${shot.name} to be ready`, 20000);
    await delay(shot.settle || 800);
    const image = await cdp.send("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: shot.width, height: shot.height, scale: 1 } });
    await writeFile(join(output, shot.name), Buffer.from(image.data, "base64"));
    console.log(`Saved ${join(output, shot.name)}`);
  }
} finally {
  await cdp.close();
}
