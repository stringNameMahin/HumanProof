// Minimal Chrome DevTools Protocol client used by the bot fleet.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";

export const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function findBrowser() {
  return process.env.HUMANPROOF_BROWSER || [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ].find(existsSync);
}

export async function waitFor(check, description, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${description}.`);
}

export async function launch({ profileDir, automationFlag = true, disableGpu = true, ignoreCertificates = false,
  windowSize = [1366, 900], extraArgs = [] }) {
  const browserPath = findBrowser();
  if (!browserPath) throw new Error("Chrome or Edge was not found. Set HUMANPROOF_BROWSER to its executable.");
  await mkdir(profileDir, { recursive: true });
  const args = [
    "--headless=new", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
    `--user-data-dir=${profileDir}`, "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-extensions", "--disable-sync",
    `--window-size=${windowSize[0]},${windowSize[1]}`,
    ...(disableGpu ? ["--disable-gpu"] : []),
    ...(automationFlag ? ["--enable-automation"] : []),
    ...(ignoreCertificates ? ["--ignore-certificate-errors"] : []),
    ...extraArgs, "about:blank",
  ];
  const child = spawn(browserPath, args, { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
  child.stderr.on("data", (data) => { if (process.env.HUMANPROOF_BROWSER_DEBUG) process.stderr.write(data); });
  let launchError;
  child.on("error", (error) => { launchError = error; });
  const port = await waitFor(async () => {
    if (launchError) throw launchError;
    if (child.exitCode !== null) throw new Error(`Browser exited with code ${child.exitCode}.`);
    try { return (await readFile(join(profileDir, "DevToolsActivePort"), "utf8")).split("\n")[0]; } catch { return null; }
  }, "the browser debugging port");
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const socket = new WebSocket(pages.find((item) => item.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const pending = new Map();
  const listeners = [];
  let sequence = 0;
  socket.addEventListener("message", (message) => {
    const data = JSON.parse(message.data);
    if (data.id) {
      const command = pending.get(data.id);
      if (!command) return;
      clearTimeout(command.timer);
      pending.delete(data.id);
      if (data.error) command.reject(new Error(data.error.message));
      else command.resolve(data.result);
    } else {
      for (const listener of listeners) listener(data);
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP command timed out: ${method}`)); }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const data = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (data.exceptionDetails) throw new Error(data.exceptionDetails.exception?.description || data.exceptionDetails.text);
    return data.result.value;
  };
  const close = async () => {
    if (socket.readyState === WebSocket.OPEN) {
      try { await send("Browser.close"); } catch {}
      socket.close();
    }
    for (const command of pending.values()) clearTimeout(command.timer);
    if (child.exitCode === null) {
      await Promise.race([new Promise((resolve) => child.once("exit", resolve)), delay(3000)]);
      if (child.exitCode === null) child.kill();
    }
    await rm(profileDir, { recursive: true, force: true, maxRetries: 3, retryDelay: 250 }).catch(() => {});
  };
  return { send, evaluate, close, onEvent: (listener) => listeners.push(listener) };
}
