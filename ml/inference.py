"""Single entry point for scoring a completed session.

Uses the trained BotBiometricNet artifact when one matches the current feature version, otherwise the
explainable rules. Rule reasons are always kept, so every score stays explainable.
"""

import os
from pathlib import Path

from .features import FEATURE_VERSION
from .model import BotBiometricNet
from .scoring import MODEL_VERSION as RULES_VERSION, score_session


DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "botbiometricnet.json"
POLICY = {"allow_below": 40, "block_at": 75}
ENFORCED_TASKS = {"sign-in"}
_cache = {}


def model_path():
    return Path(os.environ.get("HUMANPROOF_MODEL_PATH") or DEFAULT_MODEL_PATH)


def load_model():
    """Load the artifact once per file change; an unusable artifact falls back to the rules."""
    path = model_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        model = BotBiometricNet.load(path)
    except (OSError, ValueError, KeyError, TypeError):
        model = None
    if model and model.feature_version != FEATURE_VERSION:
        model = None
    _cache[path] = (stamp, model)
    return model


def current_model_version():
    model = load_model()
    return model.version if model else RULES_VERSION


def decide(result, task):
    """Observe in the lab; on protected actions allow, challenge, or block."""
    if task not in ENFORCED_TASKS:
        return "observe"
    if result["status"] != "ready" or result["risk_score"] is None:
        return "challenge"
    if result["risk_score"] >= POLICY["block_at"]:
        return "block"
    return "challenge" if result["risk_score"] >= POLICY["allow_below"] else "allow"


def assess(events, fingerprint=None, network=None, task=None):
    result = score_session(events, fingerprint, network)
    result["rule_layer_scores"] = result["layer_scores"]
    model = load_model()
    if model and result["status"] == "ready":
        prediction = model.predict(result["features"])
        result.update({
            "risk_score": prediction["score"],
            "model_score": prediction["score"],
            "model_version": model.version,
            "layer_scores": {layer: info["score"] for layer, info in prediction["layers"].items()},
            "model_explanation": prediction,
            "operating_threshold": model.spec.get("operating_threshold"),
        })
        result["limitations"] = [item for item in result["limitations"] if not item.startswith("Rule points")] + [
            "BotBiometricNet is trained on a pilot dataset; see docs/model-report.md for held-out results."]
    result["decision"] = decide(result, task)
    result["policy"] = {"enforced": task in ENFORCED_TASKS, **POLICY}
    return result
