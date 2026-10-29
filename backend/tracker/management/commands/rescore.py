from django.core.management.base import BaseCommand
from django.db import transaction

from tracker.api import EVENT_FIELDS, network_records
from tracker.models import Session


class Command(BaseCommand):
    help = "Recompute assessments of completed sessions with the current features and model."

    def handle(self, *args, **options):
        from ml.inference import assess, current_model_version

        count = 0
        for session_id in Session.objects.filter(state="complete").values_list("id", flat=True):
            with transaction.atomic():
                session = Session.objects.select_for_update().get(pk=session_id)
                events = list(session.events.values(*EVENT_FIELDS))
                session.assessment = assess(events, session.fingerprint, network_records(session), session.task)
                session.save(update_fields=["assessment"])
            count += 1
        self.stdout.write(f"Rescored {count} completed sessions with {current_model_version()}.")
