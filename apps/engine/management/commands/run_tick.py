"""Run one scheduler tick (or catch-up) from CLI, optionally at a simulated UTC timestamp."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from apps.engine.scheduler import catchup, tick


class Command(BaseCommand):
    help = "Run Dayleey's scheduler tick once (optionally at --at 'YYYY-MM-DD HH:MM' UTC)."

    def add_arguments(self, parser):
        parser.add_argument("--at", type=str, default=None, help="ISO datetime (assumed UTC if naive)")
        parser.add_argument("--catchup", action="store_true", help="Run 2-hour restart catchup")

    def handle(self, *args, **options):
        ref = None
        if options["at"]:
            parsed = datetime.fromisoformat(options["at"])
            ref = parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("UTC"))

        result = catchup(now=ref) if options["catchup"] else tick(now=ref)
        self.stdout.write(self.style.SUCCESS(f"Scheduler result: {result}"))
