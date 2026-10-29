"""BotBiometricNet inference: per-layer logistic models fused by a stacked logistic layer.

The artifact is plain JSON (imputation values, scaling, coefficients), so loading it cannot execute
code, and every prediction can be explained exactly as a sum of feature contributions.
"""

import json
import math
from pathlib import Path

from .vectorize import LAYERS, column_names, layer_available, vectorize


ARTIFACT_SCHEMA = "botbiometricnet-v1"


def sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1 + exp)


class BotBiometricNet:
    def __init__(self, spec):
        if spec.get("schema") != ARTIFACT_SCHEMA:
            raise ValueError("Unsupported model artifact.")
        for layer, layer_spec in spec["layers"].items():
            if layer_spec.get("trained") and layer_spec["features"] != column_names(layer):
                raise ValueError(f"The {layer} layer was trained on a different feature list.")
        self.spec = spec

    @property
    def version(self):
        return self.spec["model_version"]

    @property
    def feature_version(self):
        return self.spec["feature_version"]

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.spec, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def layer_logit(self, layer, features):
        spec = self.spec["layers"].get(layer) or {}
        if not spec.get("trained") or not layer_available(features, layer):
            return None, []
        values, shown = vectorize(features, layer)
        terms = []
        logit = spec["intercept"]
        for index, value in enumerate(values):
            filled = spec["impute"][index] if value is None else value
            term = spec["coef"][index] * (filled - spec["mean"][index]) / spec["scale"][index]
            logit += term
            terms.append({"feature": spec["features"][index], "value": shown[index], "logit": term})
        return logit, terms

    def predict(self, features):
        fusion = self.spec["fusion"]
        total = fusion["intercept"]
        layers, contributions = {}, []
        for layer in LAYERS:
            logit, terms = self.layer_logit(layer, features)
            weight = fusion["weights"].get(layer, 0.0)
            if logit is None:
                layers[layer] = {"available": False, "probability": None, "score": None, "weight": weight,
                                 "contribution": 0.0, "top_features": []}
                continue
            total += weight * logit + fusion["availability"].get(layer, 0.0)
            probability = sigmoid(logit)
            ranked = sorted(terms, key=lambda item: abs(item["logit"]), reverse=True)
            layers[layer] = {
                "available": True, "probability": round(probability, 4), "score": round(100 * probability, 1),
                "weight": round(weight, 4), "contribution": round(weight * logit, 4),
                "top_features": [{**item, "logit": round(item["logit"], 4)} for item in ranked[:4]],
            }
            contributions += [{"layer": layer, "feature": item["feature"], "value": item["value"],
                               "contribution": round(weight * item["logit"], 4)} for item in terms]
        probability = sigmoid(total)
        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        return {"probability": round(probability, 4), "score": round(100 * probability, 1), "layers": layers,
                "top_contributions": [item for item in contributions if abs(item["contribution"]) >= 0.05][:8]}
