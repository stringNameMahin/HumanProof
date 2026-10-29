from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tracker.models import LabelRevision, Session
from tracker.serializers import LabelSerializer


class Command(BaseCommand):
    help = ("Label the most recent completed, still-unlabelled sessions. Intended for a participant who has just "
            "recorded their own sessions, for example: label_recent --count 10 --label human "
            "--group participant-mp --source \"team member, desktop mouse\"")

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, required=True, help="How many recent sessions to label (1-50).")
        parser.add_argument("--label", choices=["human", "bot"], required=True)
        parser.add_argument("--source", required=True, help="How the label was established.")
        parser.add_argument("--group", default="", help="Participant or automation family, for grouped splits.")
        parser.add_argument("--task", default=None, help="Only sessions of this task (mixed-demo or sign-in).")
        parser.add_argument("--yes", action="store_true", help="Apply the labels. Without it, only list the sessions.")

    def handle(self, *args, **options):
        if not 1 <= options["count"] <= 50:
            raise CommandError("--count must be between 1 and 50.")
        serializer = LabelSerializer(data={"label": options["label"], "source": options["source"], "group": options["group"]})
        if not serializer.is_valid():
            raise CommandError(f"Invalid label: {serializer.errors}")
        data = serializer.validated_data
        sessions = Session.objects.filter(state="complete", label="unknown")
        if options["task"]:
            sessions = sessions.filter(task=options["task"])
        chosen = list(sessions.order_by("-completed_at")[:options["count"]])
        if not chosen:
            raise CommandError("No completed, unlabelled sessions were found.")
        for session in chosen:
            assessment = session.assessment or {}
            self.stdout.write(f"{session.id}  {session.task:<10}  {session.event_count:>5} events  "
                              f"{assessment.get('status', 'pending'):<17}  score {assessment.get('risk_score')}")
        if not options["yes"]:
            self.stdout.write(f"Would label these {len(chosen)} session(s) {data['label']}. Check they are yours, "
                              "then repeat the command with --yes.")
            return
        with transaction.atomic():
            for session in chosen:
                session.label, session.label_source, session.label_group = data["label"], data["source"], data["group"]
                session.save(update_fields=["label", "label_source", "label_group"])
                LabelRevision.objects.create(session=session, label=session.label, source=session.label_source,
                                             group=session.label_group)
        self.stdout.write(f"Labelled {len(chosen)} session(s) {data['label']}"
                          f"{' in group ' + data['group'] if data['group'] else ''}.")
