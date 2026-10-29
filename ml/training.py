"""Training and evaluation for BotBiometricNet (requires scikit-learn; inference does not)."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math

import numpy as np

from .features import FEATURE_VERSION
from .model import ARTIFACT_SCHEMA, BotBiometricNet
from .vectorize import LAYERS, column_names, layer_available, vectorize


LAYER_C = 0.5
FUSION_C = 1.0
TARGET_FPR = 0.02


def wilson(successes, total, z=1.96):
    """95% Wilson score interval, or None without observations."""
    if not total:
        return None
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4)]


def _fit_layer(rows, labels, layer, seed):
    from sklearn.linear_model import LogisticRegression

    names = column_names(layer)
    if len(rows) < 4 or len(set(labels)) < 2:
        return {"trained": False, "features": names, "reason": "needs both classes with this layer available"}
    matrix = np.array([[np.nan if value is None else value for value in row] for row in rows], dtype=float)
    impute = np.nanmedian(np.where(np.isnan(matrix).all(axis=0), 0.0, matrix), axis=0)
    impute = np.where(np.isnan(impute), 0.0, impute)
    filled = np.where(np.isnan(matrix), impute, matrix)
    mean = filled.mean(axis=0)
    scale = filled.std(axis=0)
    scale = np.where(scale < 1e-9, 1.0, scale)
    model = LogisticRegression(C=LAYER_C, class_weight="balanced", max_iter=5000, random_state=seed)
    model.fit((filled - mean) / scale, labels)
    return {"trained": True, "features": names, "impute": impute.round(6).tolist(), "mean": mean.round(6).tolist(),
            "scale": scale.round(6).tolist(), "coef": model.coef_[0].round(6).tolist(),
            "intercept": round(float(model.intercept_[0]), 6), "training_rows": len(rows),
            "class_counts": {"human": int(labels.count(0)), "bot": int(labels.count(1))}}


def _fit_layers(dataset, seed):
    layers = {}
    for layer in LAYERS:
        usable = [item for item in dataset if layer_available(item["features"], layer)]
        rows = [vectorize(item["features"], layer)[0] for item in usable]
        layers[layer] = _fit_layer(rows, [item["label"] for item in usable], layer, seed)
    return layers


def _layer_logits(layers, features):
    stub = BotBiometricNet({"schema": ARTIFACT_SCHEMA, "model_version": "fold", "feature_version": FEATURE_VERSION,
                            "layers": layers, "fusion": {"intercept": 0, "weights": {}, "availability": {}}})
    return {layer: stub.layer_logit(layer, features)[0] for layer in LAYERS}


def _folds(dataset, splits, seed):
    from sklearn.model_selection import StratifiedGroupKFold

    labels = [item["label"] for item in dataset]
    groups = [item["group"] for item in dataset]
    per_class = min(len({group for group, label in zip(groups, labels) if label == value}) for value in (0, 1))
    splits = min(splits, per_class)
    if splits < 2:
        return None
    splitter = StratifiedGroupKFold(n_splits=splits, shuffle=True, random_state=seed)
    return list(splitter.split(np.zeros(len(dataset)), labels, groups))


def _fusion_rows(logits):
    return [[logits[layer] if logits[layer] is not None else 0.0 for layer in LAYERS]
            + [1.0 if logits[layer] is not None else 0.0 for layer in LAYERS]]


def fit(dataset, seed=0, inner_splits=3):
    """Fit per-layer models on everything and the fusion layer on out-of-fold layer logits."""
    from sklearn.linear_model import LogisticRegression

    labels = [item["label"] for item in dataset]
    layers = _fit_layers(dataset, seed)
    stacked = [None] * len(dataset)
    folds = _folds(dataset, inner_splits, seed)
    fusion_source = "out-of-fold"
    if folds:
        for train_index, test_index in folds:
            fold_layers = _fit_layers([dataset[index] for index in train_index], seed)
            for index in test_index:
                stacked[index] = _fusion_rows(_layer_logits(fold_layers, dataset[index]["features"]))[0]
    else:
        fusion_source = "in-sample (too few groups for stacking folds)"
        stacked = [_fusion_rows(_layer_logits(layers, item["features"]))[0] for item in dataset]
    fusion_model = LogisticRegression(C=FUSION_C, class_weight="balanced", max_iter=5000, random_state=seed)
    fusion_model.fit(np.array(stacked), labels)
    coef = fusion_model.coef_[0]
    return {
        "schema": ARTIFACT_SCHEMA, "feature_version": FEATURE_VERSION, "layers": layers,
        "fusion": {"weights": {layer: round(float(coef[index]), 6) for index, layer in enumerate(LAYERS)},
                   "availability": {layer: round(float(coef[len(LAYERS) + index]), 6) for index, layer in enumerate(LAYERS)},
                   "intercept": round(float(fusion_model.intercept_[0]), 6), "source": fusion_source},
    }


def _predict(spec, item):
    return BotBiometricNet({**spec, "model_version": "evaluation"}).predict(item["features"])


def _metrics(labels, probabilities, threshold):
    from sklearn.metrics import roc_auc_score

    predicted = [probability >= threshold for probability in probabilities]
    tp = sum(1 for label, guess in zip(labels, predicted) if label == 1 and guess)
    fn = sum(1 for label, guess in zip(labels, predicted) if label == 1 and not guess)
    fp = sum(1 for label, guess in zip(labels, predicted) if label == 0 and guess)
    tn = sum(1 for label, guess in zip(labels, predicted) if label == 0 and not guess)
    ratio = lambda numerator, denominator: round(numerator / denominator, 4) if denominator else None
    return {
        "threshold": round(threshold, 4), "confusion_matrix": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "detection_rate": ratio(tp, tp + fn), "detection_rate_ci95": wilson(tp, tp + fn),
        "false_positive_rate": ratio(fp, fp + tn), "false_positive_rate_ci95": wilson(fp, fp + tn),
        "precision": ratio(tp, tp + fp), "accuracy": ratio(tp + tn, len(labels)),
        "auc": round(float(roc_auc_score(labels, probabilities)), 4) if len(set(labels)) == 2 else None,
        "brier": round(float(np.mean([(p - label) ** 2 for p, label in zip(probabilities, labels)])), 4),
    }


def operating_threshold(labels, probabilities, target_fpr=TARGET_FPR):
    """Lowest threshold whose false-positive rate stays within the target, never below 0.5."""
    humans = sorted((p for p, label in zip(probabilities, labels) if label == 0), reverse=True)
    if len(humans) < 20:
        return 0.5
    allowed = math.floor(target_fpr * len(humans))
    return round(max(0.5, humans[allowed] + 1e-4) if allowed < len(humans) else 0.5, 4)


def _calibration(labels, probabilities, bins=5):
    rows = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [(p, label) for p, label in zip(probabilities, labels)
                   if low <= p < high or (index == bins - 1 and p == 1.0)]
        if members:
            rows.append({"bin": f"{low:.1f}-{high:.1f}", "count": len(members),
                         "mean_predicted": round(float(np.mean([p for p, _ in members])), 4),
                         "observed_bot_rate": round(float(np.mean([label for _, label in members])), 4)})
    return rows


def known_framework_cv(dataset, seed=0, splits=5):
    """Humans grouped by participant; bot sessions individually, so every family is seen in training."""
    regrouped = [{**item, "group": item["group"] if item["label"] == 0 else f"session:{item['session_id']}"}
                 for item in dataset]
    folds = _folds(regrouped, splits, seed)
    if not folds:
        return None
    probabilities = [None] * len(dataset)
    layer_probabilities = {layer: [None] * len(dataset) for layer in LAYERS}
    rule_scores = [item.get("rule_score") for item in dataset]
    for train_index, test_index in folds:
        spec = fit([regrouped[index] for index in train_index], seed)
        for index in test_index:
            prediction = _predict(spec, dataset[index])
            probabilities[index] = prediction["probability"]
            for layer in LAYERS:
                layer_probabilities[layer][index] = prediction["layers"][layer]["probability"]
    labels = [item["label"] for item in dataset]
    threshold = operating_threshold(labels, probabilities)
    ablation = {}
    for layer in LAYERS:
        pairs = [(label, p) for label, p in zip(labels, layer_probabilities[layer]) if p is not None]
        if len({label for label, _ in pairs}) == 2:
            ablation[layer] = {"sessions": len(pairs), **_metrics([l for l, _ in pairs], [p for _, p in pairs], 0.5)}
    rules = [(label, score / 100) for label, score in zip(labels, rule_scores) if score is not None]
    families = defaultdict(list)
    for item, probability in zip(dataset, probabilities):
        if item["label"] == 1:
            families[item["group"]].append(probability >= threshold)
    return {
        "folds": len(folds), "operating_threshold": threshold,
        "family_detection": {family: {"sessions": len(hits), "detected": sum(hits),
                                      "detection_rate": round(sum(hits) / len(hits), 4)}
                             for family, hits in sorted(families.items())},
        "at_operating_threshold": _metrics(labels, probabilities, threshold),
        "at_0_5": _metrics(labels, probabilities, 0.5), "calibration": _calibration(labels, probabilities),
        "layers_alone": ablation,
        "rules_baseline_at_60": _metrics([l for l, _ in rules], [s for _, s in rules], 0.6) if rules else None,
        "predictions": [{"session_id": item["session_id"], "label": item["label"], "group": item["group"],
                         "probability": p} for item, p in zip(dataset, probabilities)],
    }


def unseen_framework_eval(dataset, threshold, seed=0):
    """Leave one automation family out of training and measure detection on it."""
    families = sorted({item["group"] for item in dataset if item["label"] == 1})
    humans = [item for item in dataset if item["label"] == 0]
    human_groups = sorted({item["group"] for item in humans})
    results = {}
    held_out_humans = defaultdict(list)
    for index, family in enumerate(families):
        human_group = human_groups[index % len(human_groups)] if len(human_groups) > 1 else None
        train = [item for item in dataset if item["group"] != family and (human_group is None or item["group"] != human_group)]
        if len({item["label"] for item in train}) < 2 or not any(item["label"] == 1 for item in train):
            results[family] = {"sessions": sum(item["group"] == family for item in dataset),
                               "detection_rate": None, "note": "no other bot family to train on"}
            continue
        spec = fit(train, seed)
        tested = [item for item in dataset if item["group"] == family]
        detected = sum(_predict(spec, item)["probability"] >= threshold for item in tested)
        results[family] = {"sessions": len(tested), "detected": detected,
                           "detection_rate": round(detected / len(tested), 4),
                           "detection_rate_ci95": wilson(detected, len(tested))}
        if human_group:
            for item in humans:
                if item["group"] == human_group:
                    held_out_humans[item["session_id"]].append(_predict(spec, item)["probability"] >= threshold)
    flagged = sum(any(values) for values in held_out_humans.values())
    return {"families": results, "threshold": threshold,
            "held_out_human_sessions": len(held_out_humans), "held_out_human_false_positives": flagged,
            "held_out_human_fpr_ci95": wilson(flagged, len(held_out_humans))}


def dataset_hash(dataset):
    digest = hashlib.sha256()
    for item in sorted(dataset, key=lambda entry: entry["session_id"]):
        digest.update(f"{item['session_id']}:{item['label']}:{item['group']}\n".encode())
    return digest.hexdigest()


def train_and_evaluate(dataset, seed=0):
    """Return a deployable artifact with its evaluation embedded."""
    labels = Counter(item["label"] for item in dataset)
    known = known_framework_cv(dataset, seed)
    if known:
        known["human_split"] = "participant"
    elif len({item["group"] for item in dataset if item["label"] == 0}) == 1:
        # One volunteer cannot be held out by participant, so hold out their sessions instead and flag it.
        known = known_framework_cv([{**item, "group": f"session:{item['session_id']}"} if item["label"] == 0 else item
                                    for item in dataset], seed)
        if known:
            known["human_split"] = "session"
    threshold = known["operating_threshold"] if known else 0.5
    unseen = unseen_framework_eval(dataset, threshold, seed) if known else None
    spec = fit(dataset, seed)
    created = datetime.now(timezone.utc)
    digest = dataset_hash(dataset)
    spec.update({
        "model_version": f"botbiometricnet-v1-{created:%Y%m%d%H%M}-{digest[:8]}",
        "created_at": created.isoformat(), "operating_threshold": threshold,
        "training": {
            "sessions": len(dataset), "humans": labels[0], "bots": labels[1], "dataset_sha256": digest,
            "groups": dict(sorted(Counter(f"{'bot' if item['label'] else 'human'}:{item['group']}" for item in dataset).items())),
        },
        "evaluation": {"known_frameworks": {key: value for key, value in (known or {}).items() if key != "predictions"} or None,
                       "unseen_frameworks": unseen},
    })
    BotBiometricNet(spec)
    return spec, (known or {}).get("predictions", [])


def report_markdown(spec):
    """Human-readable model report for docs/model-report.md."""
    training, evaluation = spec["training"], spec["evaluation"]
    known, unseen = evaluation["known_frameworks"], evaluation["unseen_frameworks"]
    percent = lambda value: "n/a" if value is None else f"{100 * value:.1f}%"
    interval = lambda pair: "" if not pair else f" (95% CI {100 * pair[0]:.0f}-{100 * pair[1]:.0f}%)"
    lines = [
        "# BotBiometricNet model report", "",
        f"Model `{spec['model_version']}`, features `{spec['feature_version']}`, trained {spec['created_at'][:16]} UTC.",
        f"Dataset: {training['sessions']} labelled sessions ({training['humans']} human, {training['bots']} bot), "
        f"SHA-256 `{training['dataset_sha256'][:16]}`.", "",
        "| Group | Sessions |", "| --- | --- |",
        *[f"| {name} | {count} |" for name, count in training["groups"].items()], "",
        "## Architecture", "",
        "Four per-layer logistic models (browser, mouse, timing, network) produce layer logits. A stacked logistic "
        "fusion layer, trained on out-of-fold layer logits, weighs the available layers. Missing layers contribute "
        "nothing instead of counting as evidence.", "",
        "| Layer | Fusion weight | Availability offset | Training rows |", "| --- | --- | --- | --- |",
    ]
    for layer in LAYERS:
        layer_spec = spec["layers"][layer]
        lines.append(f"| {layer} | {spec['fusion']['weights'][layer]:.3f} | {spec['fusion']['availability'][layer]:.3f} | "
                     f"{layer_spec.get('training_rows', 0) if layer_spec.get('trained') else 'not trained'} |")
    if known:
        operating = known["at_operating_threshold"]
        by_session = known.get("human_split") == "session"
        lines += [
            "", f"## Known frameworks ({'session-level' if by_session else 'grouped'} cross-validation)", "",
            (f"{known['folds']} folds. **Only one participant is labelled**, so human sessions are held out one at a "
             "time: the same person and device appear in training and test folds, and the human false-positive rate "
             "is optimistic until more participants are labelled. Bot families appear on both sides." if by_session else
             f"{known['folds']} folds; human sessions grouped by participant, so no participant appears in both "
             "training and test folds. Bot families appear on both sides."), "",
            f"- ROC AUC: {operating['auc']}",
            f"- Operating threshold: {known['operating_threshold']} (target false-positive rate {TARGET_FPR:.0%})",
            f"- Bot detection rate: {percent(operating['detection_rate'])}{interval(operating['detection_rate_ci95'])}",
            f"- Human false-positive rate: {percent(operating['false_positive_rate'])}{interval(operating['false_positive_rate_ci95'])}",
            f"- Brier score: {operating['brier']}", "",
            "| Evidence | Sessions | AUC | Detection @0.5 | FPR @0.5 |", "| --- | --- | --- | --- | --- |",
        ]
        for layer, metrics in known["layers_alone"].items():
            lines.append(f"| {layer} layer alone | {metrics['sessions']} | {metrics['auc']} | "
                         f"{percent(metrics['detection_rate'])} | {percent(metrics['false_positive_rate'])} |")
        rules = known.get("rules_baseline_at_60")
        if rules:
            lines.append(f"| rules-v2 baseline (score >= 60) | {sum(rules['confusion_matrix'].values())} | {rules['auc']} | "
                         f"{percent(rules['detection_rate'])} | {percent(rules['false_positive_rate'])} |")
        lines.append(f"| **BotBiometricNet fused (@0.5)** | {training['sessions']} | {known['at_0_5']['auc']} | "
                     f"{percent(known['at_0_5']['detection_rate'])} | {percent(known['at_0_5']['false_positive_rate'])} |")
    if unseen:
        known_families = (known or {}).get("family_detection", {})
        lines += ["", "## Unseen frameworks and retraining recovery", "",
                  "Each automation family is first removed from training and tested as an unseen framework (an "
                  "attacker the model has never seen). The second column is the same family's detection once it "
                  "is included in training (the cross-validation above): the recovery after retraining.", "",
                  "| Family | Sessions | Detected while unseen | Detected after retraining |", "| --- | --- | --- | --- |"]
        for family, result in unseen["families"].items():
            rate = result.get("detection_rate")
            unseen_text = (f"{percent(rate)}{interval(result.get('detection_rate_ci95'))}" if rate is not None
                           else f"n/a ({result.get('note', '')})")
            lines.append(f"| {family} | {result['sessions']} | {unseen_text} | "
                         f"{percent(known_families.get(family, {}).get('detection_rate'))} |")
        if unseen["held_out_human_sessions"]:
            lines.append(f"\nHeld-out human sessions flagged: {unseen['held_out_human_false_positives']} of "
                         f"{unseen['held_out_human_sessions']}{interval(unseen['held_out_human_fpr_ci95'])}.")
    lines += ["", "## Limitations", "",
              "- A pilot dataset: small samples give wide confidence intervals, shown above.",
              "- Bot sessions come from local automation profiles; commercial anti-detect browsers were not available.",
              "- Human sessions come from a small set of volunteers and devices, so they may not represent all users.",
              "- Scores are logistic outputs trained with balanced class weights, not calibrated population probabilities."]
    return "\n".join(lines) + "\n"


def dumps(spec):
    return json.dumps(spec, indent=1, sort_keys=True)
