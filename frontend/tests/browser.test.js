import assert from "node:assert/strict";
import test from "node:test";
import { collectBrowserSignals, isChromium, sha256 } from "../js/browser.js";

const HEX = /^[0-9a-f]{64}$/;

function context2d() {
  return {
    fillRect() {}, fillText() {}, beginPath() {}, arc() {}, fill() {},
    measureText() { return { width: this.font.includes("Arial") ? 410 : 400 }; },
  };
}

function webgl() {
  return {
    MAX_TEXTURE_SIZE: 1, MAX_RENDERBUFFER_SIZE: 2, MAX_VERTEX_ATTRIBS: 3, MAX_VARYING_VECTORS: 4,
    SHADING_LANGUAGE_VERSION: 5, VENDOR: 6, RENDERER: 7,
    getExtension: () => ({ UNMASKED_VENDOR_WEBGL: 100, UNMASKED_RENDERER_WEBGL: 101 }),
    getParameter: (name) => ({ 100: "Test Vendor", 101: "ANGLE (Test GPU)" })[name] ?? name * 1024,
    getSupportedExtensions: () => ["OES_texture_float"],
  };
}

class OfflineAudio {
  constructor() { this.destination = {}; }
  createOscillator() { return { type: "", frequency: {}, connect() {}, start() {} }; }
  createDynamicsCompressor() {
    return { threshold: {}, knee: {}, ratio: {}, attack: {}, release: {}, connect() {} };
  }
  async startRendering() {
    return { getChannelData: () => Float32Array.from({ length: 4410 }, (_, index) => Math.sin(index) / 10) };
  }
}

function browserScope(overrides = {}) {
  return {
    navigator: {
      webdriver: false, hardwareConcurrency: 8, language: "en-US", languages: ["en-US", "en"],
      plugins: { length: 5 }, maxTouchPoints: 0, deviceMemory: 8, platform: "Win32",
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/156.0.0.0 Safari/537.36",
      userAgentData: { brands: [{ brand: "Chromium", version: "156" }] },
      permissions: { query: async () => ({ state: "prompt" }) },
    },
    document: {
      createElement: () => ({
        width: 0, height: 0,
        getContext: (type) => type === "2d" ? context2d() : webgl(),
        toDataURL: () => "data:image/png;base64,c2FtcGxl",
      }),
    },
    innerWidth: 1280, innerHeight: 720, outerWidth: 1296, outerHeight: 800, devicePixelRatio: 1.25,
    screen: { width: 1920, height: 1080, colorDepth: 24 },
    matchMedia: (query) => ({ matches: query === "(pointer: fine)" }),
    chrome: {}, Notification: { permission: "default" }, console: { debug() {} },
    OfflineAudioContext: OfflineAudio,
    ...overrides,
  };
}

test("all browser probes report values and availability", async () => {
  const signals = await collectBrowserSignals(browserScope());
  assert.equal(signals.webdriver, false);
  assert.equal(signals.plugins_count, 5);
  assert.equal(signals.languages_count, 2);
  assert.equal(signals.has_chrome_object, true);
  assert.equal(signals.pointer_type, "fine");
  assert.equal(signals.webgl_vendor, "Test Vendor");
  assert.equal(signals.webgl_renderer, "ANGLE (Test GPU)");
  assert.equal(signals.permission_query_state, "prompt");
  assert.equal(signals.cdp_detected, false);
  assert.ok(signals.font_count > 0);
  for (const name of ["canvas_hash", "webgl_hash", "audio_hash", "font_hash"]) {
    assert.match(signals[name], HEX, name);
    assert.equal(signals.availability[name], true, name);
  }
  const again = await collectBrowserSignals(browserScope());
  assert.equal(again.canvas_hash, signals.canvas_hash);
  assert.equal(again.audio_hash, signals.audio_hash);
});

test("failing or missing probes become null with explicit availability", async () => {
  const scope = browserScope({
    document: { createElement: () => { throw new Error("blocked"); } },
    OfflineAudioContext: undefined,
    Notification: undefined,
  });
  scope.navigator.permissions.query = async () => { throw new Error("denied"); };
  delete scope.navigator.plugins;
  const signals = await collectBrowserSignals(scope);
  for (const name of ["canvas_hash", "webgl_renderer", "audio_hash", "font_count", "permission_query_state",
    "notification_permission", "plugins_count"]) {
    assert.equal(signals[name], null, name);
    assert.equal(signals.availability[name], false, name);
  }
  assert.equal(signals.hardware_concurrency, 8);
});

test("an attached DevTools Protocol client is detected through error serialisation", async () => {
  const scope = browserScope({ console: { debug: (value) => value.stack } });
  assert.equal((await collectBrowserSignals(scope)).cdp_detected, true);
});

test("helpers hash deterministically and recognise Chromium agents", async () => {
  assert.equal(await sha256("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.equal(isChromium("Mozilla/5.0 Chrome/156.0 Safari/537.36"), true);
  assert.equal(isChromium("Mozilla/5.0 Edg/150.0"), true);
  assert.equal(isChromium("Mozilla/5.0 Firefox/140.0"), false);
});
