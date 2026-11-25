"""Check DB connectivity and django-q2 worker heartbeat (for Docker HEALTHCHECK)."""
from django.core.management.base import BaseCommand, CommandError

from apps.engine.health import collect_system_health


class Command(BaseCommand):
    help = "Verify database and django-q2 worker heartbeat; exits non-zero if unhealthy."

    def handle(self, *args, **options):
        health = collect_system_health()
        if not health["db_ok"] or not health["worker_ok"]:
            raise CommandError(
                f"Worker unhealthy (db_ok={health['db_ok']}, worker_ok={health['worker_ok']}, "
                f"mode={health['worker_mode']}, last_tick_age={health['last_tick_age_seconds']}s)"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Worker healthy (mode={health['worker_mode']}, "
                f"last_tick_age={health['last_tick_age_seconds']}s, queue={health['queue_depth']})"
            )
        )
