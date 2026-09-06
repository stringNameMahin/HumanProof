"""Deterministic features for the local interaction demo."""

import math
from statistics import fmean, pstdev


FEATURE_VERSION = "features-v1"
EVENT_TYPES = {"move", "click", "scroll", "focus", "shortcut"}
MAX_MOTION_GAP_MS = 2000
MAX_PLOT_POINTS = 500


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _round(value):
    return round(value, 4) if value is not None and math.isfinite(value) else None


def _mean(values):
    return _round(fmean(values)) if values else None


def _cv(values):
    average = fmean(values) if values else 0
    return _round(pstdev(values) / average) if average > 0 else None


def _sample(values):
    if len(values) <= MAX_PLOT_POINTS:
        return values
    last = len(values) - 1
    return [values[round(index * last / (MAX_PLOT_POINTS - 1))]
            for index in range(MAX_PLOT_POINTS)]


def _normalize(events):
    quality = {
        "received_events": len(events),
        "usable_events": 0,
        "dropped_events": 0,
        "duplicate_events": 0,
        "sequence_gap_count": 0,
        "out_of_order_events": 0,
        "duplicate_timestamp_count": 0,
        "motion_gap_count": 0,
        "short_motion_interval_count": 0,
        "short_scroll_interval_count": 0,
        "unknown_scroll_source_count": 0,
        "missing_coordinates": 0,
        "timestamp_source": "elapsed_ms",
        "pointer_types": [],
    }
    epoch_origins = [event["ts"] - event["elapsed_ms"] for event in events
                     if isinstance(event, dict)
                     and _number(event.get("ts")) is not None
                     and _number(event.get("elapsed_ms")) is not None]
    epoch_times = [_number(event.get("ts")) for event in events
                   if isinstance(event, dict) and _number(event.get("ts")) is not None]
    origin = epoch_origins[0] if epoch_origins else min(epoch_times, default=0)
    normalized = []
    seen = set()
    previous_time = None
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") not in EVENT_TYPES:
            quality["dropped_events"] += 1
            continue
        elapsed = _number(event.get("elapsed_ms"))
        if elapsed is None:
            timestamp = _number(event.get("ts"))
            elapsed = timestamp - origin if timestamp is not None else None
            quality["timestamp_source"] = "includes_epoch_fallback"
        if elapsed is None or not math.isfinite(elapsed) or elapsed < 0:
            quality["dropped_events"] += 1
            continue
        sequence = event.get("seq")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            identity = ("seq", sequence)
            if identity in seen:
                quality["duplicate_events"] += 1
                continue
            seen.add(identity)
        if previous_time is not None and elapsed < previous_time:
            quality["out_of_order_events"] += 1
        previous_time = elapsed
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        x, y = _number(event.get("x")), _number(event.get("y"))
        if event["event_type"] == "move" and (x is None or y is None):
            quality["missing_coordinates"] += 1
        normalized.append({
            "event_type": event["event_type"], "elapsed_ms": elapsed,
            "x": x, "y": y, "metadata": metadata,
        })
    normalized.sort(key=lambda event: event["elapsed_ms"])
    sequences = sorted(identity[1] for identity in seen if identity[1] >= 0)
    quality["sequence_gap_count"] = (sequences[0] if sequences else 0) + sum(
        right - left - 1 for left, right in zip(sequences, sequences[1:]))
    quality["usable_events"] = len(normalized)
    quality["duplicate_timestamp_count"] = sum(
        left["elapsed_ms"] == right["elapsed_ms"]
        for left, right in zip(normalized, normalized[1:]))
    quality["pointer_types"] = sorted({
        event["metadata"].get("pointer_type") for event in normalized
        if event["metadata"].get("pointer_type") in {"mouse", "touch", "pen"}
    })
    return normalized, quality


