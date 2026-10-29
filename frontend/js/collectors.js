import { createId } from "./transport.js";

const MOVE_INTERVAL_MS = 16;
const SCROLL_KEYS = new Set(["arrowup", "arrowdown", "pageup", "pagedown", "home", "end", " ", "spacebar"]);
const TEXT_INPUT_TYPES = new Set(["text", "email", "search", "url", "tel", "number"]);
const MODIFIER_KEYS = new Set(["Shift", "Control", "Alt", "AltGraph", "Meta", "CapsLock", "Fn"]);

const round = (value) => Math.round(value * 100) / 100;

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

// Only the class of a key is recorded, never the key value itself.
export function keyClass(key) {
  if (key === " " || key === "Spacebar") return "space";
  if (key === "Enter") return "enter";
  if (key === "Backspace" || key === "Delete") return "backspace";
  if (MODIFIER_KEYS.has(key)) return "modifier";
  if (/^(Arrow|Page)/.test(key) || ["Tab", "Home", "End"].includes(key)) return "navigation";
  if (key?.length === 1 || key === "Unidentified" || key === "Process") return "char";
  return "other";
}

export function isPrivateField(element) {
  return element?.type === "password" || Boolean(element?.closest?.("[data-hp-private]"));
}

export function isTextField(element) {
  if (element?.tagName === "TEXTAREA") return true;
  return element?.tagName === "INPUT" && TEXT_INPUT_TYPES.has(element.type);
}

export function inputKind(event, lastKeyAt, now) {
  const type = event.inputType || "";
  if (type === "insertFromPaste") return "paste";
  if (type === "insertFromDrop") return "drop";
  if (type.startsWith("delete")) return "delete";
  if (type === "insertReplacementText") return "replacement";
  if (!type) return "unknown";
  return now - lastKeyAt <= 1000 ? "typed" : "untyped";
}

export function clickTarget(element) {
  const target = element?.closest?.("[data-hp-target]");
  if (target) return { kind: "target", element: target };
  const control = element?.closest?.("button, a, select, input, label, textarea, summary, [role=button]");
  return { kind: control ? "control" : "other", element: control || element };
}

export function scrollInput(state, now) {
  if (state.pointerDown) return "drag";
  if (now - state.lastWheel < 500) return "wheel";
  if (now - state.lastTouch < 800) return "touch";
  if (now - state.lastScrollKey < 800) return "key";
  return "programmatic";
}

export function startCollectors(onEvent) {
  const startedAt = performance.now();
  const cleanup = [];
  const scrollPositions = new WeakMap();
  if (document.scrollingElement) scrollPositions.set(document.scrollingElement, document.scrollingElement.scrollTop || 0);
  const readingPanel = document.getElementById("reading-panel");
  if (readingPanel) scrollPositions.set(readingPanel, readingPanel.scrollTop || 0);
  const input = { pointerDown: false, lastWheel: -Infinity, lastTouch: -Infinity, lastScrollKey: -Infinity };
  const keyDowns = new Map();
  let sequence = 0;
  let lastMove = -Infinity;
  let lastScroll = -Infinity;
  let lastKeyAt = -Infinity;
  let lastDown = null;

  const listen = (target, type, listener, options = { passive: true }) => {
    target.addEventListener(type, listener, options);
    cleanup.push(() => target.removeEventListener(type, listener, options));
  };
  const emit = (eventType, x = null, y = null, metadata = {}, source = null) => {
    if (source && source.isTrusted === false) metadata.is_trusted = false;
    onEvent({
      event_id: createId(),
      seq: sequence++,
      event_type: eventType,
      x,
      y,
      ts: Date.now(),
      elapsed_ms: round(performance.now() - startedAt),
      metadata: { viewport_width: window.innerWidth, viewport_height: window.innerHeight, ...metadata },
    });
  };

  listen(document, "pointermove", (event) => {
    const now = performance.now();
    if (!event.isPrimary || now - lastMove < MOVE_INTERVAL_MS) return;
    lastMove = now;
    emit("move", event.clientX, event.clientY, { pointer_type: event.pointerType || "mouse" }, event);
  });
  listen(document, "pointerdown", (event) => {
    input.pointerDown = true;
    lastDown = { at: performance.now(), x: event.clientX, y: event.clientY };
  }, { passive: true, capture: true });
  listen(document, "pointerup", () => { input.pointerDown = false; }, { passive: true, capture: true });
  listen(document, "pointercancel", () => { input.pointerDown = false; }, { passive: true, capture: true });
  listen(document, "click", (event) => {
    const now = performance.now();
    const keyboard = event.detail === 0 && !event.pointerType;
    const { kind, element } = clickTarget(event.target);
    const metadata = { pointer_type: keyboard ? "keyboard" : event.pointerType || "mouse", target_kind: kind };
    const bounds = element?.getBoundingClientRect?.();
    if (bounds) {
      metadata.target_width = round(bounds.width);
      metadata.target_height = round(bounds.height);
      metadata.target_center_x = round(bounds.x + bounds.width / 2);
      metadata.target_center_y = round(bounds.y + bounds.height / 2);
    }
    if (!keyboard && lastDown && now - lastDown.at < 10000) metadata.press_ms = round(now - lastDown.at);
    lastDown = null;
    emit("click", keyboard ? null : event.clientX, keyboard ? null : event.clientY, metadata, event);
  }, { passive: true, capture: true });
  listen(document, "wheel", () => { input.lastWheel = performance.now(); }, { passive: true, capture: true });
  listen(document, "touchmove", () => { input.lastTouch = performance.now(); }, { passive: true, capture: true });
  listen(document, "scroll", (event) => {
    const now = performance.now();
    if (now - lastScroll < 75) return;
    lastScroll = now;
    const target = event.target === document ? document.scrollingElement : event.target;
    if (!target) return;
    const current = target.scrollTop || 0;
    const previous = scrollPositions.get(target) ?? current;
    scrollPositions.set(target, current);
    emit("scroll", null, null, {
      scroll_y: current, delta_y: current - previous,
      scroll_source: target === document.scrollingElement ? "page" : "panel",
      scroll_input: scrollInput(input, now),
    }, event);
  }, { passive: true, capture: true });
  listen(window, "focus", () => emit("focus", null, null, { focused: true }));
  listen(window, "blur", () => emit("focus", null, null, { focused: false }));
  listen(document, "keydown", (event) => {
    const now = performance.now();
    lastKeyAt = now;
    if (SCROLL_KEYS.has(event.key.toLowerCase())) input.lastScrollKey = now;
    const category = shortcutCategory(event);
    if (category) emit("shortcut", null, null, { shortcut_category: category }, event);
    if (!event.repeat && !isPrivateField(event.target)) keyDowns.set(event.code || event.key, now);
  });
  listen(document, "keyup", (event) => {
    const id = event.code || event.key;
    const down = keyDowns.get(id);
    keyDowns.delete(id);
    if (down === undefined || isPrivateField(event.target)) return;
    emit("key", null, null, { key_class: keyClass(event.key), press_ms: round(performance.now() - down) }, event);
  });
  listen(document, "input", (event) => {
    if (!isTextField(event.target) || isPrivateField(event.target)) return;
    emit("input", null, null, { input_kind: inputKind(event, lastKeyAt, performance.now()) }, event);
  }, { passive: true, capture: true });
  emit("focus", null, null, { focused: document.hasFocus() });
  return { startedAt, stop: () => cleanup.splice(0).forEach((remove) => remove()) };
}
