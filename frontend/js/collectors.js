import { createId } from "./transport.js";

export function shortcutCategory(event) {
  if (event.repeat || !(event.ctrlKey || event.metaKey || event.altKey)) return null;
  const key = event.key.toLowerCase();
  if (event.ctrlKey || event.metaKey) {
    const categories = { c: "copy", v: "paste", x: "cut", a: "select_all", z: "undo", y: "redo" };
    if (categories[key]) return key === "z" && event.shiftKey ? "redo" : categories[key];
  }
  if (["arrowleft", "arrowright", "tab", "home", "end"].includes(key)) return "navigation";
  return "other";
}

export function startCollectors(onEvent) {
  const startedAt = performance.now();
  const cleanup = [];
  const scrollPositions = new WeakMap();
  if (document.scrollingElement) scrollPositions.set(document.scrollingElement, document.scrollingElement.scrollTop || 0);
  const readingPanel = document.getElementById("reading-panel");
  if (readingPanel) scrollPositions.set(readingPanel, readingPanel.scrollTop || 0);
  let sequence = 0;
  let lastMove = -Infinity;
  let lastScroll = -Infinity;

  const listen = (target, type, listener, options = { passive: true }) => {
    target.addEventListener(type, listener, options);
    cleanup.push(() => target.removeEventListener(type, listener, options));
  };
  const emit = (eventType, x = null, y = null, metadata = {}) => {
    onEvent({
      event_id: createId(),
      seq: sequence++,
      event_type: eventType,
      x,
      y,
      ts: Date.now(),
      elapsed_ms: Math.round((performance.now() - startedAt) * 100) / 100,
      metadata: { viewport_width: window.innerWidth, viewport_height: window.innerHeight, ...metadata },
    });
  };

  listen(document, "pointermove", (event) => {
    const now = performance.now();
    if (!event.isPrimary || now - lastMove < 50) return;
    lastMove = now;
    emit("move", event.clientX, event.clientY, { pointer_type: event.pointerType || "mouse" });
  });
  listen(document, "click", (event) => {
    const keyboard = event.detail === 0 && !event.pointerType;
    const bounds = event.target?.getBoundingClientRect?.();
    const metadata = { pointer_type: keyboard ? "keyboard" : event.pointerType || "mouse" };
    if (bounds) {
      metadata.target_width = Math.round(bounds.width * 100) / 100;
      metadata.target_height = Math.round(bounds.height * 100) / 100;
    }
    emit("click", keyboard ? null : event.clientX, keyboard ? null : event.clientY, metadata);
  }, { passive: true, capture: true });
  listen(document, "scroll", (event) => {
    const now = performance.now();
    if (now - lastScroll < 75) return;
    lastScroll = now;
    const target = event.target === document ? document.scrollingElement : event.target;
    if (!target) return;
    const current = target.scrollTop || 0;
    const previous = scrollPositions.get(target) ?? current;
    scrollPositions.set(target, current);
    emit("scroll", null, null, { scroll_y: current, delta_y: current - previous, scroll_source: target === document.scrollingElement ? "page" : "panel" });
  }, { passive: true, capture: true });
  listen(window, "focus", () => emit("focus", null, null, { focused: true }));
  listen(window, "blur", () => emit("focus", null, null, { focused: false }));
  listen(document, "keydown", (event) => {
    const category = shortcutCategory(event);
    if (category) emit("shortcut", null, null, { shortcut_category: category });
  });
  emit("focus", null, null, { focused: document.hasFocus() });
  return { startedAt, stop: () => cleanup.splice(0).forEach((remove) => remove()) };
}
