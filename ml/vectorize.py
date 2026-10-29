"""Fixed numeric feature vectors per detection layer, shared by training and inference."""

import math

# (feature name, transform). Transforms:
#   log    log1p of a non-negative number, missing indicator added
#   raw    the number as is, missing indicator added
#   flag   True/False as 1/0, missing indicator added
#   flag0  True as 1, False or missing as 0, no indicator (for evidence that depends on transport)
LAYER_FEATURES = {
    "browser": [
        ("webdriver", "flag"), ("headless_user_agent", "flag"), ("software_renderer", "flag"),
        ("cdp_detected", "flag"), ("anomaly_count", "log"), ("plugins_count", "log"), ("languages_count", "log"),
        ("font_count", "log"), ("hardware_concurrency", "log"), ("pixel_ratio", "raw"),
        ("max_touch_points", "log"), ("device_memory", "log"),
    ],
    "mouse": [
        ("speed_cv", "raw"), ("mean_speed_px_s", "log"), ("mean_acceleration_px_s2", "log"),
        ("mean_jerk_px_s3", "log"), ("straightness", "raw"), ("mean_turn_angle_deg", "raw"),
        ("mean_curvature_rad_px", "log"), ("micro_jitter_px", "log"), ("tremor_band_ratio", "raw"),
        ("fitts_slope_ms_per_bit", "raw"), ("fitts_r2", "raw"), ("movement_time_cv", "raw"),
        ("mean_click_offset_ratio", "raw"), ("approachless_click_count", "log"), ("pause_count", "log"),
        ("throughput_bits_s", "raw"),
    ],
    "timing": [
        ("median_press_ms", "log"), ("press_cv", "raw"), ("instant_press_share", "raw"),
        ("click_interval_cv", "raw"), ("mean_click_interval_ms", "log"), ("burstiness", "raw"),
        ("time_to_first_interaction_ms", "log"), ("programmatic_scroll_share", "raw"),
        ("mean_scroll_speed_px_s", "log"), ("mean_key_press_ms", "log"), ("key_interval_cv", "raw"),
        ("untyped_input", "flag0"), ("unknown_input", "flag0"), ("untrusted_events", "flag0"),
        ("idle_period_count", "log"), ("shortcut_count", "log"),
    ],
    "network": [
        ("client_library_user_agent", "flag0"), ("missing_browser_headers", "flag0"),
        ("headless_client_hints", "flag0"), ("user_agent_mismatch", "flag0"), ("language_mismatch", "flag0"),
        ("tls_user_agent_mismatch", "flag0"), ("sec_fetch_share", "raw"), ("client_hint_share", "raw"),
        ("accept_language_share", "raw"), ("header_count_mean", "raw"),
    ],
}
LAYERS = tuple(LAYER_FEATURES)


def _derived(features):
    timing = features.get("timing", {})
    presses = timing.get("press_count") or 0
    scrolls = timing.get("scroll_count") or 0
    return {
        "instant_press_share": timing.get("instant_press_count", 0) / presses if presses else None,
        "programmatic_scroll_share": timing.get("programmatic_scroll_count", 0) / scrolls if scrolls else None,
        "untyped_input": (timing.get("untyped_input_count") or 0) > 0,
        "unknown_input": (timing.get("unknown_input_count") or 0) > 0,
        "untrusted_events": (timing.get("untrusted_event_count") or 0) > 0,
    }


def _value(raw, transform):
    if transform in ("flag", "flag0"):
        if isinstance(raw, bool):
            return float(raw)
        return 0.0 if transform == "flag0" else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return None
    if transform == "log":
        return math.log1p(max(float(raw), 0.0))
    return float(raw)


def column_names(layer):
    names = []
    for name, transform in LAYER_FEATURES[layer]:
        names.append(name)
        if transform != "flag0":
            names.append(f"{name}__missing")
    return names


def layer_available(features, layer):
    return bool((features.get(layer) or {}).get("available"))


def vectorize(features, layer):
    """Return (values with None for missing, display values) aligned with column_names(layer)."""
    source = dict(features.get(layer) or {})
    if layer == "timing":
        source.update(_derived(features))
    values, shown = [], []
    for name, transform in LAYER_FEATURES[layer]:
        raw = source.get(name)
        value = _value(raw, transform)
        values.append(value)
        shown.append(raw)
        if transform != "flag0":
            values.append(1.0 if value is None else 0.0)
            shown.append(value is None)
    return values, shown
