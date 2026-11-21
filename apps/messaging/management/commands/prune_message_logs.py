"""Redact old MessageLog payloads while retaining billing/cost metadata."""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.messaging.models import MessageLog


class Command(BaseCommand):
    help = "Redact MessageLog payloads older than N days while preserving billing fields."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=60,
            help="Redact payloads for logs older than this many days (default: 60).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many logs would be redacted without modifying the database.",
        )

    def handle(self, *args, **options):
        days = max(0, int(options["days"]))
        cutoff = timezone.now() - timedelta(days=days)
        candidates = (
            MessageLog.objects.filter(created_at__lt=cutoff)
            .exclude(payload={"redacted": True})
            .values_list("pk", flat=True)
        )
        target_ids = list(candidates)
        count = len(target_ids)

        if options["dry_run"]:
            self.stdout.write(
                f"[DRY RUN] Would redact {count} MessageLog payload(s) older than {days} day(s)."
            )
            return

        if count:
            MessageLog.objects.filter(pk__in=target_ids).update(payload={"redacted": True})

        self.stdout.write(
            self.style.SUCCESS(
                f"Redacted {count} MessageLog payload(s) older than {days} day(s)."
            )
        )
