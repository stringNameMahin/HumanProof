export function collectBrowserSignals() {
  const signals = { availability: {} };
  const read = (name, getter) => {
    try {
      const value = getter();
      const available = value !== undefined && value !== null && (typeof value !== "number" || Number.isFinite(value));
      signals.availability[name] = available;
      signals[name] = available ? value : null;
    } catch {
      signals.availability[name] = false;
      signals[name] = null;
    }
  };
  read("webdriver", () => typeof navigator.webdriver === "boolean" ? navigator.webdriver : null);
  read("hardware_concurrency", () => navigator.hardwareConcurrency);
  read("language", () => navigator.language?.slice(0, 64));
  read("timezone", () => Intl.DateTimeFormat().resolvedOptions().timeZone);
  read("viewport_width", () => window.innerWidth);
  read("viewport_height", () => window.innerHeight);
  read("screen_width", () => window.screen.width);
  read("screen_height", () => window.screen.height);
  read("pixel_ratio", () => window.devicePixelRatio);
  read("max_touch_points", () => navigator.maxTouchPoints);
  read("pointer_type", () => {
    if (window.matchMedia("(pointer: fine)").matches) return "fine";
    if (window.matchMedia("(pointer: coarse)").matches) return "coarse";
    return "none";
  });
  return signals;
}