def _mouse_features(events, quality):
    moves = [event for event in events if event["event_type"] == "move"
             and event["x"] is not None and event["y"] is not None
             and event["metadata"].get("pointer_type") not in {"touch", "pen"}]
    distances, speeds, accelerations, turns, samples = [], [], [], [], []
    previous_speed = None
    previous_interval = None
    previous_vector = None
    pauses = 0
    valid_intervals = 0
    for left, right in zip(moves, moves[1:]):
        dx, dy = right["x"] - left["x"], right["y"] - left["y"]
        distance = math.hypot(dx, dy)
        interval = right["elapsed_ms"] - left["elapsed_ms"]
        distances.append(distance)
        if interval < 1 or interval > MAX_MOTION_GAP_MS:
            if interval < 1:
                quality["short_motion_interval_count"] += 1
            if interval > MAX_MOTION_GAP_MS:
                quality["motion_gap_count"] += 1
                pauses += 1
            previous_speed = previous_vector = previous_interval = None
            continue
        valid_intervals += 1
        speed = distance * 1000 / interval
        if interval >= 250:
            pauses += 1
        speeds.append(speed)
        samples.append({"elapsed_ms": _round(right["elapsed_ms"]),
                        "speed_px_s": _round(speed)})
        if previous_speed is not None:
            accelerations.append(abs(speed - previous_speed) * 2000 / (interval + previous_interval))
        if previous_vector is not None and distance > 0:
            previous_distance = math.hypot(*previous_vector)
            if previous_distance > 0:
                cosine = (dx * previous_vector[0] + dy * previous_vector[1]) / (
                    distance * previous_distance)
                turns.append(math.degrees(math.acos(max(-1, min(1, cosine)))))
        previous_speed = speed
        previous_interval = interval
        previous_vector = (dx, dy)
    path_length = sum(distances)
    displacement = math.hypot(moves[-1]["x"] - moves[0]["x"],
                              moves[-1]["y"] - moves[0]["y"]) if moves else 0
    return {
        "available": valid_intervals >= 5,
        "move_count": len(moves),
        "valid_interval_count": valid_intervals,
        "path_length_px": _round(path_length),
        "displacement_px": _round(displacement),
        "mean_speed_px_s": _mean(speeds),
        "max_speed_px_s": _round(max(speeds)) if speeds else None,
        "speed_cv": _cv(speeds),
        "mean_acceleration_px_s2": _mean(accelerations),
        "straightness": _round(displacement / path_length) if path_length else None,
        "mean_turn_angle_deg": _mean(turns),
        "pause_count": pauses,
        "speed_samples": _sample(samples),
        "path": _sample([{"x": event["x"], "y": event["y"],
                          "elapsed_ms": _round(event["elapsed_ms"])} for event in moves]),
    }


