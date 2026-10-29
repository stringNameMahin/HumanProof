import json
import math
import unittest

from .features import extract_features
from .scoring import score_session


def move(index, x=None, y=0, elapsed=None, **metadata):
    return {
        "seq": index, "event_type": "move", "x": index * 20 if x is None else x,
        "y": y, "elapsed_ms": index * 200 if elapsed is None else elapsed,
        "metadata": metadata,
    }


class FeatureTests(unittest.TestCase):
    def test_constant_speed_trajectory(self):
        result = extract_features([move(index) for index in range(41)])
        mouse = result["mouse"]
        self.assertEqual(mouse["path_length_px"], 800)
        self.assertEqual(mouse["displacement_px"], 800)
        self.assertEqual(mouse["mean_speed_px_s"], 100)
        self.assertEqual(mouse["mean_acceleration_px_s2"], 0)
        self.assertEqual(mouse["straightness"], 1)
        self.assertEqual(mouse["speed_cv"], 0)
        self.assertEqual(result["timing"]["duration_ms"], 8000)

    def test_turning_path(self):
        events = [move(0, 0), move(1, 10), move(2, 10, 10)]
        mouse = extract_features(events)["mouse"]
        self.assertEqual(mouse["path_length_px"], 20)
        self.assertAlmostEqual(mouse["straightness"], math.sqrt(2) / 2, places=4)
        self.assertEqual(mouse["mean_turn_angle_deg"], 90)

    def test_duplicates_gaps_and_bad_times(self):
        events = [move(0), move(1), move(1), move(2, elapsed=200),
                  move(3, elapsed=5000), move(4, elapsed=5100),
                  move(5, elapsed=-2), move(6, elapsed=float("nan"))]
        features = extract_features(events)
        quality = features["data_quality"]
        self.assertEqual(quality["duplicate_events"], 1)
        self.assertEqual(quality["dropped_events"], 2)
        self.assertEqual(quality["duplicate_timestamp_count"], 1)
        self.assertEqual(quality["motion_gap_count"], 1)
        self.assertEqual(features["mouse"]["valid_interval_count"], 2)
        json.dumps(features, allow_nan=False)

    def test_epoch_fallback_and_out_of_order(self):
        events = [{"seq": index, "event_type": "click", "ts": 1700000000000 + index * 500}
                  for index in (2, 0, 1)]
        features = extract_features(events)
        self.assertEqual(features["timing"]["duration_ms"], 1000)
        self.assertEqual(features["timing"]["mean_click_interval_ms"], 500)
        self.assertEqual(features["data_quality"]["out_of_order_events"], 1)

    def test_scroll_speed_and_acceleration(self):
        events = [{"event_type": "scroll", "elapsed_ms": index * 100,
                   "metadata": {"scroll_y": position, "delta_y": 20}}
                  for index, position in enumerate((0, 10, 30))]
        timing = extract_features(events)["timing"]
        self.assertEqual(timing["scroll_distance_px"], 60)
        self.assertEqual(timing["mean_scroll_speed_px_s"], 150)
        self.assertEqual(timing["mean_scroll_acceleration_px_s2"], 1000)

    def test_scroll_rates_keep_page_and_panel_coordinates_separate(self):
        observations = [(0, "page", 1000), (50, "panel", 0), (100, "page", 1010),
                        (150, "panel", 20), (200, "page", 1020), (250, "panel", 40)]
        events = [{"event_type": "scroll", "elapsed_ms": elapsed,
                   "metadata": {"scroll_source": source, "scroll_y": position}}
                  for elapsed, source, position in observations]
        features = extract_features(events)
        self.assertEqual(features["timing"]["mean_scroll_speed_px_s"], 150)
        self.assertEqual(features["timing"]["mean_scroll_acceleration_px_s2"], 0)
        self.assertEqual(features["timing"]["scroll_sources"], ["page", "panel"])
        self.assertEqual(features["data_quality"]["unknown_scroll_source_count"], 0)

    def test_missing_scroll_source_has_an_explicit_quality_flag(self):
        events = [{"event_type": "scroll", "elapsed_ms": 100,
                   "metadata": {"scroll_y": 10}}]
        result = score_session(events)
        self.assertEqual(result["features"]["timing"]["scroll_sources"], ["unknown"])
        self.assertEqual(result["features"]["data_quality"]["unknown_scroll_source_count"], 1)
        self.assertTrue(any("lack a source" in message for message in result["limitations"]))

    def test_sequence_gaps_include_leading_loss_without_adding_risk(self):
        events = [{"seq": index, "event_type": "shortcut", "elapsed_ms": index * 300}
                  for index in range(3, 27) if index != 10]
        result = score_session(events)
        self.assertEqual(result["features"]["data_quality"]["sequence_gap_count"], 4)
        self.assertEqual(result["risk_score"], 0)
        self.assertTrue(any("incomplete collection" in message for message in result["limitations"]))

    def test_submillisecond_intervals_do_not_overflow_rates(self):
        events = [move(0), move(1, elapsed=5e-324), move(2, elapsed=0.5)]
        events += [{"event_type": "scroll", "elapsed_ms": elapsed,
                    "metadata": {"scroll_y": position, "delta_y": position}}
                   for elapsed, position in ((0, 0), (5e-324, 10000000), (0.5, -10000000))]
        result = score_session(events)
        self.assertIsNone(result["features"]["mouse"]["mean_speed_px_s"])
        self.assertIsNone(result["features"]["timing"]["mean_scroll_speed_px_s"])
        self.assertEqual(result["features"]["data_quality"]["short_motion_interval_count"], 2)
        self.assertEqual(result["features"]["data_quality"]["short_scroll_interval_count"], 2)
        json.dumps(result, allow_nan=False)

    def test_missing_mouse_coordinates_and_touch(self):
        events = [move(index, pointer_type="touch") for index in range(31)]
        events.append({"event_type": "move", "elapsed_ms": 6200})
        features = extract_features(events, {"max_touch_points": 5})
        self.assertFalse(features["mouse"]["available"])
        self.assertEqual(features["data_quality"]["missing_coordinates"], 1)
        self.assertEqual(features["data_quality"]["pointer_types"], ["touch"])

    def test_plot_size_is_bounded_without_truncating_features(self):
        features = extract_features([move(index) for index in range(1200)])
        self.assertEqual(features["mouse"]["move_count"], 1200)
        self.assertEqual(len(features["mouse"]["path"]), 500)
        self.assertEqual(len(features["mouse"]["speed_samples"]), 500)
        self.assertEqual(features["mouse"]["path"][-1]["x"], 23980)


