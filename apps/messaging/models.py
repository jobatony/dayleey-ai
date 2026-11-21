from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.accounts.models import Person


class MessageLog(models.Model):
    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class MsgType(models.TextChoices):
        TEXT = "text", "Text"
        BUTTONS = "buttons", "Buttons"
        LIST = "list", "List"
        TEMPLATE = "template", "Template"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RECEIVED = "received", "Received"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"
        BLOCKED = "blocked", "Blocked"

    person = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="message_logs"
    )
    phone = models.CharField(max_length=24, db_index=True)
    direction = models.CharField(max_length=16, choices=Direction.choices, db_index=True)
    wa_message_id = models.CharField(max_length=128, unique=True, null=True, blank=True)
    msg_type = models.CharField(max_length=16, choices=MsgType.choices, default=MsgType.TEXT)
    template_name = models.CharField(max_length=80, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    error_code = models.CharField(max_length=32, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    billable = models.BooleanField(default=False)
    est_cost = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    delivery_counted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.direction}] {self.phone} ({self.msg_type}/{self.status})"


class UsageCounter(models.Model):
    """Tracks monthly delivered messages and estimated spend.

    person=None represents the global business phone number counter.
    """

    month = models.CharField(max_length=7, db_index=True)  # YYYY-MM
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, null=True, blank=True, related_name="usage_counters"
    )
    delivered_service = models.PositiveIntegerField(default=0)
    delivered_template = models.PositiveIntegerField(default=0)
    sent_total = models.PositiveIntegerField(default=0)
    est_cost = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("month", "person")
        ordering = ["-month"]

    @property
    def delivered_total(self) -> int:
        return self.delivered_service + self.delivered_template

    def __str__(self) -> str:
        scope = self.person.display_name if self.person else "GLOBAL"
        return f"{self.month} {scope}: {self.delivered_total} delivered (${self.est_cost:.4f})"


class ScheduledEvent(models.Model):
    """Idempotency ledger for scheduled outbound events."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    event_key = models.CharField(max_length=128, unique=True, db_index=True)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="scheduled_events")
    event_type = models.CharField(max_length=48, db_index=True)
    local_date = models.DateField(db_index=True)
    quarter = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SENT)
    detail = models.CharField(max_length=255, blank=True, default="")
    executed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-executed_at"]

    def __str__(self) -> str:
        return f"{self.event_key} ({self.status})"
