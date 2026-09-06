"""Observation-only rules; these scores are not calibrated probabilities."""

from .features import FEATURE_VERSION, extract_features


MODEL_VERSION = "rules-v1"
MIN_EVENTS = 20
MIN_DURATION_MS = 5000


def score_session(events, fingerprint=None):
    features = extract_features(events, fingerprint)
    mouse, timing, browser = (features[name] for name in ("mouse", "timing", "browser"))
    quality = features["data_quality"]
    reasons = []
    limitations = [
        "Experimental rules, not a trained BotBiometricNet model or a probability.",
        "Client signals can be altered; no detection accuracy has been established.",
        "The evaluation threshold of 60 is a demo choice, not a validated operating point.",
        "Network analysis is not implemented.",
    ]
    ready = (timing["interaction_event_count"] >= MIN_EVENTS
             and timing["interaction_duration_ms"] >= MIN_DURATION_MS)
    if not ready:
        reasons.append({
            "code": "insufficient_activity",
            "message": "Collect at least 20 interaction events spanning 5 seconds.",
            "points": 0,
        })
    if not mouse["available"]:
        limitations.append("Mouse evidence is unavailable; touch and keyboard use are not penalized.")
    if not browser["available"]:
        limitations.append("Browser evidence is unavailable and is not penalized.")
    if quality["motion_gap_count"]:
        limitations.append("Long mouse sampling gaps were excluded from speed calculations.")
    if quality["short_motion_interval_count"] or quality["short_scroll_interval_count"]:
        limitations.append("Motion intervals below 1 ms were excluded from rate calculations.")
    if quality["duplicate_events"] or quality["dropped_events"]:
        limitations.append("Duplicate or invalid events were excluded from features.")

    if ready:
        if browser["webdriver"] is True:
            reasons.append({
                "code": "webdriver_reported",
                "message": "The browser reports automation control. This is one signal only.",
                "points": 35,
            })
        if (mouse["available"] and mouse["valid_interval_count"] >= 30
                and mouse["path_length_px"] >= 500
                and mouse["straightness"] is not None and mouse["straightness"] >= 0.995
                and mouse["speed_cv"] is not None and mouse["speed_cv"] <= 0.06
                and quality["motion_gap_count"] == 0):
            reasons.append({
                "code": "uniform_straight_motion",
                "message": "A long pointer path is almost straight with very uniform speed.",
                "points": 25,
            })
        if (timing["click_interval_count"] >= 6
                and timing["mean_click_interval_ms"] is not None
                and timing["mean_click_interval_ms"] >= 100
                and timing["click_interval_cv"] is not None
                and timing["click_interval_cv"] <= 0.04):
            reasons.append({
                "code": "regular_click_timing",
                "message": "At least seven clicks have nearly identical spacing.",
                "points": 25,
            })
        if not reasons:
            reasons.append({
                "code": "no_rule_flags",
                "message": "No baseline rule fired. This does not establish that a visitor is human.",
                "points": 0,
            })
    return {
        "risk_score": min(100, sum(reason["points"] for reason in reasons)) if ready else None,
        "status": "ready" if ready else "insufficient_data",
        "decision": "observe",
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "reasons": reasons,
        "features": features,
        "available_layers": features["available_layers"],
        "limitations": limitations,
    }
