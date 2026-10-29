"""Observation-only rules; these scores are not calibrated probabilities."""

from dataclasses import dataclass
from typing import Callable, Union

from .features import FEATURE_VERSION, extract_features


MODEL_VERSION = "rules-v2"
MIN_EVENTS = 20
MIN_DURATION_MS = 5000
LAYERS = ("browser", "mouse", "timing", "network")
BROWSER_INCONSISTENCIES = ("permission_inconsistent", "zero_outer_window", "window_geometry_inconsistent",
                           "empty_languages", "chromium_without_chrome_object", "chromium_without_plugins")


@dataclass(frozen=True)
class Rule:
    code: str
    layer: str
    points: Union[int, Callable[[dict], int]]
    message: str
    applies: Callable[[dict], bool]

    def score(self, features):
        return self.points(features) if callable(self.points) else self.points


def _at_least(value, minimum):
    return value is not None and value >= minimum


def _below(value, maximum):
    return value is not None and value < maximum


def _uniform_straight(features):
    mouse, quality = features["mouse"], features["data_quality"]
    return (mouse["available"] and mouse["valid_interval_count"] >= 30
            and mouse["path_length_px"] >= 500
            and _at_least(mouse["straightness"], 0.995)
            and mouse["speed_cv"] is not None and mouse["speed_cv"] <= 0.06
            and quality["motion_gap_count"] == 0)


def _regular_clicks(features):
    timing = features["timing"]
    return (timing["click_interval_count"] >= 6
            and _at_least(timing["mean_click_interval_ms"], 100)
            and timing["click_interval_cv"] is not None and timing["click_interval_cv"] <= 0.04)


def _inconsistencies(features):
    return sum(features["browser"].get(name) is True for name in BROWSER_INCONSISTENCIES)


def _device_scrolls(features):
    inputs = features["timing"]["scroll_inputs"]
    return sum(count for kind, count in inputs.items() if kind in {"wheel", "touch", "key", "drag"})


def _mechanical_typing(features):
    timing = features["timing"]
    regular = (timing["key_count"] >= 9 and timing["key_interval_cv"] is not None
               and timing["key_interval_cv"] < 0.1)
    instant = timing["key_count"] >= 5 and _below(timing["mean_key_press_ms"], 10)
    return regular or instant


def _network(features, name):
    return (features.get("network") or {}).get(name) is True


RULES = [
    Rule("webdriver_reported", "browser", 35,
         "The browser reports automation control (navigator.webdriver).",
         lambda f: f["browser"]["webdriver"] is True),
    Rule("headless_user_agent", "browser", 30,
         "The user agent identifies a headless browser.",
         lambda f: f["browser"].get("headless_user_agent") is True),
    Rule("devtools_protocol_attached", "browser", 15,
         "A DevTools Protocol client appears attached. Open developer tools also trigger this.",
         lambda f: f["browser"].get("cdp_detected") is True),
    Rule("software_renderer", "browser", 10,
         "WebGL uses a software renderer, common in headless and virtual machines.",
         lambda f: f["browser"].get("software_renderer") is True),
    Rule("browser_inconsistencies", "browser", lambda f: min(30, 10 * _inconsistencies(f)),
         "Browser properties contradict each other (permissions, window size, languages, or Chrome objects).",
         lambda f: _inconsistencies(f) > 0),
    Rule("uniform_straight_motion", "mouse", 25,
         "A long pointer path is almost straight with very uniform speed.", _uniform_straight),
    Rule("no_micro_jitter", "mouse", 15,
         "Pointer paths have no sideways micro-movement; device-driven motion is rarely this smooth.",
         lambda f: f["mouse"]["micro_jitter_samples"] >= 40 and _below(f["mouse"]["micro_jitter_px"], 0.05)),
    Rule("center_precise_clicks", "mouse", 20,
         "Target clicks land almost exactly on the target centre.",
         lambda f: f["mouse"]["target_click_count"] >= 4 and _below(f["mouse"]["mean_click_offset_ratio"], 0.1)),
    Rule("approachless_clicks", "mouse", 25,
         "Several clicks happened where the pointer had not moved to first.",
         lambda f: f["mouse"]["approachless_click_count"] >= 3),
    Rule("constant_movement_time", "mouse", 15,
         "Movement time stays constant across target distances and sizes, violating Fitts' law.",
         lambda f: f["mouse"]["fitts_count"] >= 5 and _below(f["mouse"]["movement_time_cv"], 0.08)),
    Rule("regular_click_timing", "timing", 25,
         "At least seven clicks have nearly identical spacing.", _regular_clicks),
    Rule("instant_press_release", "timing", 25,
         "Mouse buttons are released almost instantly after being pressed.",
         lambda f: f["timing"]["press_count"] >= 3 and _below(f["timing"]["median_press_ms"], 15)),
    Rule("untrusted_events", "timing", 40,
         "Page scripts generated input events instead of an input device.",
         lambda f: f["timing"]["untrusted_event_count"] >= 1),
    Rule("programmatic_scroll", "timing", 15,
         "Most scrolling happened without wheel, touch, keyboard, or scrollbar input.",
         lambda f: f["timing"]["programmatic_scroll_count"] >= 3
         and f["timing"]["programmatic_scroll_count"] >= _device_scrolls(f)),
    Rule("untyped_text_input", "timing", 25,
         "Text appeared in a field without key presses. Paste and autofill are recorded separately.",
         lambda f: f["timing"]["untyped_input_count"] >= 1),
    Rule("mechanical_typing", "timing", 20,
         "Key presses are released instantly or arrive at a metronomic rhythm.", _mechanical_typing),
    Rule("automation_client_library", "network", 40,
         "Requests came from an HTTP library rather than a browser.",
         lambda f: _network(f, "client_library_user_agent")),
    Rule("missing_browser_headers", "network", 20,
         "Requests lack headers that browsers always send.",
         lambda f: _network(f, "missing_browser_headers")),
    Rule("headless_client_hints", "network", 25,
         "Client hint headers identify a headless browser.",
         lambda f: _network(f, "headless_client_hints")),
    Rule("tls_user_agent_mismatch", "network", 35,
         "The TLS handshake does not match the browser named in the user agent.",
         lambda f: _network(f, "tls_user_agent_mismatch")),
    Rule("header_user_agent_mismatch", "network", 25,
         "The user agent sent in headers differs from the one the page reported.",
         lambda f: _network(f, "user_agent_mismatch")),
    Rule("language_mismatch", "network", 10,
         "The Accept-Language header disagrees with the browser language.",
         lambda f: _network(f, "language_mismatch")),
]


