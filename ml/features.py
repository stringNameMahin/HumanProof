"""Deterministic features for the local interaction demo."""

import math
import re
from statistics import fmean, median, pstdev

import numpy as np


FEATURE_VERSION = "features-v2"
EVENT_TYPES = {"move", "click", "scroll", "focus", "shortcut", "key", "input"}
MAX_MOTION_GAP_MS = 2000
MAX_PLOT_POINTS = 500
STROKE_GAP_MS = 100
TREMOR_RATE_HZ = 60
TREMOR_BAND_HZ = (6, 14)
SOFTWARE_RENDERERS = re.compile(r"swiftshader|llvmpipe|softpipe|software|mesa offscreen|microsoft basic render", re.I)
CHROMIUM_UA = re.compile(r"(Chrome|Chromium|CriOS|Edg)/")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _round(value):
    return round(value, 4) if value is not None and math.isfinite(value) else None


def _mean(values):
    return _round(fmean(values)) if values else None


def _median(values):
    return _round(median(values)) if values else None


def _cv(values):
    average = fmean(values) if values else 0
    return _round(pstdev(values) / average) if average > 0 else None


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return _round(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


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


def _strokes(moves):
    """Split pointer samples into continuous strokes separated by pauses."""
    strokes, current = [], []
    for event in moves:
        if current:
            interval = event["elapsed_ms"] - current[-1]["elapsed_ms"]
            if interval < 1:
                continue
            if interval > STROKE_GAP_MS:
                if len(current) > 1:
                    strokes.append(current)
                current = []
        current.append(event)
    if len(current) > 1:
        strokes.append(current)
    return strokes


def _lateral_jitter(strokes):
    """Median sideways distance between each sample and a 5-sample moving average.

    The median ignores the few samples at path corners, where any smooth trajectory deviates.
    """
    residuals = []
    for stroke in strokes:
        for index in range(2, len(stroke) - 2):
            window = stroke[index - 2:index + 3]
            mean_x = fmean(point["x"] for point in window)
            mean_y = fmean(point["y"] for point in window)
            direction_x = stroke[index + 2]["x"] - stroke[index - 2]["x"]
            direction_y = stroke[index + 2]["y"] - stroke[index - 2]["y"]
            length = math.hypot(direction_x, direction_y)
            if length < 1:
                continue
            offset_x, offset_y = stroke[index]["x"] - mean_x, stroke[index]["y"] - mean_y
            residuals.append(abs(offset_x * direction_y - offset_y * direction_x) / length)
    return _median(residuals), len(residuals)


def _tremor(strokes):
    """Share of velocity power in the physiological tremor band, on a uniform 60 Hz grid."""
    band_power = total_power = peak_power = 0.0
    peak_frequency = None
    analysed = 0
    step = 1000 / TREMOR_RATE_HZ
    for stroke in strokes:
        times = np.array([point["elapsed_ms"] for point in stroke], dtype=float)
        if len(stroke) < 24 or times[-1] - times[0] < 500:
            continue
        grid = np.arange(times[0], times[-1], step)
        if len(grid) < 32:
            continue
        x = np.interp(grid, times, [point["x"] for point in stroke])
        y = np.interp(grid, times, [point["y"] for point in stroke])
        velocity = [np.diff(x) * TREMOR_RATE_HZ, np.diff(y) * TREMOR_RATE_HZ]
        window = np.hanning(len(velocity[0]))
        frequencies = np.fft.rfftfreq(len(velocity[0]), d=1 / TREMOR_RATE_HZ)
        power = sum(np.abs(np.fft.rfft((axis - axis.mean()) * window)) ** 2 for axis in velocity)
        useful = frequencies >= 1
        band = (frequencies >= TREMOR_BAND_HZ[0]) & (frequencies <= TREMOR_BAND_HZ[1])
        band_power += float(power[band].sum())
        total_power += float(power[useful].sum())
        search = (frequencies >= 4) & (frequencies <= 20)
        if search.any() and float(power[search].max()) > peak_power:
            peak_power = float(power[search].max())
            peak_frequency = float(frequencies[search][int(np.argmax(power[search]))])
        analysed += 1
    if not analysed or total_power <= 1e-9:
        return None, None, analysed
    return _round(band_power / total_power), _round(peak_frequency), analysed


def _target_acquisition(events):
    """Fitts' law fit (MT = a + b * ID) and click precision for marked targets."""
    rows, offsets = [], []
    last_click = None
    onset = None
    for event in events:
        if event["x"] is None or event["y"] is None:
            continue
        if event["event_type"] == "move":
            if last_click and onset is None and math.hypot(
                    event["x"] - last_click["x"], event["y"] - last_click["y"]) > 3:
                onset = event["elapsed_ms"]
            continue
        if event["event_type"] != "click":
            continue
        metadata = event["metadata"]
        center_x = _number(metadata.get("target_center_x"))
        center_y = _number(metadata.get("target_center_y"))
        width = min(_number(metadata.get("target_width")) or 0, _number(metadata.get("target_height")) or 0)
        if metadata.get("target_kind") == "target" and center_x is not None and center_y is not None and width > 0:
            offsets.append(math.hypot(event["x"] - center_x, event["y"] - center_y) / (width / 2))
            if last_click:
                distance = math.hypot(center_x - last_click["x"], center_y - last_click["y"])
                movement_time = event["elapsed_ms"] - (onset if onset is not None else last_click["elapsed_ms"])
                if distance > 0 and 50 <= movement_time <= 5000:
                    rows.append((math.log2(distance / width + 1), movement_time))
        last_click = event
        onset = None
    times = [row[1] for row in rows]
    result = {
        "fitts_count": len(rows), "fitts_slope_ms_per_bit": None, "fitts_intercept_ms": None,
        "fitts_r2": None, "mean_movement_time_ms": _mean(times), "movement_time_cv": _cv(times),
        "throughput_bits_s": _mean([row[0] * 1000 / row[1] for row in rows]),
        "target_click_count": len(offsets), "mean_click_offset_ratio": _mean(offsets),
        "fitts_points": [{"id_bits": _round(row[0]), "movement_time_ms": _round(row[1])} for row in rows],
    }
    if len(rows) >= 4:
        ids = [row[0] for row in rows]
        mean_id, mean_time = fmean(ids), fmean(times)
        variance_id = sum((value - mean_id) ** 2 for value in ids)
        variance_time = sum((value - mean_time) ** 2 for value in times)
        if variance_id > 1e-9:
            covariance = sum((a - mean_id) * (b - mean_time) for a, b in zip(ids, times))
            slope = covariance / variance_id
            result["fitts_slope_ms_per_bit"] = _round(slope)
            result["fitts_intercept_ms"] = _round(mean_time - slope * mean_id)
            result["fitts_r2"] = _round(covariance ** 2 / (variance_id * variance_time)) if variance_time > 1e-9 else 0.0
    return result


def _approachless_clicks(events):
    """Pointer clicks with no pointer sample nearby in the preceding second."""
    count = 0
    recent = []
    for event in events:
        if event["x"] is None or event["y"] is None:
            continue
        if event["event_type"] == "move":
            recent.append(event)
        elif event["event_type"] == "click":
            recent = [move for move in recent if event["elapsed_ms"] - move["elapsed_ms"] <= 1000]
            if not any(math.hypot(move["x"] - event["x"], move["y"] - event["y"]) <= 30 for move in recent):
                count += 1
    return count


def _mouse_features(events, quality):
    pointer_events = [event for event in events if event["metadata"].get("pointer_type") not in {"touch", "pen"}]
    moves = [event for event in pointer_events if event["event_type"] == "move"
             and event["x"] is not None and event["y"] is not None]
    distances, speeds, accelerations, turns, samples = [], [], [], [], []
    jerks, curvatures = [], []
    previous_speed = None
    previous_interval = None
    previous_vector = None
    previous_acceleration = None
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
            previous_speed = previous_vector = previous_interval = previous_acceleration = None
            continue
        valid_intervals += 1
        speed = distance * 1000 / interval
        if interval >= 250:
            pauses += 1
        speeds.append(speed)
        samples.append({"elapsed_ms": _round(right["elapsed_ms"]),
                        "speed_px_s": _round(speed)})
        if previous_speed is not None:
            acceleration = (speed - previous_speed) * 2000 / (interval + previous_interval)
            accelerations.append(abs(acceleration))
            if previous_acceleration is not None:
                jerks.append(abs(acceleration - previous_acceleration) * 1000 / interval)
            previous_acceleration = acceleration
        if previous_vector is not None and distance > 0:
            previous_distance = math.hypot(*previous_vector)
            if previous_distance > 0:
                cosine = (dx * previous_vector[0] + dy * previous_vector[1]) / (
                    distance * previous_distance)
                angle = math.acos(max(-1, min(1, cosine)))
                turns.append(math.degrees(angle))
                if distance >= 1 and previous_distance >= 1:
                    curvatures.append(angle * 2 / (distance + previous_distance))
        previous_speed = speed
        previous_interval = interval
        previous_vector = (dx, dy)
    path_length = sum(distances)
    displacement = math.hypot(moves[-1]["x"] - moves[0]["x"],
                              moves[-1]["y"] - moves[0]["y"]) if moves else 0
    strokes = _strokes(moves)
    jitter, jitter_samples = _lateral_jitter(strokes)
    tremor_ratio, tremor_peak, tremor_strokes = _tremor(strokes)
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
        "mean_jerk_px_s3": _mean(jerks),
        "straightness": _round(displacement / path_length) if path_length else None,
        "mean_turn_angle_deg": _mean(turns),
        "mean_curvature_rad_px": _mean(curvatures),
        "pause_count": pauses,
        "stroke_count": len(strokes),
        "micro_jitter_px": jitter,
        "micro_jitter_samples": jitter_samples,
        "tremor_band_ratio": tremor_ratio,
        "tremor_peak_hz": tremor_peak,
        "tremor_stroke_count": tremor_strokes,
        "approachless_click_count": _approachless_clicks(pointer_events),
        **_target_acquisition(pointer_events),
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
    presses = [value for value in (_number(event["metadata"].get("press_ms")) for event in clicks)
               if value is not None]
    keys = [event for event in events if event["event_type"] == "key"]
    key_presses = [value for value in (_number(event["metadata"].get("press_ms")) for event in keys)
                   if value is not None]
    typing = [event for event in keys if event["metadata"].get("key_class") in {"char", "space", "backspace"}]
    key_intervals = [right["elapsed_ms"] - left["elapsed_ms"] for left, right in zip(typing, typing[1:])
                     if right["elapsed_ms"] - left["elapsed_ms"] <= 2000]
    scroll_inputs = {}
    for event in scrolls:
        kind = event["metadata"].get("scroll_input") or "unknown"
        scroll_inputs[kind] = scroll_inputs.get(kind, 0) + 1
    input_kinds = {}
    for event in events:
        if event["event_type"] == "input":
            kind = event["metadata"].get("input_kind") or "unknown"
            input_kinds[kind] = input_kinds.get(kind, 0) + 1
    return {
        "available": len(events) >= 5 and duration >= 1000,
        "duration_ms": _round(duration),
        "event_count": len(events),
        "interaction_event_count": len(interactions),
        "interaction_duration_ms": _round(interaction_duration),
        "time_to_first_interaction_ms": _round(interactions[0]["elapsed_ms"]) if interactions else None,
        "click_count": len(clicks),
        "click_interval_count": len(click_intervals),
        "mean_click_interval_ms": _mean(click_intervals),
        "click_interval_cv": _cv(click_intervals),
        "click_interval_p10_ms": _percentile(click_intervals, 0.1),
        "click_interval_p50_ms": _percentile(click_intervals, 0.5),
        "click_interval_p90_ms": _percentile(click_intervals, 0.9),
        "press_count": len(presses),
        "mean_press_ms": _mean(presses),
        "median_press_ms": _median(presses),
        "press_cv": _cv(presses),
        "instant_press_count": sum(value < 15 for value in presses),
        "scroll_count": len(scrolls),
        "scroll_sources": sorted(previous_scrolls),
        "scroll_inputs": dict(sorted(scroll_inputs.items())),
        "programmatic_scroll_count": scroll_inputs.get("programmatic", 0),
        "scroll_distance_px": _round(sum(scroll_distances)),
        "mean_scroll_speed_px_s": _mean(scroll_speeds),
        "mean_scroll_acceleration_px_s2": _mean(scroll_accelerations),
        "idle_period_count": sum(value >= 1000 for value in intervals),
        "idle_time_ms": _round(sum(value for value in intervals if value >= 1000)),
        "burstiness": _round(burstiness),
        "focus_changes": sum(event["event_type"] == "focus" for event in events),
        "shortcut_count": sum(event["event_type"] == "shortcut" for event in events),
        "key_count": len(keys),
        "mean_key_press_ms": _mean(key_presses),
        "key_press_cv": _cv(key_presses),
        "mean_key_interval_ms": _mean(key_intervals),
        "key_interval_cv": _cv(key_intervals),
        "input_kinds": dict(sorted(input_kinds.items())),
        "untyped_input_count": input_kinds.get("untyped", 0),
        "unknown_input_count": input_kinds.get("unknown", 0),
        "untrusted_event_count": sum(event["metadata"].get("is_trusted") is False for event in events),
    }


def _geometry_inconsistent(fields):
    """A viewport far larger than its window or screen; ordinary zoom levels stay below the margin."""
    pairs = [(fields["viewport_width"], fields["outer_width"]), (fields["viewport_height"], fields["outer_height"]),
             (fields["viewport_width"], fields["screen_width"]), (fields["viewport_height"], fields["screen_height"])]
    usable = [(inner, outer) for inner, outer in pairs if inner is not None and outer]
    if not usable:
        return None
    return any(inner > outer * 1.6 for inner, outer in usable)


def _browser_anomalies(fields):
    """Consistency checks; each is None when the evidence needed for it is missing."""
    agent = fields["user_agent"]
    brands = fields["ua_brands"] or ""
    renderer = fields["webgl_renderer"]
    chromium = bool(agent and CHROMIUM_UA.search(agent))
    checks = {
        "headless_user_agent": ("HeadlessChrome" in agent or "HeadlessChrome" in brands) if agent else None,
        "software_renderer": bool(SOFTWARE_RENDERERS.search(renderer)) if renderer else None,
        "permission_inconsistent": (fields["notification_permission"] == "denied"
                                    and fields["permission_query_state"] == "prompt")
        if fields["notification_permission"] and fields["permission_query_state"] else None,
        "zero_outer_window": (fields["outer_width"] == 0 or fields["outer_height"] == 0)
        if fields["outer_width"] is not None and fields["outer_height"] is not None else None,
        "window_geometry_inconsistent": _geometry_inconsistent(fields),
        "empty_languages": fields["languages_count"] == 0 if fields["languages_count"] is not None else None,
        "chromium_without_chrome_object": (chromium and fields["has_chrome_object"] is False)
        if agent and fields["has_chrome_object"] is not None else None,
        "chromium_without_plugins": (chromium and fields["plugins_count"] == 0)
        if agent and fields["plugins_count"] is not None else None,
    }
    checks["anomaly_count"] = sum(value is True for value in checks.values())
    return checks


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
    for name in ("outer_width", "outer_height", "color_depth", "device_memory",
                 "languages_count", "plugins_count", "font_count"):
        fields[name] = _number(fingerprint.get(name))
    for name in ("has_chrome_object", "touch_events", "cdp_detected"):
        value = fingerprint.get(name)
        fields[name] = value if isinstance(value, bool) else None
    for name in ("language", "timezone", "platform", "pointer_type", "user_agent", "ua_brands",
                 "webgl_vendor", "webgl_renderer", "notification_permission", "permission_query_state",
                 "canvas_hash", "webgl_hash", "audio_hash", "font_hash"):
        value = fingerprint.get(name)
        fields[name] = value[:512] if isinstance(value, str) else None
    fields["available"] = any(value is not None for value in fields.values())
    fields["unavailable_signals"] = [key for key, value in fields.items() if value is None]
    fields.update(_browser_anomalies(fields))
    return fields


def extract_features(events, fingerprint=None, network=None):
    """Extract observed features; missing evidence is represented explicitly."""
    from .network import network_features

    events, quality = _normalize(list(events))
    mouse = _mouse_features(events, quality)
    timing = _timing_features(events, quality)
    browser = _browser_features(fingerprint)
    network = network_features(network, browser)
    layers = {"browser": browser, "mouse": mouse, "timing": timing, "network": network}
    return {
        "feature_version": FEATURE_VERSION,
        "mouse": mouse,
        "timing": timing,
        "browser": browser,
        "network": network,
        "data_quality": quality,
        "available_layers": [name for name, data in layers.items() if data["available"]],
    }
