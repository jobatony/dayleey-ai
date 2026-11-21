from __future__ import annotations

import secrets

from django.db import models

from apps.accounts.models import Person


def _generate_token() -> str:
    return secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()


class Pairing(models.Model):
    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        JOINED = "joined", "Joined"
        DECLINED = "declined", "Declined"
        REMOVED = "removed", "Removed"

    class Mode(models.TextChoices):
        PARTNER = "partner", "Partner"
        SELF_REPORT = "self_report", "Self-report"

    user = models.OneToOneField(Person, on_delete=models.CASCADE, related_name="pairing_as_user")
    partner = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pairings_as_partner",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INVITED)
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.PARTNER)
    mutual = models.BooleanField(default=False)
    invite_token = models.CharField(max_length=24, unique=True, default=_generate_token, db_index=True)

    invited_at = models.DateTimeField(auto_now_add=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    fallback_notified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        partner_label = self.partner.display_name if self.partner else "Unassigned"
        return f"{self.user.display_name} -> {partner_label} ({self.mode}/{self.status})"

    @property
    def has_active_partner(self) -> bool:
        return (
            self.mode == self.Mode.PARTNER
            and self.status == self.Status.JOINED
            and self.partner is not None
            and self.partner.opted_in
            and not self.partner.opted_out
        )