def rule_catalogue():
    return [{"code": rule.code, "layer": rule.layer, "message": rule.message,
             "points": "10 per check, max 30" if callable(rule.points) else str(rule.points)} for rule in RULES]


def _limitations(features):
    mouse, browser, quality = features["mouse"], features["browser"], features["data_quality"]
    limitations = [
        "Rule points are explanations, not calibrated probabilities.",
        "Client signals can be altered; detection accuracy is only measured on the pilot dataset.",
        "The evaluation threshold of 60 is a demo choice, not a validated operating point.",
    ]
    if not mouse["available"]:
        limitations.append("Mouse evidence is unavailable; touch and keyboard use are not penalized.")
    if not browser["available"]:
        limitations.append("Browser evidence is unavailable and is not penalized.")
    if not (features.get("network") or {}).get("available"):
        limitations.append("Network evidence is unavailable for this session.")
    if quality["motion_gap_count"]:
        limitations.append("Long mouse sampling gaps were excluded from speed calculations.")
    if quality["short_motion_interval_count"] or quality["short_scroll_interval_count"]:
        limitations.append("Motion intervals below 1 ms were excluded from rate calculations.")
    if quality["duplicate_events"] or quality["dropped_events"]:
        limitations.append("Duplicate or invalid events were excluded from features.")
    if quality["sequence_gap_count"]:
        limitations.append("Missing event sequence numbers indicate incomplete collection and do not add risk points.")
    if quality["unknown_scroll_source_count"]:
        limitations.append("Some scroll events lack a source; their surface continuity cannot be verified.")
    return limitations


def evaluate_rules(features):
    """Return fired rule reasons and capped per-layer rule scores."""
    reasons = [{"code": rule.code, "layer": rule.layer, "message": rule.message, "points": rule.score(features)}
               for rule in RULES if rule.applies(features)]
    layer_scores = {layer: min(100, sum(reason["points"] for reason in reasons if reason["layer"] == layer))
                    for layer in LAYERS}
    return reasons, layer_scores


def is_ready(features):
    timing = features["timing"]
    return (timing["interaction_event_count"] >= MIN_EVENTS
            and timing["interaction_duration_ms"] >= MIN_DURATION_MS)


def score_features(features):
    ready = is_ready(features)
    reasons, layer_scores = evaluate_rules(features) if ready else ([], {layer: None for layer in LAYERS})
    if not ready:
        reasons.append({"code": "insufficient_activity", "layer": None,
                        "message": "Collect at least 20 interaction events spanning 5 seconds.", "points": 0})
    elif not reasons:
        reasons.append({"code": "no_rule_flags", "layer": None,
                        "message": "No baseline rule fired. This does not establish that a visitor is human.",
                        "points": 0})
    rule_score = min(100, sum(reason["points"] for reason in reasons)) if ready else None
    return {
        "risk_score": rule_score,
        "rule_score": rule_score,
        "status": "ready" if ready else "insufficient_data",
        "decision": "observe",
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "reasons": reasons,
        "layer_scores": layer_scores,
        "features": features,
        "available_layers": features["available_layers"],
        "limitations": _limitations(features),
    }


def score_session(events, fingerprint=None, network=None):
    return score_features(extract_features(events, fingerprint, network))
