from __future__ import annotations

from django.db import models

from apps.accounts.models import Person


class DailyReport(models.Model):
    class PartnerReply(models.TextChoices):
        NONE = "none", "None"
        ACK = "ack", "Acknowledged / Cheer"
        SEEN = "seen", "Seen"
        NUDGE = "nudge", "Nudge"

    user = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="daily_reports")
    partner = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_daily_reports",
    )
    date = models.DateField(db_index=True)
    summary = models.JSONField(default=dict)
    sent_at = models.DateTimeField(null=True, blank=True)
    partner_reply = models.CharField(
        max_length=16, choices=PartnerReply.choices, default=PartnerReply.NONE
    )
    partner_replied_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"Report {self.user.display_name} ({self.date}) [{self.partner_reply}]"
