import secrets

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a local analyst key without overwriting an existing key."

    def handle(self, *args, **options):
        path = settings.BASE_DIR / ".analyst-key"
        if path.exists():
            self.stdout.write("Local analyst key already exists.")
            return
        with path.open("x", encoding="ascii") as output:
            output.write(secrets.token_urlsafe(32) + "\n")
        self.stdout.write("Created backend/.analyst-key. Restart the API if it is already running.")
