"""Automation-framework and API-level bots for the HumanProof dataset.

Complements scripts/bot_fleet.mjs (raw DevTools Protocol profiles) with:
  selenium-chrome    Selenium WebDriver with ActionChains, send_keys, and wheel actions
  playwright-chrome  Playwright with page.click, mouse.wheel, select_option, and fill
  http-naive         Direct API calls from Python urllib with straight synthetic paths
  http-forged        Direct API calls with a spoofed Chrome user agent, a forged browser fingerprint,
                     and humanised synthetic events (tests what the network layer adds)

Every completed session is labelled "bot" with source "bot-fleet: <profile> v1" and group <profile>.
Install the optional framework packages with: pip install -r requirements-fleet.txt
"""

import argparse
import json
import math
from pathlib import Path
import random
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener
import uuid

if __package__:
    from .demo_bot import ApiError, NoRedirects, REPO_DIR, analyst_key, base_url
else:
    from demo_bot import ApiError, NoRedirects, REPO_DIR, analyst_key, base_url


PROFILES = ["selenium-chrome", "playwright-chrome", "http-naive", "http-forged"]
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/156.0.0.0 Safari/537.36")
LAB_TARGETS = [(22, 32, 47), (74, 18, 30), (46, 60, 56), (12, 52, 26), (70, 56, 40), (42, 20, 34), (86, 62, 24), (30, 68, 44)]
COMPLETE_JS = "document.getElementById('session-state').textContent === 'Complete'"
COLLECTING_JS = "document.getElementById('session-state').textContent === 'Collecting'"