class ScoringTests(unittest.TestCase):
    def test_empty_and_short_sessions_abstain(self):
        for events in ([], [move(index) for index in range(10)],
                       [move(index, elapsed=index) for index in range(100)]):
            with self.subTest(count=len(events)):
                result = score_session(events, {"webdriver": True})
                self.assertEqual(result["status"], "insufficient_data")
                self.assertIsNone(result["risk_score"])
                self.assertEqual(result["decision"], "observe")

    def test_automation_signals_are_explained_and_deterministic(self):
        events = [move(index) for index in range(41)]
        events += [{"seq": index + 100, "event_type": "click", "elapsed_ms": index * 1000}
                   for index in range(8)]
        first = score_session(events, {"webdriver": True})
        self.assertEqual(first, score_session(events, {"webdriver": True}))
        self.assertEqual(first["risk_score"], 85)
        self.assertEqual({reason["code"] for reason in first["reasons"]},
                         {"webdriver_reported", "uniform_straight_motion", "regular_click_timing"})
        self.assertEqual(first["available_layers"], ["browser", "mouse", "timing"])
        json.dumps(first, allow_nan=False)

    def test_regular_webdriver_trace_reaches_demo_threshold(self):
        result = score_session([move(index) for index in range(41)], {"webdriver": True})
        self.assertGreaterEqual(result["risk_score"], 60)
        self.assertTrue(any("threshold of 60" in value for value in result["limitations"]))

    def test_focus_events_cannot_supply_missing_interactions(self):
        events = [{"seq": index, "event_type": "focus", "elapsed_ms": index * 300}
                  for index in range(21)]
        self.assertEqual(score_session(events)["status"], "insufficient_data")
        short_activity = [{"event_type": "focus", "elapsed_ms": 0}]
        short_activity += [move(index, elapsed=6000 + index) for index in range(30)]
        self.assertEqual(score_session(short_activity)["status"], "insufficient_data")

    def test_keyboard_only_is_not_penalized(self):
        events = [{"seq": index, "event_type": "shortcut", "elapsed_ms": index * 300,
                   "metadata": {"category": "navigation"}} for index in range(21)]
        result = score_session(events)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["risk_score"], 0)
        self.assertEqual(result["available_layers"], ["timing"])
        self.assertEqual(result["features"]["timing"]["shortcut_count"], 21)

    def test_webdriver_alone_does_not_establish_a_bot(self):
        events = [move(index, pointer_type="touch") for index in range(31)]
        result = score_session(events, {"webdriver": True})
        self.assertEqual(result["risk_score"], 35)
        self.assertEqual(result["decision"], "observe")

    def test_unknown_browser_values_are_missing(self):
        result = score_session([move(index) for index in range(31)],
                               {"webdriver": "true", "hardware_concurrency": float("inf")})
        self.assertNotIn("browser", result["available_layers"])
        self.assertNotIn("webdriver_reported", [reason["code"] for reason in result["reasons"]])
        json.dumps(result, allow_nan=False)