def _timing_features(events, quality):
    intervals = [right["elapsed_ms"] - left["elapsed_ms"]
                 for left, right in zip(events, events[1:])]
    clicks = [event for event in events if event["event_type"] == "click"]
    click_intervals = [right["elapsed_ms"] - left["elapsed_ms"]
                       for left, right in zip(clicks, clicks[1:])]
    scrolls = [event for event in events if event["event_type"] == "scroll"]
    scroll_distances, scroll_speeds, scroll_accelerations = [], [], []
    previous_scrolls = {}
    for event in scrolls:
        delta = _number(event["metadata"].get("delta_y"))
        if delta is not None:
            scroll_distances.append(abs(delta))
    for right in scrolls:
        source = right["metadata"].get("scroll_source", "unknown")
        source = source if source in {"page", "panel"} else "unknown"
        if source == "unknown":
            quality["unknown_scroll_source_count"] += 1
        previous = previous_scrolls.get(source)
        previous_scrolls[source] = (right, None, None)
        if previous is None:
            continue
        left, previous_speed, previous_interval = previous
        old_y = _number(left["metadata"].get("scroll_y"))
        new_y = _number(right["metadata"].get("scroll_y"))
        interval = right["elapsed_ms"] - left["elapsed_ms"]
        if interval < 1:
            quality["short_scroll_interval_count"] += 1
        if old_y is None or new_y is None or not 1 <= interval <= MAX_MOTION_GAP_MS:
            continue
        speed = abs(new_y - old_y) * 1000 / interval
        scroll_speeds.append(speed)
        if previous_speed is not None:
            scroll_accelerations.append(abs(speed - previous_speed) * 2000 / (interval + previous_interval))
        previous_scrolls[source] = (right, speed, interval)
    duration = events[-1]["elapsed_ms"] - events[0]["elapsed_ms"] if events else 0
    interactions = [event for event in events if event["event_type"] != "focus"]
    interaction_duration = (interactions[-1]["elapsed_ms"] - interactions[0]["elapsed_ms"]
                            if interactions else 0)
    positive_intervals = [value for value in intervals if value > 0]
    average = fmean(positive_intervals) if positive_intervals else 0
    deviation = pstdev(positive_intervals) if positive_intervals else 0
    burstiness = (deviation - average) / (deviation + average) if average else None
    return {
        "available": len(events) >= 5 and duration >= 1000,
        "duration_ms": _round(duration),
        "event_count": len(events),
        "interaction_event_count": len(interactions),
        "interaction_duration_ms": _round(interaction_duration),
        "click_count": len(clicks),
        "click_interval_count": len(click_intervals),
        "mean_click_interval_ms": _mean(click_intervals),
        "click_interval_cv": _cv(click_intervals),
        "scroll_count": len(scrolls),
        "scroll_sources": sorted(previous_scrolls),
        "scroll_distance_px": _round(sum(scroll_distances)),
        "mean_scroll_speed_px_s": _mean(scroll_speeds),
        "mean_scroll_acceleration_px_s2": _mean(scroll_accelerations),
        "idle_period_count": sum(value >= 1000 for value in intervals),
        "idle_time_ms": _round(sum(value for value in intervals if value >= 1000)),
        "burstiness": _round(burstiness),
        "focus_changes": sum(event["event_type"] == "focus" for event in events),
        "shortcut_count": sum(event["event_type"] == "shortcut" for event in events),
    }


def _browser_features(fingerprint):
    fingerprint = fingerprint if isinstance(fingerprint, dict) else {}
    webdriver = fingerprint.get("webdriver")
    webdriver = webdriver if isinstance(webdriver, bool) else None
    fields = {
        "webdriver": webdriver,
        "hardware_concurrency": _number(fingerprint.get("hardware_concurrency")),
        "max_touch_points": _number(fingerprint.get("max_touch_points")),
        "viewport_width": _number(fingerprint.get("viewport_width")),
        "viewport_height": _number(fingerprint.get("viewport_height")),
        "screen_width": _number(fingerprint.get("screen_width")),
        "screen_height": _number(fingerprint.get("screen_height")),
        "pixel_ratio": _number(fingerprint.get("pixel_ratio")),
    }
    for name in ("language", "timezone", "platform", "pointer_type"):
        value = fingerprint.get(name)
        fields[name] = value[:128] if isinstance(value, str) else None
    fields["available"] = any(value is not None for value in fields.values())
    fields["unavailable_signals"] = [key for key, value in fields.items() if value is None]
    return fields


def extract_features(events, fingerprint=None):
    """Extract observed features; missing evidence is represented explicitly."""
    events, quality = _normalize(list(events))
    mouse = _mouse_features(events, quality)
    timing = _timing_features(events, quality)
    browser = _browser_features(fingerprint)
    layers = {"browser": browser, "mouse": mouse, "timing": timing}
    return {
        "feature_version": FEATURE_VERSION,
        "mouse": mouse,
        "timing": timing,
        "browser": browser,
        "data_quality": quality,
        "available_layers": [name for name, data in layers.items() if data["available"]],
    }