class Api:
    """JSON client that accepts the local edge's self-signed certificate on loopback only."""

    def __init__(self, root_url, headers=None):
        self.root_url = root_url
        handlers = [NoRedirects()]
        if root_url.startswith("https://"):
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(HTTPSHandler(context=context))
        self.opener = build_opener(*handlers)
        self.headers = headers or {}

    def request(self, path, payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else None
        merged = {**self.headers, **(headers or {})}
        if body is not None:
            merged["Content-Type"] = "application/json"
        try:
            with self.opener.open(Request(self.root_url + path, data=body, headers=merged), timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise ApiError(f"API returned HTTP {error.code} for {path}.") from None
        except (URLError, TimeoutError, ConnectionError):
            raise ApiError("Cannot reach the API. Start it with scripts/start_demo.ps1.") from None


def label(api_url, key, session_id, profile, transport, detail):
    analyst = Api(api_url, {"X-Analyst-Key": key})
    analyst.request(f"/api/v1/sessions/{session_id}/labels/", {
        "label": "bot", "source": f"bot-fleet: {profile} v1; {detail}; {transport}", "group": profile})
    return analyst.request(f"/api/v1/sessions/{session_id}/")


# Browser frameworks -------------------------------------------------------------------------------

def run_selenium(page_url, edge, generator):
    from selenium import webdriver
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select, WebDriverWait

    options = webdriver.ChromeOptions()
    for argument in ("--headless=new", "--window-size=1366,1000", "--no-first-run", "--disable-extensions"):
        options.add_argument(argument)
    if edge:
        options.add_argument("--ignore-certificate-errors")
    driver = webdriver.Chrome(options=options)
    try:
        wait = WebDriverWait(driver, 30)
        driver.get(page_url)
        wait.until(lambda browser: browser.find_element(By.ID, "start-session").is_enabled())
        find = lambda element_id: driver.find_element(By.ID, element_id)
        ActionChains(driver).move_to_element(find("start-session")).pause(0.3).click().perform()
        wait.until(lambda browser: browser.execute_script(f"return {COLLECTING_JS}"))
        total = int(find("target-total").text)
        for _ in range(total):
            ActionChains(driver).move_to_element(find("target")).pause(generator.uniform(0.1, 0.4)).click().perform()
            time.sleep(generator.uniform(0.2, 0.5))
        panel = find("reading-panel")
        for _ in range(6):
            ActionChains(driver).scroll_from_origin(ScrollOrigin.from_element(panel), 0, 160).perform()
            time.sleep(0.25)
        Select(find("focus-area")).select_by_value("mouse")
        find("note").send_keys("selenium run")
        find("show-reasons").click()
        driver.find_element(By.CSS_SELECTOR, "#preference-form button").click()
        time.sleep(0.8)
        find("finish-session").click()
        wait.until(lambda browser: browser.execute_script(f"return {COMPLETE_JS}"))
        return find("session-id").text, driver.capabilities.get("browserVersion", "chrome")
    finally:
        driver.quit()


def run_playwright(page_url, edge, generator):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(viewport={"width": 1350, "height": 900}, ignore_https_errors=edge)
            page = context.new_page()
            page.goto(page_url)
            page.click("#start-session")
            page.wait_for_function(COLLECTING_JS)
            total = int(page.text_content("#target-total"))
            for _ in range(total):
                page.click("#target")
                page.wait_for_timeout(generator.uniform(200, 500))
            box = page.locator("#reading-panel").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=10)
            for _ in range(6):
                page.mouse.wheel(0, 160)
                page.wait_for_timeout(250)
            page.select_option("#focus-area", "mouse")
            page.fill("#note", "playwright run")
            page.check("#show-reasons")
            page.click("#preference-form button")
            page.wait_for_timeout(800)
            page.click("#finish-session")
            page.wait_for_function(COMPLETE_JS, timeout=30000)
            return page.text_content("#session-id"), browser.version
        finally:
            browser.close()


# API-level bots -----------------------------------------------------------------------------------

def _bezier_path(start, end, start_ms, generator, humanised):
    distance = math.hypot(end[0] - start[0], end[1] - start[1]) or 1
    duration = (180 + 140 * math.log2(distance / 30 + 1)) * generator.uniform(0.8, 1.2) if humanised else 600
    steps = max(4, int(duration / (16 if humanised else 50)))
    bend = generator.gauss(0, distance * 0.12) if humanised else 0
    normal = (-(end[1] - start[1]) / distance, (end[0] - start[0]) / distance)
    middle = ((start[0] + end[0]) / 2 + normal[0] * bend, (start[1] + end[1]) / 2 + normal[1] * bend)
    points = []
    for step in range(1, steps + 1):
        tau = step / steps
        progress = 10 * tau ** 3 - 15 * tau ** 4 + 6 * tau ** 5 if humanised else tau
        q = 1 - progress
        x = q * q * start[0] + 2 * q * progress * middle[0] + progress * progress * end[0]
        y = q * q * start[1] + 2 * q * progress * middle[1] + progress * progress * end[1]
        if humanised:
            x, y = x + generator.gauss(0, 0.5), y + generator.gauss(0, 0.5)
        points.append((round(x, 2), round(y, 2), start_ms + tau * duration))
    return points, duration


def synthetic_lab_events(generator, humanised):
    """Events shaped like the lab's target, scroll, and typing tasks, forged without a browser."""
    events = [{"event_type": "focus", "elapsed_ms": 0, "metadata": {"focused": True}}]
    arena = (60, 330, 640, 235)
    position, now = (300.0, 200.0), generator.uniform(400, 900) if humanised else 50
    for left, top, size in LAB_TARGETS:
        center = (arena[0] + arena[2] * left / 100 + size / 2, arena[1] + arena[3] * top / 100 + size / 2)
        aim = (center[0] + (generator.gauss(0, size / 6) if humanised else 0),
               center[1] + (generator.gauss(0, size / 6) if humanised else 0))
        points, duration = _bezier_path(position, aim, now, generator, humanised)
        events += [{"event_type": "move", "x": x, "y": y, "elapsed_ms": t, "metadata": {"pointer_type": "mouse"}}
                   for x, y, t in points]
        now += duration + (generator.uniform(40, 160) if humanised else 5)
        events.append({"event_type": "click", "x": aim[0], "y": aim[1], "elapsed_ms": now, "metadata": {
            "pointer_type": "mouse", "target_kind": "target", "target_width": size, "target_height": size,
            "target_center_x": round(center[0], 2), "target_center_y": round(center[1], 2),
            "press_ms": round(generator.uniform(60, 140), 2) if humanised else 1.0}})
        position = aim
        now += generator.uniform(200, 700) if humanised else 300
    for index in range(6):
        now += generator.uniform(120, 400) if humanised else 250
        events.append({"event_type": "scroll", "elapsed_ms": now, "metadata": {
            "scroll_source": "panel", "scroll_y": 140 * (index + 1), "delta_y": 140,
            "scroll_input": "wheel" if humanised else "programmatic"}})
    for character in "forged run":
        now += generator.uniform(80, 240) if humanised else 40
        events.append({"event_type": "key", "elapsed_ms": now, "metadata": {
            "key_class": "space" if character == " " else "char",
            "press_ms": round(generator.uniform(55, 130), 2) if humanised else 0.5}})
        events.append({"event_type": "input", "elapsed_ms": now + 1, "metadata": {"input_kind": "typed"}})
    events.sort(key=lambda event: event["elapsed_ms"])
    origin = int(time.time() * 1000) - int(now) - 500
    for index, event in enumerate(events):
        event.update({"event_id": str(uuid.uuid4()), "seq": index, "ts": origin + int(event["elapsed_ms"]),
                      "elapsed_ms": round(event["elapsed_ms"], 2)})
        event.setdefault("x", None)
        event.setdefault("y", None)
        event["metadata"].update({"viewport_width": 1350, "viewport_height": 900})
    return events


def forged_fingerprint():
    return {"webdriver": False, "hardware_concurrency": 8, "language": "en-US", "timezone": "Asia/Calcutta",
            "viewport_width": 1350, "viewport_height": 900, "outer_width": 1366, "outer_height": 1000,
            "screen_width": 1920, "screen_height": 1080, "pixel_ratio": 1, "max_touch_points": 0,
            "pointer_type": "fine", "user_agent": CHROME_UA, "platform": "Win32", "languages_count": 2,
            "plugins_count": 5, "has_chrome_object": True, "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "notification_permission": "default", "permission_query_state": "prompt", "cdp_detected": False,
            "canvas_hash": "1" * 64, "webgl_hash": "2" * 64, "audio_hash": "3" * 64, "font_hash": "4" * 64, "font_count": 26}


def run_http(collection_url, generator, forged):
    headers = {"User-Agent": CHROME_UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"} if forged else {}
    api = Api(collection_url, headers)
    created = api.request("/api/v1/sessions/", {"task": "mixed-demo", "schema_version": 1})
    session_id, token = created["session_id"], {"X-Collection-Token": created["collection_token"]}
    fingerprint = forged_fingerprint() if forged else {"webdriver": None, "language": "en-US"}
    api.request("/api/v1/fingerprints/", {"session_id": session_id, "schema_version": 1, "signals": fingerprint}, token)
    events = synthetic_lab_events(generator, humanised=forged)
    for offset in range(0, len(events), 200):
        api.request("/api/v1/telemetry/", {"session_id": session_id, "batch_id": str(uuid.uuid4()), "schema_version": 1,
                                           "events": events[offset:offset + 200]}, token)
    api.request(f"/api/v1/sessions/{session_id}/complete/", {}, token)
    return session_id, "python-urllib" + (" with spoofed Chrome headers" if forged else "")


def run_profile(profile, api_url, edge_url, edge, key, seed):
    generator = random.Random(seed)
    transport = "tls-edge" if edge else "http"
    target = edge_url if edge else api_url
    if profile == "selenium-chrome":
        session_id, detail = run_selenium(target + "/", edge, generator)
    elif profile == "playwright-chrome":
        session_id, detail = run_playwright(target + "/", edge, generator)
    else:
        session_id, detail = run_http(target, generator, forged=profile == "http-forged")
    details = label(api_url, key, session_id, profile, transport, detail)
    assessment = details.get("assessment") or {}
    return {"profile": profile, "transport": transport, "session_id": session_id, "status": assessment.get("status"),
            "risk_score": assessment.get("risk_score"), "model_version": assessment.get("model_version"),
            "reasons": [reason.get("code") for reason in assessment.get("reasons", [])], "events": details.get("event_count")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profiles", default=",".join(PROFILES), help="Comma-separated profiles.")
    parser.add_argument("--runs", type=int, default=1, help="Runs per profile, 1 to 100.")
    parser.add_argument("--transport", choices=["http", "edge", "mixed"], default="http")
    parser.add_argument("--api-url", type=base_url, default="http://127.0.0.1:8000")
    parser.add_argument("--edge-url", type=base_url, default="https://127.0.0.1:8443")
    parser.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    args = parser.parse_args(argv)
    profiles = [item.strip() for item in args.profiles.split(",") if item.strip()]
    unknown = sorted(set(profiles) - set(PROFILES))
    if unknown:
        parser.error(f"Unknown profiles: {', '.join(unknown)}")
    if not 1 <= args.runs <= 100:
        parser.error("--runs must be between 1 and 100.")
    key = analyst_key()
    if not key:
        print("Analyst key missing. Run: python backend/manage.py init_demo", file=sys.stderr)
        return 1
    results, index = [], 0
    for run in range(args.runs):
        for position, profile in enumerate(profiles):
            # Alternate each profile between transports across runs, so transport never stands in for the family.
            edge = args.transport == "edge" or (args.transport == "mixed" and (run + position) % 2 == 1)
            try:
                result = run_profile(profile, args.api_url, args.edge_url, edge, key, args.seed + index * 7919)
            except ImportError:
                result = {"profile": profile, "error": "Install the optional packages: pip install -r requirements-fleet.txt"}
            except Exception as error:  # one failed run must not stop the fleet
                result = {"profile": profile, "error": f"{type(error).__name__}: {error}"[:300]}
            index += 1
            results.append(result)
            print(json.dumps(result), flush=True)
    output = REPO_DIR / "test-results" / "bot-fleet-python.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    failures = sum("error" in item for item in results)
    print(f"Completed {len(results) - failures} of {len(results)} runs. Report: {output.relative_to(REPO_DIR)}")
    return 1 if failures == len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