def stroke(start, end, start_ms, duration_ms, tremor_px=0.0, rate_hz=60):
    """Minimum-jerk movement between two points with optional sideways tremor at 9 Hz."""
    events = []
    steps = max(2, int(duration_ms * rate_hz / 1000))
    length = math.hypot(end[0] - start[0], end[1] - start[1]) or 1
    normal = (-(end[1] - start[1]) / length, (end[0] - start[0]) / length)
    for step in range(steps + 1):
        tau = step / steps
        progress = 10 * tau ** 3 - 15 * tau ** 4 + 6 * tau ** 5
        wobble = tremor_px * math.sin(2 * math.pi * 9 * tau * duration_ms / 1000)
        events.append({"event_type": "move", "elapsed_ms": start_ms + tau * duration_ms,
                       "x": start[0] + (end[0] - start[0]) * progress + normal[0] * wobble,
                       "y": start[1] + (end[1] - start[1]) * progress + normal[1] * wobble,
                       "metadata": {"pointer_type": "mouse"}})
    return events


def target_click(elapsed, x, y, center, width, press_ms=90.0, **metadata):
    return {"event_type": "click", "elapsed_ms": elapsed, "x": x, "y": y,
            "metadata": {"pointer_type": "mouse", "target_kind": "target", "target_center_x": center[0],
                         "target_center_y": center[1], "target_width": width, "target_height": width,
                         "press_ms": press_ms, **metadata}}


def acquisition_session(movement_time, offset=(3, -2), press_ms=90.0):
    """Clicks on targets of varied size and distance with a chosen movement-time model."""
    targets = [((420, 140), 30), ((180, 300), 56), ((640, 330), 24), ((300, 120), 44),
               ((560, 220), 34), ((140, 180), 26), ((480, 360), 48)]
    events = [{"event_type": "click", "elapsed_ms": 0, "x": 100, "y": 100,
               "metadata": {"pointer_type": "mouse", "target_kind": "control"}}]
    previous, now = (100, 100), 0
    for center, width in targets:
        distance = math.hypot(center[0] - previous[0], center[1] - previous[1])
        duration = movement_time(math.log2(distance / width + 1))
        onset = now + 400
        click_at = (center[0] + offset[0], center[1] + offset[1])
        events += stroke(previous, click_at, onset, duration, tremor_px=0.6)
        now = onset + duration + 5
        events.append(target_click(now, click_at[0], click_at[1], center, width, press_ms))
        previous = click_at
    return events


