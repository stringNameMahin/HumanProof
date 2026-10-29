const FONT_CANDIDATES = [
  "Arial", "Arial Black", "Calibri", "Cambria", "Candara", "Comic Sans MS", "Consolas", "Constantia",
  "Corbel", "Courier New", "Franklin Gothic Medium", "Gabriola", "Georgia", "Helvetica", "Impact",
  "Lucida Console", "Lucida Sans Unicode", "Microsoft Sans Serif", "Palatino Linotype", "Segoe Print",
  "Segoe Script", "Segoe UI", "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana", "Menlo", "Monaco",
  "Noto Sans", "Roboto", "Ubuntu", "DejaVu Sans", "Liberation Sans", "Cantarell", "Fira Sans", "Helvetica Neue",
];

export async function sha256(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

export function isChromium(userAgent) {
  return /(Chrome|Chromium|CriOS|Edg)\//.test(userAgent || "") && !/Firefox\//.test(userAgent || "");
}

function canvasProbe(doc) {
  const canvas = doc.createElement("canvas");
  canvas.width = 240;
  canvas.height = 60;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.textBaseline = "alphabetic";
  context.fillStyle = "#f60";
  context.fillRect(100, 1, 62, 20);
  context.fillStyle = "#069";
  context.font = "15px 'Arial'";
  context.fillText("HumanProof <canvas> 1.0 \u{1F50D}", 4, 22);
  context.fillStyle = "rgba(102, 204, 0, 0.7)";
  context.font = "18px 'Times New Roman'";
  context.fillText("Cwm fjordbank glyphs vext quiz", 6, 48);
  context.globalCompositeOperation = "multiply";
  context.beginPath();
  context.arc(200, 30, 18, 0, Math.PI * 2);
  context.fill();
  return canvas.toDataURL();
}

function webglProbe(doc) {
  const canvas = doc.createElement("canvas");
  const gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
  if (!gl) return null;
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const parameters = [
    gl.getParameter(gl.MAX_TEXTURE_SIZE), gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
    gl.getParameter(gl.MAX_VERTEX_ATTRIBS), gl.getParameter(gl.MAX_VARYING_VECTORS),
    gl.getParameter(gl.SHADING_LANGUAGE_VERSION), (gl.getSupportedExtensions() || []).join(","),
  ];
  return {
    vendor: String(debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR)).slice(0, 256),
    renderer: String(debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)).slice(0, 256),
    parameters: parameters.join("|"),
  };
}

async function audioProbe(scope) {
  const OfflineContext = scope.OfflineAudioContext || scope.webkitOfflineAudioContext;
  if (!OfflineContext) return null;
  const context = new OfflineContext(1, 4410, 44100);
  const oscillator = context.createOscillator();
  oscillator.type = "triangle";
  oscillator.frequency.value = 10000;
  const compressor = context.createDynamicsCompressor();
  compressor.threshold.value = -50;
  compressor.knee.value = 40;
  compressor.ratio.value = 12;
  compressor.attack.value = 0;
  compressor.release.value = 0.25;
  oscillator.connect(compressor);
  compressor.connect(context.destination);
  oscillator.start(0);
  const buffer = await Promise.race([
    context.startRendering(),
    new Promise((resolve) => setTimeout(() => resolve(null), 1000)),
  ]);
  if (!buffer) return null;
  const samples = buffer.getChannelData(0);
  let total = 0;
  for (let index = 4000; index < samples.length; index += 1) total += Math.abs(samples[index]);
  return total.toFixed(6);
}

function fontProbe(doc) {
  const context = doc.createElement("canvas").getContext("2d");
  if (!context) return null;
  const sample = "mmmmmmmmmmlli1WQ@#";
  const bases = ["monospace", "sans-serif", "serif"];
  const width = (family) => {
    context.font = `72px ${family}`;
    return context.measureText(sample).width;
  };
  const baseWidths = bases.map(width);
  return FONT_CANDIDATES.filter((font) => bases.some((base, index) => width(`'${font}', ${base}`) !== baseWidths[index]));
}

