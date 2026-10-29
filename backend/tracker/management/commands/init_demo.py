import secrets

from django.conf import settings
from django.core.management.base import BaseCommand


KEYS = {".analyst-key": "analyst key", ".edge-key": "TLS edge signing key"}


class Command(BaseCommand):
    help = "Create local analyst and edge keys without overwriting existing keys."

    def handle(self, *args, **options):
        for file_name, description in KEYS.items():
            path = settings.BASE_DIR / file_name
            if path.exists():
                self.stdout.write(f"Local {description} already exists.")
                continue
            with path.open("x", encoding="ascii") as output:
                output.write(secrets.token_urlsafe(32) + "\n")
            self.stdout.write(f"Created backend/{file_name}. Restart the API if it is already running.")