def browser_profile(**overrides):
    profile = {
        "webdriver": False, "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36", "ua_brands": "Chromium 156, Google Chrome 156",
        "webgl_renderer": "ANGLE (NVIDIA GeForce RTX 3060 Direct3D11)", "notification_permission": "default",
        "permission_query_state": "prompt", "viewport_width": 1280, "viewport_height": 720, "outer_width": 1296,
        "outer_height": 800, "screen_width": 1920, "screen_height": 1080, "languages_count": 2,
        "plugins_count": 5, "has_chrome_object": True, "language": "en-US",
    }
    profile.update(overrides)
    return profile


def observation(**overrides):
    data = {
        "user_agent": browser_profile()["user_agent"], "accept_language": "en-US,en;q=0.9",
        "sec_fetch_mode": "cors", "sec_ch_ua": '"Chromium";v="156", "Google Chrome";v="156"',
        "header_names": ["host", "user-agent", "accept"], "header_order_hash": "abc", "tls": None,
    }
    data.update(overrides)
    return data


class MouseLayerTests(unittest.TestCase):
    def test_device_jitter_and_tremor_separate_from_linear_motion(self):
        linear = extract_features([move(index, elapsed=index * 16) for index in range(90)])["mouse"]
        self.assertEqual(linear["micro_jitter_px"], 0)
        self.assertIsNone(linear["tremor_band_ratio"])
        human = extract_features(stroke((100, 400), (900, 120), 0, 1500, tremor_px=1.2))["mouse"]
        self.assertGreater(human["micro_jitter_px"], 0.05)
        self.assertGreater(human["tremor_band_ratio"], 0.01)
        self.assertGreater(human["mean_curvature_rad_px"], 0)
        self.assertIsNotNone(human["mean_jerk_px_s3"])

    def test_fitts_law_fit_recovers_slope_and_intercept(self):
        mouse = extract_features(acquisition_session(lambda bits: 150 + 120 * bits))["mouse"]
        self.assertEqual(mouse["fitts_count"], 7)
        self.assertAlmostEqual(mouse["fitts_slope_ms_per_bit"], 120, delta=15)
        self.assertGreater(mouse["fitts_r2"], 0.9)
        self.assertEqual(mouse["target_click_count"], 7)
        self.assertGreater(mouse["mean_click_offset_ratio"], 0.1)
        self.assertEqual(len(mouse["fitts_points"]), 7)

    def test_constant_movement_time_and_centre_clicks_are_flagged(self):
        events = acquisition_session(lambda bits: 900, offset=(0, 0))
        result = score_session(events, browser_profile())
        codes = {reason["code"] for reason in result["reasons"]}
        self.assertLess(result["features"]["mouse"]["movement_time_cv"], 0.08)
        self.assertIn("constant_movement_time", codes)
        self.assertIn("center_precise_clicks", codes)
        human = score_session(acquisition_session(lambda bits: 150 + 120 * bits), browser_profile())
        self.assertEqual(human["risk_score"], 0)
        self.assertEqual(human["layer_scores"]["mouse"], 0)

    def test_clicks_without_an_approach_are_counted(self):
        events = [move(index, x=10, y=10, elapsed=index * 16) for index in range(10)]
        events += [{"event_type": "click", "elapsed_ms": 1000 + index * 400, "x": 400 + index * 60, "y": 300,
                    "metadata": {"pointer_type": "mouse"}} for index in range(4)]
        self.assertEqual(extract_features(events)["mouse"]["approachless_click_count"], 4)
        events.append({"event_type": "move", "elapsed_ms": 2700, "x": 402, "y": 302, "metadata": {}})
        events.append({"event_type": "click", "elapsed_ms": 2750, "x": 400, "y": 300, "metadata": {}})
        self.assertEqual(extract_features(events)["mouse"]["approachless_click_count"], 4)


