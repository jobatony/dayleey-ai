from __future__ import annotations

from django.db import models

from apps.accounts.models import Person
from apps.quarters.rules import QUARTER_HOURS, QUARTER_LABELS


class QuarterConfig(models.Model):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="quarter_configs")
    quarter = models.PositiveSmallIntegerField(choices=[(q, QUARTER_LABELS[q]) for q in (1, 2, 3, 4)])
    active = models.BooleanField(default=True)
    start_hour = models.PositiveSmallIntegerField(default=6)
    end_hour = models.PositiveSmallIntegerField(default=12)

    class Meta:
        unique_together = ("person", "quarter")
        ordering = ["quarter"]

    def save(self, *args, **kwargs):
        start_h, end_h = QUARTER_HOURS[self.quarter]
        self.start_hour = start_h
        self.end_hour = end_h
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        state = "ON" if self.active else "OFF"
        return f"{self.person.display_name} Q{self.quarter} ({state})"


class Priority(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        DONE = "done", "Done"
        PARTIAL = "partial", "Partial"
        MISSED = "missed", "Missed"
        UNCONFIRMED = "unconfirmed", "Unconfirmed"

    class MissedReason(models.TextChoices):
        FORGOT = "forgot", "Forgot"
        TIME = "time", "Ran out of time"
        EMERGENCY = "emergency", "Emergency / unexpected"
        OTHER = "other", "Other"

    owner = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="priorities")
    date = models.DateField(db_index=True)
    quarter = models.PositiveSmallIntegerField(choices=[(q, QUARTER_LABELS[q]) for q in (1, 2, 3, 4)])
    position = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    missed_reason = models.CharField(max_length=24, choices=MissedReason.choices, blank=True, default="")

    checkin_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "quarter", "position"]
        unique_together = ("owner", "date", "quarter", "position")

    def __str__(self) -> str:
        return f"{self.owner.display_name} {self.date} Q{self.quarter}#{self.position}: {self.title} [{self.status}]"


class Checkin(models.Model):
    class Kind(models.TextChoices):
        BOUNDARY = "boundary", "Boundary"
        WRAPUP = "wrapup", "Daily Wrap-up"

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="checkins")
    date = models.DateField(db_index=True)
    quarter = models.PositiveSmallIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    sent_at = models.DateTimeField()
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    response = models.CharField(max_length=64, blank=True, default="")
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self) -> str:
        return f"{self.person.display_name} {self.date} {self.kind} Q{self.quarter or '-'}"
