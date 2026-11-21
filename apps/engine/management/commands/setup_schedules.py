"""Register the per-minute scheduler tick in django-q2."""
from django.core.management.base import BaseCommand
from django_q.models import Schedule


class Command(BaseCommand):
    help = "Create or update the per-minute Dayleey scheduler tick in django-q2."

    def handle(self, *args, **options):
        schedule, created = Schedule.objects.update_or_create(
            name="dayleey_per_minute_tick",
            defaults={
                "func": "apps.engine.scheduler.tick",
                "schedule_type": Schedule.MINUTES,
                "minutes": 1,
                "repeats": -1,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} django-q2 schedule: {schedule.name}"))