class TimingLayerTests(unittest.TestCase):
    def base(self):
        return [move(index, elapsed=index * 300) for index in range(25)]

    def codes(self, events, fingerprint=None):
        return {reason["code"] for reason in score_session(events, fingerprint or browser_profile())["reasons"]}

    def test_press_dwell_statistics_and_instant_release(self):
        clicks = [{"event_type": "click", "elapsed_ms": 500 + index * 911, "x": 20, "y": 0,
                   "metadata": {"press_ms": press}} for index, press in enumerate((1.2, 0.8, 1.5, 2.0))]
        result = score_session(self.base() + clicks, browser_profile())
        self.assertEqual(result["features"]["timing"]["median_press_ms"], 1.35)
        self.assertEqual(result["features"]["timing"]["instant_press_count"], 4)
        self.assertIn("instant_press_release", {reason["code"] for reason in result["reasons"]})
        for click in clicks:
            click["metadata"]["press_ms"] += 90
        self.assertNotIn("instant_press_release", self.codes(self.base() + clicks))

    def test_untrusted_events_are_decisive_evidence(self):
        events = self.base()
        events[3]["metadata"] = {"is_trusted": False}
        result = score_session(events, browser_profile())
        self.assertEqual(result["features"]["timing"]["untrusted_event_count"], 1)
        self.assertEqual(result["risk_score"], 40)

    def test_scroll_input_sources(self):
        def scrolls(kind, count):
            return [{"event_type": "scroll", "elapsed_ms": 1000 + index * 120,
                     "metadata": {"scroll_source": "panel", "scroll_y": index * 40, "scroll_input": kind}}
                    for index in range(count)]
        self.assertIn("programmatic_scroll", self.codes(self.base() + scrolls("programmatic", 4)))
        self.assertNotIn("programmatic_scroll", self.codes(self.base() + scrolls("wheel", 6) + scrolls("programmatic", 2)))
        timing = extract_features(scrolls("wheel", 3) + scrolls("key", 2))["timing"]
        self.assertEqual(timing["scroll_inputs"], {"key": 2, "wheel": 3})

    def test_key_cadence_never_needs_key_values(self):
        def keys(press, gap, count=12):
            return [{"event_type": "key", "elapsed_ms": 1000 + index * gap,
                     "metadata": {"key_class": "char", "press_ms": press}} for index in range(count)]
        self.assertIn("mechanical_typing", self.codes(self.base() + keys(1, 100)))
        self.assertIn("mechanical_typing", self.codes(self.base() + keys(80, 100)))
        varied = keys(80, 100)
        for index, event in enumerate(varied):
            event["elapsed_ms"] += (index % 4) * 37
            event["metadata"]["press_ms"] += index % 3 * 15
        timing = extract_features(varied)["timing"]
        self.assertGreater(timing["key_interval_cv"], 0.1)
        self.assertNotIn("mechanical_typing", self.codes(self.base() + varied))

    def test_untyped_input_is_flagged_but_paste_is_not(self):
        def inputs(kind):
            return [{"event_type": "input", "elapsed_ms": 2000, "metadata": {"input_kind": kind}}]
        self.assertIn("untyped_text_input", self.codes(self.base() + inputs("untyped")))
        self.assertNotIn("untyped_text_input", self.codes(self.base() + inputs("paste")))
        timing = extract_features(inputs("unknown"))["timing"]
        self.assertEqual((timing["untyped_input_count"], timing["unknown_input_count"]), (0, 1))


