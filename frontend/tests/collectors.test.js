import assert from "node:assert/strict";
import test from "node:test";
import { clickTarget, inputKind, isPrivateField, isTextField, keyClass, scrollInput } from "../js/collectors.js";

test("key classes describe the kind of key without recording its value", () => {
  const cases = { a: "char", Z: "char", 7: "char", " ": "space", Enter: "enter", Backspace: "backspace",
    Delete: "backspace", Shift: "modifier", Meta: "modifier", ArrowLeft: "navigation", PageDown: "navigation",
    Tab: "navigation", Unidentified: "char", F5: "other", Escape: "other" };
  for (const [key, expected] of Object.entries(cases)) assert.equal(keyClass(key), expected, key);
});

test("input kinds separate typing, pasting and text that appears without key presses", () => {
  const now = 10000;
  assert.equal(inputKind({ inputType: "insertText" }, now - 30, now), "typed");
  assert.equal(inputKind({ inputType: "insertText" }, now - 5000, now), "untyped");
  assert.equal(inputKind({ inputType: "insertFromPaste" }, now - 30, now), "paste");
  assert.equal(inputKind({ inputType: "insertFromDrop" }, -Infinity, now), "drop");
  assert.equal(inputKind({ inputType: "deleteContentBackward" }, now - 30, now), "delete");
  assert.equal(inputKind({ inputType: "insertReplacementText" }, -Infinity, now), "replacement");
  assert.equal(inputKind({}, -Infinity, now), "unknown");
});

test("scroll input prefers the most direct recent device", () => {
  const idle = { pointerDown: false, lastWheel: -Infinity, lastTouch: -Infinity, lastScrollKey: -Infinity };
  assert.equal(scrollInput(idle, 5000), "programmatic");
  assert.equal(scrollInput({ ...idle, lastWheel: 4800 }, 5000), "wheel");
  assert.equal(scrollInput({ ...idle, lastWheel: 4000 }, 5000), "programmatic");
  assert.equal(scrollInput({ ...idle, lastTouch: 4500 }, 5000), "touch");
  assert.equal(scrollInput({ ...idle, lastScrollKey: 4500 }, 5000), "key");
  assert.equal(scrollInput({ ...idle, pointerDown: true, lastWheel: 4900 }, 5000), "drag");
});

function element(matches, extra = {}) {
  const node = {
    ...extra,
    closest(selector) {
      if (selector === "[data-hp-target]") return matches.target ? node : null;
      if (selector === "[data-hp-private]") return matches.private ? node : null;
      return matches.control ? node : null;
    },
  };
  return node;
}

test("click targets distinguish measured targets from ordinary controls", () => {
  assert.equal(clickTarget(element({ target: true, control: true })).kind, "target");
  assert.equal(clickTarget(element({ control: true })).kind, "control");
  const plain = element({});
  assert.deepEqual(clickTarget(plain), { kind: "other", element: plain });
  assert.equal(clickTarget(null).kind, "other");
});

test("password and private fields are never collected", () => {
  assert.equal(isPrivateField({ type: "password" }), true);
  assert.equal(isPrivateField(element({ private: true }, { type: "text" })), true);
  assert.equal(isPrivateField(element({}, { type: "email" })), false);
  assert.equal(isTextField({ tagName: "INPUT", type: "email" }), true);
  assert.equal(isTextField({ tagName: "INPUT", type: "checkbox" }), false);
  assert.equal(isTextField({ tagName: "TEXTAREA" }), true);
  assert.equal(isTextField({ tagName: "SELECT" }), false);
});
