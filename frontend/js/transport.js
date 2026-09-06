export function createId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
    this.retryable = status === 0 || status === 408 || status === 429 || status >= 500;
  }
}

export async function request(path, { token, body, keepalive = false, fetchImpl = globalThis.fetch } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const headers = { "Content-Type": "application/json" };
    if (token) headers["X-Collection-Token"] = token;
    const response = await fetchImpl(path, {
      method: body === undefined ? "GET" : "POST",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      keepalive,
      credentials: "same-origin",
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new ApiError("The API returned an unreadable response.", response.ok ? 502 : response.status);
    }
    if (!response.ok) {
      const message = typeof data.detail === "string" ? data.detail : `Request failed (${response.status}).`;
      throw new ApiError(message, response.status);
    }
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(error.name === "AbortError" ? "The API request timed out." : "Cannot reach the API. Check the connection.");
  } finally {
    clearTimeout(timeout);
  }
}

export class TelemetryTransport {
  constructor(session, { onChange = () => {}, fetchImpl = globalThis.fetch, maxBuffer = 2000, batchSize = 200, retryBase = 1000 } = {}) {
    this.session = session;
    this.onChange = onChange;
    this.fetchImpl = fetchImpl;
    this.maxBuffer = maxBuffer;
    this.batchSize = batchSize;
    this.retryBase = retryBase;
    this.queue = [];
    this.batch = null;
    this.inflight = null;
    this.acknowledged = 0;
    this.dropped = 0;
    this.failures = 0;
    this.retryAt = 0;
    this.error = null;
    this.active = false;
    this.timer = null;
    this.retryTimer = null;
  }

  snapshot() {
    return { buffered: this.queue.length, acknowledged: this.acknowledged, dropped: this.dropped, sending: Boolean(this.inflight), error: this.error?.message || null };
  }

  notify() {
    this.onChange(this.snapshot());
  }

  start() {
    if (this.active) return;
    this.active = true;
    this.timer = setInterval(() => this.uploadInBackground(), 3000);
  }

  stop() {
    this.active = false;
    clearInterval(this.timer);
    clearTimeout(this.retryTimer);
    this.timer = null;
    this.retryTimer = null;
  }

  add(event) {
    if (this.queue.length >= this.maxBuffer) {
      this.dropped += 1;
      this.notify();
      return false;
    }
    this.queue.push(event);
    this.notify();
    if (this.active && this.queue.length >= this.batchSize) this.uploadInBackground();
    return true;
  }

  makeBatch(size = this.batchSize) {
    if (!this.batch && this.queue.length) {
      this.batch = {
        session_id: this.session.session_id,
        batch_id: createId(),
        schema_version: 1,
        events: this.queue.slice(0, size),
      };
    }
    return this.batch;
  }

  uploadInBackground() {
    if (!this.active || this.retryAt > Date.now() || (this.error && !this.error.retryable)) return;
    this.flush().catch(() => {});
  }

  flush() {
    if (this.inflight) return this.inflight;
    if (!this.queue.length) return Promise.resolve();
    const batch = this.makeBatch();
    this.inflight = this.sendBatch(batch).finally(() => {
      this.inflight = null;
      this.notify();
      if (this.active && !this.error && this.queue.length >= this.batchSize) this.uploadInBackground();
    });
    this.notify();
    return this.inflight;
  }

  async sendBatch(batch) {
    try {
      const data = await request("/api/v1/telemetry/", { token: this.session.collection_token, body: batch, fetchImpl: this.fetchImpl });
      if (data.batch_id !== batch.batch_id || !Number.isInteger(data.stored) || !Number.isInteger(data.duplicates) || data.stored < 0 || data.duplicates < 0 || data.stored + data.duplicates !== batch.events.length) {
        throw new ApiError("The API did not acknowledge the entire batch.");
      }
      this.queue.splice(0, batch.events.length);
      this.acknowledged += batch.events.length;
      this.batch = null;
      this.error = null;
      this.failures = 0;
      this.retryAt = 0;
      clearTimeout(this.retryTimer);
    } catch (error) {
      this.error = error;
      this.failures += 1;
      const delay = Math.min(this.retryBase * 2 ** Math.min(this.failures - 1, 4), 15000);
      this.retryAt = Date.now() + delay;
      if (this.active && error.retryable) {
        clearTimeout(this.retryTimer);
        this.retryTimer = setTimeout(() => this.uploadInBackground(), delay);
      }
      throw error;
    }
  }

  async flushAll() {
    this.stop();
    let attempts = 0;
    while (this.queue.length || this.inflight) {
      try {
        await this.flush();
        attempts = 0;
      } catch (error) {
        attempts += 1;
        if (!error.retryable || attempts >= 3) throw error;
        await new Promise((resolve) => setTimeout(resolve, Math.min(this.retryBase * 2 ** (attempts - 1), 4000)));
      }
    }
  }

  sendOnExit() {
    const batch = this.makeBatch(60);
    if (!batch) return;
    if (new TextEncoder().encode(JSON.stringify(batch)).byteLength > 48000) return;
    request("/api/v1/telemetry/", { token: this.session.collection_token, body: batch, keepalive: true, fetchImpl: this.fetchImpl }).catch(() => {});
  }
}