class BrowserLayerTests(unittest.TestCase):
    def test_consistent_browser_has_no_anomalies(self):
        browser = extract_features([], browser_profile())["browser"]
        self.assertEqual(browser["anomaly_count"], 0)
        self.assertFalse(browser["headless_user_agent"])
        self.assertFalse(browser["software_renderer"])

    def test_headless_and_inconsistent_environments(self):
        profile = browser_profile(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 HeadlessChrome/156.0.0.0 Safari/537.36",
            webgl_renderer="Google SwiftShader", notification_permission="denied",
            outer_width=800, outer_height=600, screen_width=800, screen_height=600,
            viewport_width=1365, viewport_height=1000, has_chrome_object=False, plugins_count=0)
        browser = extract_features([], profile)["browser"]
        for name in ("headless_user_agent", "software_renderer", "permission_inconsistent",
                     "window_geometry_inconsistent", "chromium_without_chrome_object", "chromium_without_plugins"):
            self.assertTrue(browser[name], name)
        result = score_session([move(index, elapsed=index * 300) for index in range(25)], profile)
        self.assertEqual(result["layer_scores"]["browser"], 70)
        self.assertEqual(result["risk_score"], 70)

    def test_missing_evidence_is_not_an_anomaly(self):
        browser = extract_features([], {"webdriver": False})["browser"]
        for name in ("headless_user_agent", "software_renderer", "permission_inconsistent", "zero_outer_window",
                     "window_geometry_inconsistent", "chromium_without_chrome_object"):
            self.assertIsNone(browser[name], name)
        self.assertEqual(browser["anomaly_count"], 0)


class NetworkLayerTests(unittest.TestCase):
    def network(self, observations, fingerprint=None):
        return extract_features([], fingerprint or browser_profile(), observations)["network"]

    def test_missing_observations_mark_the_layer_unavailable(self):
        result = score_session([move(index, elapsed=index * 300) for index in range(25)])
        self.assertFalse(result["features"]["network"]["available"])
        self.assertNotIn("network", result["available_layers"])
        self.assertTrue(any("Network evidence" in item for item in result["limitations"]))

    def test_browser_requests_are_consistent(self):
        network = self.network([observation(), observation()])
        self.assertTrue(network["available"])
        self.assertEqual(network["user_agent_family"], "chromium")
        for name in ("client_library_user_agent", "missing_browser_headers", "user_agent_mismatch", "language_mismatch"):
            self.assertFalse(network[name], name)

    def test_http_library_and_spoofed_headers(self):
        library = self.network([observation(user_agent="Python-urllib/3.14", sec_fetch_mode=None, sec_ch_ua=None)])
        self.assertTrue(library["client_library_user_agent"])
        spoofed = self.network([observation(sec_fetch_mode=None, sec_ch_ua=None, accept_language=None)])
        self.assertTrue(spoofed["missing_browser_headers"])
        self.assertTrue(spoofed["missing_client_hints"])
        mismatch = self.network([observation(accept_language="de-DE")],
                                browser_profile(user_agent="Mozilla/5.0 Chrome/150.0.0.0"))
        self.assertTrue(mismatch["user_agent_mismatch"])
        self.assertTrue(mismatch["language_mismatch"])

    def test_tls_fingerprint_without_grease_contradicts_a_chromium_agent(self):
        tls = {"ja4": "t13i1311h1_abc_def", "ja3_hash": "0" * 32, "tls_version": "1.3", "alpn": "http/1.1",
               "grease": False, "cipher_count": 13, "extension_count": 11, "handshake_ms": 4.0, "hello_delay_ms": 1.0}
        network = self.network([observation(tls=tls)])
        self.assertEqual(network["transport"], "edge-tls")
        self.assertTrue(network["tls_user_agent_mismatch"])
        events = [move(index, elapsed=index * 300) for index in range(25)]
        result = score_session(events, browser_profile(), [observation(tls=tls)])
        self.assertEqual(result["layer_scores"]["network"], 35)
        tls["grease"] = True
        self.assertFalse(self.network([observation(tls=tls)])["tls_user_agent_mismatch"])


