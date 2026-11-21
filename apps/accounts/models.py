from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models
from django.utils import timezone


class Person(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        PARTNER = "partner", "Partner"
        BOTH = "both", "Both"

    class Plan(models.TextChoices):
        FREE_BETA = "free_beta", "Free Beta"
        PAID = "paid", "Paid"

    phone = models.CharField(max_length=24, unique=True, db_index=True)
    wa_jid = models.CharField(max_length=80, blank=True, default="", db_index=True)
    name = models.CharField(max_length=120, blank=True, default="")
    timezone = models.CharField(max_length=64, default=settings.DEFAULT_TIMEZONE)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.USER)
    opted_in = models.BooleanField(default=False)
    opted_out = models.BooleanField(default=False)
    consent_partner_sharing = models.BooleanField(default=False)
    paused = models.BooleanField(default=False)
    is_allowlisted = models.BooleanField(default=True)

    last_inbound_at = models.DateTimeField(null=True, blank=True)
    window_expires_at = models.DateTimeField(null=True, blank=True)

    plan = models.CharField(max_length=24, choices=Plan.choices, default=Plan.FREE_BETA)
    state = models.CharField(max_length=48, default="NEW", db_index=True)
    state_data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name or 'Unnamed'} ({self.phone})"

    @property
    def display_name(self) -> str:
        return self.name.strip() or self.phone

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo(settings.DEFAULT_TIMEZONE)

    def local_now(self, now: datetime | None = None) -> datetime:
        ref = now or timezone.now()
        if timezone.is_naive(ref):
            ref = timezone.make_aware(ref, ZoneInfo("UTC"))
        return ref.astimezone(self.tzinfo)

    def is_window_open(self, now: datetime | None = None) -> bool:
        if not self.window_expires_at:
            return False
        ref = now or timezone.now()
        return ref < self.window_expires_at

    def touch_inbound(self, now: datetime | None = None) -> None:
        ref = now or timezone.now()
        self.last_inbound_at = ref
        self.window_expires_at = ref + timezone.timedelta(hours=24)
        self.save(update_fields=["last_inbound_at", "window_expires_at", "updated_at"])

    def set_state(self, state: str, data: dict | None = None) -> None:
        self.state = state
        self.state_data = data if data is not None else {}
        self.save(update_fields=["state", "state_data", "updated_at"])

    def clear_state(self) -> None:
        self.set_state("IDLE", {})

    @property
    def is_active_user(self) -> bool:
        return self.role in {self.Role.USER, self.Role.BOTH} and self.opted_in and not self.opted_out

    def country_code_hint(self) -> str:
        digits = self.phone.lstrip("+")
        if digits.startswith("234"):
            return "NG"
        if digits.startswith("44"):
            return "GB"
        if digits.startswith("1"):
            return "US"
        if digits.startswith("91"):
            return "IN"
        if digits.startswith("55"):
            return "BR"
        return "default"

    def anonymize_and_wipe(self) -> None:
        """Delete user-identifying data and priorities for DELETE MY DATA compliance."""
        self.priorities.all().delete()
        self.checkins.all().delete()
        self.quarter_configs.all().delete()
        self.daily_reports.all().delete()
        if hasattr(self, "pairing_as_user"):
            self.pairing_as_user.delete()
        self.pairings_as_partner.update(partner=None, status="removed", mode="self_report")
        self.message_logs.update(payload={"redacted": True})
        self.name = ""
        self.wa_jid = ""
        self.opted_in = False
        self.opted_out = True
        self.consent_partner_sharing = False
        self.paused = True
        self.state = "DELETED"
        self.state_data = {}
        self.save()
