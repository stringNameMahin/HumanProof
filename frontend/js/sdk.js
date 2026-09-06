import { collectBrowserSignals } from "./browser.js";
import { startCollectors } from "./collectors.js";
import { request, TelemetryTransport } from "./transport.js";

export class HumanProofSession {
  constructor({ onEvent = () => {}, onTransport = () => {}, onBrowser = () => {} } = {}) {
    this.onEvent = onEvent;
    this.onTransport = onTransport;
    this.onBrowser = onBrowser;
    this.session = null;
    this.transport = null;
    this.collector = null;
    this.signals = null;
    this.fingerprintSent = false;
    this.fingerprintPromise = null;
    this.exitHandler = null;
    this.paused = false;
    this.startedAt = null;
    this.finished = false;
  }

  async start() {
    this.session = await request("/api/v1/sessions/", { body: { task: "mixed-demo", schema_version: 1 } });
    this.transport = new TelemetryTransport(this.session, { onChange: this.onTransport });
    this.signals = collectBrowserSignals();
    this.transport.start();
    this.collector = startCollectors((event) => {
      if (this.transport.add(event)) this.onEvent(event);
    });
    this.startedAt = this.collector.startedAt;
    this.exitHandler = () => this.transport.sendOnExit();
    window.addEventListener("pagehide", this.exitHandler);
    this.saveFingerprint().catch(() => this.onBrowser(false));
    return this.session;
  }

  saveFingerprint() {
    if (this.fingerprintSent) return Promise.resolve();
    if (this.fingerprintPromise) return this.fingerprintPromise;
    this.fingerprintPromise = request("/api/v1/fingerprints/", {
      token: this.session.collection_token,
      body: { session_id: this.session.session_id, schema_version: 1, signals: this.signals },
    }).then(() => {
      this.fingerprintSent = true;
      this.onBrowser(true);
    }).finally(() => { this.fingerprintPromise = null; });
    return this.fingerprintPromise;
  }

  async finish() {
    this.collector?.stop();
    this.paused = true;
    await this.transport.flushAll();
    let browserError = null;
    try {
      await this.saveFingerprint();
    } catch (error) {
      browserError = error;
      this.onBrowser(false);
    }
    const result = await request(`/api/v1/sessions/${this.session.session_id}/complete/`, { token: this.session.collection_token, body: {} });
    window.removeEventListener("pagehide", this.exitHandler);
    this.finished = true;
    return { ...result, browserError: browserError?.message || null };
  }

  dispose() {
    this.collector?.stop();
    this.transport?.stop();
    if (this.exitHandler) window.removeEventListener("pagehide", this.exitHandler);
  }
}