class RuleCatalogueTests(unittest.TestCase):
    def test_rules_have_unique_codes_and_known_layers(self):
        from .scoring import LAYERS, rule_catalogue

        catalogue = rule_catalogue()
        self.assertEqual(len({rule["code"] for rule in catalogue}), len(catalogue))
        self.assertTrue(all(rule["layer"] in LAYERS for rule in catalogue))

    def test_layer_scores_are_capped(self):
        profile = browser_profile(webdriver=True, user_agent="HeadlessChrome/1", webgl_renderer="SwiftShader",
                                  notification_permission="denied", outer_width=0, languages_count=0)
        result = score_session([move(index, elapsed=index * 300) for index in range(25)], profile)
        self.assertEqual(result["layer_scores"]["browser"], 100)
        self.assertEqual(result["risk_score"], 100)
        json.dumps(result, allow_nan=False)


def fixture_dataset(humans=12, bots=12, seed=3):
    """Test-only sessions. These are generated fixtures and are never stored or labelled outside tests."""
    import random

    generator = random.Random(seed)
    dataset = []
    for index in range(humans):
        slope = generator.uniform(90, 160)
        events = acquisition_session(lambda bits, s=slope: 140 + s * bits + generator.uniform(-40, 40),
                                     offset=(generator.uniform(-8, 8), generator.uniform(-8, 8)),
                                     press_ms=generator.uniform(70, 140))
        features = extract_features(events, browser_profile(hardware_concurrency=generator.choice([4, 8, 12])),
                                    [observation()])
        dataset.append({"session_id": f"h{index}", "label": 0, "group": f"participant-{index % 4}",
                        "features": features, "rule_score": score_session(events, browser_profile())["rule_score"]})
    families = ["family-a", "family-b", "family-c"]
    for index in range(bots):
        family = families[index % 3]
        events = acquisition_session(lambda bits: 900 + generator.uniform(-5, 5), offset=(0, 0),
                                     press_ms=generator.uniform(0.5, 3))
        profile = browser_profile(webdriver=family != "family-c",
                                  user_agent="Mozilla/5.0 HeadlessChrome/156.0.0.0" if family == "family-a" else None)
        features = extract_features(events, profile, [observation()])
        dataset.append({"session_id": f"b{index}", "label": 1, "group": family, "features": features,
                        "rule_score": score_session(events, profile)["rule_score"]})
    return dataset


class BotBiometricNetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from .training import fit, train_and_evaluate

        cls.dataset = fixture_dataset()
        cls.spec = {**fit(cls.dataset), "model_version": "botbiometricnet-test"}
        cls.trained, cls.predictions = train_and_evaluate(cls.dataset)

    def test_fused_scores_separate_the_fixture_classes(self):
        from .model import BotBiometricNet

        model = BotBiometricNet(self.spec)
        human = [model.predict(item["features"])["probability"] for item in self.dataset if item["label"] == 0]
        bot = [model.predict(item["features"])["probability"] for item in self.dataset if item["label"] == 1]
        self.assertGreater(min(bot), max(human))
        self.assertTrue(all(self.spec["layers"][layer]["trained"] for layer in ("browser", "mouse", "timing")))

    def test_json_round_trip_and_exact_explanations(self):
        import tempfile
        from pathlib import Path

        from .model import BotBiometricNet, sigmoid

        model = BotBiometricNet(self.spec)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            model.save(path)
            loaded = BotBiometricNet.load(path)
        features = self.dataset[-1]["features"]
        prediction = loaded.predict(features)
        self.assertEqual(prediction, model.predict(features))
        fusion = self.spec["fusion"]
        logit = fusion["intercept"] + sum(
            info["contribution"] + fusion["availability"][layer]
            for layer, info in prediction["layers"].items() if info["available"])
        self.assertAlmostEqual(sigmoid(logit), prediction["probability"], places=3)
        json.dumps(prediction, allow_nan=False)

    def test_missing_layers_contribute_nothing(self):
        from .model import BotBiometricNet

        features = extract_features([], None, None)
        prediction = BotBiometricNet(self.spec).predict(features)
        self.assertTrue(all(not info["available"] for info in prediction["layers"].values()))
        self.assertEqual(prediction["top_contributions"], [])

    def test_artifact_validation(self):
        from .model import BotBiometricNet

        with self.assertRaises(ValueError):
            BotBiometricNet({**self.spec, "schema": "other"})
        broken = json.loads(json.dumps(self.spec))
        broken["layers"]["mouse"]["features"] = broken["layers"]["mouse"]["features"][:-1]
        with self.assertRaises(ValueError):
            BotBiometricNet(broken)

    def test_training_report_covers_known_and_unseen_frameworks(self):
        from .training import report_markdown

        evaluation = self.trained["evaluation"]
        known = evaluation["known_frameworks"]
        self.assertGreaterEqual(known["folds"], 2)
        self.assertEqual(known["at_operating_threshold"]["auc"], 1.0)
        self.assertEqual(set(evaluation["unseen_frameworks"]["families"]), {"family-a", "family-b", "family-c"})
        self.assertIn("rules_baseline_at_60", known)
        self.assertEqual(len(self.predictions), len(self.dataset))
        self.assertTrue(self.trained["model_version"].startswith("botbiometricnet-v1-"))
        report = report_markdown(self.trained)
        self.assertEqual(set(known["family_detection"]), {"family-a", "family-b", "family-c"})
        for heading in ("## Known frameworks", "## Unseen frameworks and retraining recovery", "## Limitations",
                        "rules-v2 baseline", "Detected after retraining"):
            self.assertIn(heading, report)
        json.dumps(self.trained, allow_nan=False)

    def test_single_participant_falls_back_to_session_holdout(self):
        from .training import report_markdown, train_and_evaluate

        single = [{**item, "group": "participant-0"} if item["label"] == 0 else item for item in self.dataset]
        spec, _ = train_and_evaluate(single)
        self.assertEqual(spec["evaluation"]["known_frameworks"]["human_split"], "session")
        self.assertEqual(self.trained["evaluation"]["known_frameworks"]["human_split"], "participant")
        self.assertIsNotNone(spec["evaluation"]["unseen_frameworks"])
        report = report_markdown(spec)
        self.assertIn("Only one participant is labelled", report)
        self.assertNotIn("Held-out human sessions flagged", report)

    def test_inference_uses_a_matching_model_and_falls_back_to_rules(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from . import inference
        from .model import BotBiometricNet

        events = acquisition_session(lambda bits: 900, offset=(0, 0), press_ms=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            with patch.dict(os.environ, {"HUMANPROOF_MODEL_PATH": str(path)}):
                self.assertEqual(inference.current_model_version(), "rules-v2")
                BotBiometricNet(self.spec).save(path)
                result = inference.assess(events, browser_profile(webdriver=True), [observation()])
                self.assertEqual(result["model_version"], "botbiometricnet-test")
                self.assertEqual(result["risk_score"], result["model_explanation"]["score"])
                self.assertIsNotNone(result["rule_score"])
                stale = {**self.spec, "feature_version": "features-v1"}
                path.write_text(json.dumps(stale))
                os.utime(path, ns=(1, 1))
                self.assertEqual(inference.current_model_version(), "rules-v2")

    def test_protected_action_policy(self):
        from .inference import decide

        ready = lambda score: {"status": "ready", "risk_score": score}
        self.assertEqual(decide(ready(90), "mixed-demo"), "observe")
        self.assertEqual(decide(ready(10), "sign-in"), "allow")
        self.assertEqual(decide(ready(50), "sign-in"), "challenge")
        self.assertEqual(decide(ready(80), "sign-in"), "block")
        self.assertEqual(decide({"status": "insufficient_data", "risk_score": None}, "sign-in"), "challenge")

    def test_intervals_and_threshold_floor(self):
        from .training import operating_threshold, wilson

        self.assertIsNone(wilson(0, 0))
        low, high = wilson(9, 10)
        self.assertLess(low, 0.9)
        self.assertLess(0.9, high)
        self.assertEqual(operating_threshold([0, 1], [0.9, 0.95]), 0.5)


if __name__ == "__main__":
    unittest.main()
