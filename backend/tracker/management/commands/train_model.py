from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracker.api import EVENT_FIELDS, network_records
from tracker.models import Session


def labelled_dataset():
    """Recompute current-version features for every completed, labelled, sufficiently active session."""
    from ml.features import extract_features
    from ml.scoring import is_ready, score_features

    dataset, skipped = [], 0
    for session in Session.objects.filter(label__in=["human", "bot"], state="complete").iterator():
        features = extract_features(list(session.events.values(*EVENT_FIELDS)), session.fingerprint,
                                    network_records(session))
        if not is_ready(features):
            skipped += 1
            continue
        group = session.label_group or (session.label_source if session.label == "bot" else "") or f"session:{session.id}"
        dataset.append({"session_id": session.id, "label": 1 if session.label == "bot" else 0, "group": group,
                        "features": features, "rule_score": score_features(features)["rule_score"]})
    return dataset, skipped


class Command(BaseCommand):
    help = "Train BotBiometricNet on labelled sessions, evaluate it, and write the model and its report."

    def add_arguments(self, parser):
        parser.add_argument("--min-humans", type=int, default=8)
        parser.add_argument("--min-bots", type=int, default=8)
        parser.add_argument("--output", default=str(settings.REPO_DIR / "ml" / "models" / "botbiometricnet.json"))
        parser.add_argument("--report", default=str(settings.REPO_DIR / "docs" / "model-report.md"))
        parser.add_argument("--seed", type=int, default=0)

    def handle(self, *args, **options):
        from ml.model import BotBiometricNet
        from ml.training import report_markdown, train_and_evaluate

        dataset, skipped = labelled_dataset()
        humans = sum(item["label"] == 0 for item in dataset)
        bots = len(dataset) - humans
        self.stdout.write(f"Usable labelled sessions: {humans} human, {bots} bot ({skipped} skipped as insufficient).")
        if humans < options["min_humans"] or bots < options["min_bots"]:
            raise CommandError(
                f"Need at least {options['min_humans']} human and {options['min_bots']} bot sessions. "
                "Record and label genuine human sessions in the lab, and run scripts/bot_fleet.mjs for bots.")
        spec, _ = train_and_evaluate(dataset, seed=options["seed"])
        BotBiometricNet(spec).save(options["output"])
        report_path = Path(options["report"])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_markdown(spec), encoding="utf-8")
        known = spec["evaluation"]["known_frameworks"]
        if known:
            metrics = known["at_operating_threshold"]
            split = ("Grouped CV" if known.get("human_split") != "session" else
                     "Session-level CV (one participant, so false positives are optimistic)")
            self.stdout.write(f"{split}: AUC {metrics['auc']}, detection {metrics['detection_rate']}, "
                              f"false positives {metrics['false_positive_rate']} at threshold {known['operating_threshold']}.")
        else:
            self.stdout.write("Too few groups for cross-validation; the model is trained but not evaluated.")
        self.stdout.write(f"Saved {spec['model_version']} to {options['output']} and the report to {report_path}.")
        self.stdout.write("Run `manage.py rescore` so stored assessments use the new model.")
