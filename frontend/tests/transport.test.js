import assert from "node:assert/strict";
import test from "node:test";
import { createId, TelemetryTransport } from "../js/transport.js";
import { shortcutCategory } from "../js/collectors.js";

const session = { session_id: createId(), collection_token: "test-token" };
const event = (seq) => ({ event_id: createId(), seq, event_type: "move", x: seq, y: seq, ts: Date.now(), elapsed_ms: seq * 50, metadata: {} });
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
const acknowledge = (body, duplicate = false) => response({ batch_id: body.batch_id, stored: duplicate ? 0 : body.events.length, duplicates: duplicate ? body.events.length : 0 });

test("a lost response retries the same batch and acknowledges server deduplication", async () => {
  const calls = [];
  const transport = new TelemetryTransport(session, { retryBase: 1, fetchImpl: async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push(body);
    assert.equal(options.headers["X-Collection-Token"], "test-token");
    if (calls.length === 1) throw new Error("Connection lost after the server saved the batch");
    return acknowledge(body, true);
  } });
  transport.add(event(0));
  transport.add(event(1));
  await assert.rejects(transport.flush());
  assert.equal(transport.snapshot().buffered, 2);
  transport.add(event(2));
  await transport.flush();
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(transport.snapshot().buffered, 1);
  assert.equal(transport.snapshot().acknowledged, 2);
  await transport.flushAll();
  assert.equal(transport.snapshot().buffered, 0);
});

test("simultaneous flushes share one request and preserve newly recorded events", async () => {
  let release;
  let calls = 0;
  const transport = new TelemetryTransport(session, { fetchImpl: async (url, options) => {
    calls += 1;
    await new Promise((resolve) => { release = resolve; });
    return acknowledge(JSON.parse(options.body));
  } });
  transport.add(event(0));
  const first = transport.flush();
  const second = transport.flush();
  assert.equal(first, second);
  transport.add(event(1));
  assert.equal(calls, 1);
  release();
  await first;
  assert.equal(transport.snapshot().buffered, 1);
  assert.equal(transport.queue[0].seq, 1);
});

test("full buffers retain pending events and report dropped new samples", () => {
  const transport = new TelemetryTransport(session, { maxBuffer: 2 });
  const first = event(0);
  assert.equal(transport.add(first), true);
  assert.equal(transport.add(event(1)), true);
  assert.equal(transport.add(event(2)), false);
  assert.equal(transport.queue[0], first);
  assert.equal(transport.snapshot().buffered, 2);
  assert.equal(transport.snapshot().dropped, 1);
});

test("finish drains bounded batches in order", async () => {
  const batches = [];
  const transport = new TelemetryTransport(session, { batchSize: 2, fetchImpl: async (url, options) => {
    const body = JSON.parse(options.body);
    batches.push(body);
    return acknowledge(body);
  } });
  for (let index = 0; index < 5; index += 1) transport.add(event(index));
  await transport.flushAll();
  assert.deepEqual(batches.map((batch) => batch.events.length), [2, 2, 1]);
  assert.deepEqual(batches.flatMap((batch) => batch.events.map((item) => item.seq)), [0, 1, 2, 3, 4]);
  assert.equal(new Set(batches.map((batch) => batch.batch_id)).size, 3);
  assert.equal(transport.snapshot().acknowledged, 5);
});

test("partial acknowledgements retain the complete batch", async () => {
  const transport = new TelemetryTransport(session, { fetchImpl: async (url, options) => {
    const body = JSON.parse(options.body);
    return response({ batch_id: body.batch_id, stored: 0, duplicates: 0 });
  } });
  transport.add(event(0));
  await assert.rejects(transport.flush(), /entire batch/);
  assert.equal(transport.snapshot().buffered, 1);
});

test("permanent validation failures do not consume queued data or loop on finish", async () => {
  let calls = 0;
  const transport = new TelemetryTransport(session, { retryBase: 1, fetchImpl: async () => {
    calls += 1;
    return response({ detail: "Session token expired." }, 403);
  } });
  transport.add(event(0));
  await assert.rejects(transport.flushAll(), /expired/);
  assert.equal(calls, 1);
  assert.equal(transport.snapshot().buffered, 1);
});

test("page exit sends a small keepalive batch without removing unacknowledged events", async () => {
  let sent;
  const transport = new TelemetryTransport(session, { fetchImpl: async (url, options) => {
    sent = options;
    return acknowledge(JSON.parse(options.body));
  } });
  for (let index = 0; index < 100; index += 1) transport.add(event(index));
  transport.sendOnExit();
  assert.equal(sent.keepalive, true);
  assert.equal(JSON.parse(sent.body).events.length, 60);
  assert.equal(transport.snapshot().buffered, 100);
  await new Promise((resolve) => setTimeout(resolve, 0));
});

test("shortcut categories exclude ordinary typing and never return typed text", () => {
  assert.equal(shortcutCategory({ key: "q" }), null);
  assert.equal(shortcutCategory({ key: "V", ctrlKey: true }), "paste");
  assert.equal(shortcutCategory({ key: "z", metaKey: true, shiftKey: true }), "redo");
  assert.equal(shortcutCategory({ key: "ArrowLeft", altKey: true }), "navigation");
  assert.equal(shortcutCategory({ key: "s", ctrlKey: true }), "other");
  assert.equal(shortcutCategory({ key: "v", ctrlKey: true, repeat: true }), null);
});
