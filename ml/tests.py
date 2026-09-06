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


if __name__ == "__main__":
    unittest.main()