function cdpProbe(consoleObject) {
  // Serialising an Error for an attached DevTools Protocol client reads its stack property.
  let detected = false;
  const probe = new Error("humanproof");
  Object.defineProperty(probe, "stack", { configurable: true, get() { detected = true; return ""; } });
  consoleObject.debug(probe);
  return detected;
}

async function notificationQueryState(nav) {
  if (!nav.permissions?.query) return null;
  const status = await nav.permissions.query({ name: "notifications" });
  return ["prompt", "granted", "denied"].includes(status.state) ? status.state : null;
}

export async function collectBrowserSignals(scope = globalThis) {
  const nav = scope.navigator;
  const doc = scope.document;
  const signals = { availability: {} };
  const store = (name, value) => {
    const available = value !== undefined && value !== null && (typeof value !== "number" || Number.isFinite(value));
    signals.availability[name] = available;
    signals[name] = available ? value : null;
  };
  const read = (name, getter) => {
    try {
      store(name, getter());
    } catch {
      store(name, null);
    }
  };
  const readAsync = async (name, getter) => {
    try {
      store(name, await getter());
    } catch {
      store(name, null);
    }
  };
  read("webdriver", () => typeof nav.webdriver === "boolean" ? nav.webdriver : null);
  read("hardware_concurrency", () => nav.hardwareConcurrency);
  read("language", () => nav.language?.slice(0, 64));
  read("timezone", () => Intl.DateTimeFormat().resolvedOptions().timeZone);
  read("viewport_width", () => scope.innerWidth);
  read("viewport_height", () => scope.innerHeight);
  read("outer_width", () => scope.outerWidth);
  read("outer_height", () => scope.outerHeight);
  read("screen_width", () => scope.screen.width);
  read("screen_height", () => scope.screen.height);
  read("color_depth", () => scope.screen.colorDepth);
  read("pixel_ratio", () => scope.devicePixelRatio);
  read("max_touch_points", () => nav.maxTouchPoints);
  read("device_memory", () => nav.deviceMemory);
  read("pointer_type", () => {
    if (scope.matchMedia("(pointer: fine)").matches) return "fine";
    if (scope.matchMedia("(pointer: coarse)").matches) return "coarse";
    return "none";
  });
  read("user_agent", () => nav.userAgent?.slice(0, 512));
  read("platform", () => nav.platform?.slice(0, 64));
  read("ua_brands", () => nav.userAgentData?.brands?.map((brand) => `${brand.brand} ${brand.version}`).join(", ").slice(0, 256));
  read("languages_count", () => nav.languages?.length);
  read("plugins_count", () => nav.plugins?.length);
  read("has_chrome_object", () => typeof scope.chrome === "object" && scope.chrome !== null);
  read("touch_events", () => "ontouchstart" in scope);
  read("notification_permission", () => {
    const value = scope.Notification?.permission;
    return ["default", "granted", "denied"].includes(value) ? value : null;
  });
  read("cdp_detected", () => cdpProbe(scope.console));
  const webgl = (() => { try { return webglProbe(doc); } catch { return null; } })();
  store("webgl_vendor", webgl?.vendor);
  store("webgl_renderer", webgl?.renderer);
  const fonts = (() => { try { return fontProbe(doc); } catch { return null; } })();
  store("font_count", fonts?.length);
  await Promise.all([
    readAsync("permission_query_state", () => notificationQueryState(nav)),
    readAsync("canvas_hash", async () => {
      const image = canvasProbe(doc);
      return image ? sha256(image) : null;
    }),
    readAsync("webgl_hash", () => webgl ? sha256(`${webgl.vendor}|${webgl.renderer}|${webgl.parameters}`) : null),
    readAsync("audio_hash", async () => {
      const value = await audioProbe(scope);
      return value === null ? null : sha256(value);
    }),
    readAsync("font_hash", () => fonts ? sha256(fonts.join(",")) : null),
  ]);
  return signals;
}
