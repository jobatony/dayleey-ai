"""Per-message budgeting, rate limiting, quiet hours, and global kill-switch."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Person
from apps.messaging.models import MessageLog, UsageCounter


class BlockedSendError(Exception):
    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        super().__init__(message or reason)


def month_key(now: datetime | None = None) -> str:
    ref = now or timezone.now()
    return ref.strftime("%Y-%m")


def get_or_create_global_counter(now: datetime | None = None) -> UsageCounter:
    mk = month_key(now)
    counter = UsageCounter.objects.filter(month=mk, person__isnull=True).first()
    if counter is None:
        counter = UsageCounter.objects.create(month=mk, person=None)
    return counter


def get_or_create_person_counter(person: Person, now: datetime | None = None) -> UsageCounter:
    mk = month_key(now)
    counter, _ = UsageCounter.objects.get_or_create(month=mk, person=person)
    return counter


def is_in_quiet_hours(person: Person, now: datetime | None = None) -> bool:
    local_dt = person.local_now(now)
    start = settings.QUIET_HOURS_START
    end = settings.QUIET_HOURS_END
    hour = local_dt.hour
    if start > end:
        # Spans midnight e.g. 22:00 -> 06:00
        return hour >= start or hour < end
    return start <= hour < end


def lookup_unit_rate(country_code: str, is_template: bool, category: str = "utility") -> Decimal:
    card = settings.RATE_CARD
    country_rates = card.get(country_code) or card.get("default") or {}
    key = category if is_template else "service"
    val = country_rates.get(key, 0.0068 if is_template else 0.0050)
    return Decimal(str(val))


def estimate_message_cost(
    person: Person,
    is_template: bool,
    now: datetime | None = None,
    category: str = "utility",
) -> tuple[bool, Decimal]:
    """Estimate (billable, cost_usd) under Oct 2026 Meta pricing.

    - Utility templates are always billable (including inside the 24h window).
    - Service (free-form) messages are free up to FREE_SERVICE_MESSAGES_PER_MONTH per phone number.
    """
    unit = lookup_unit_rate(person.country_code_hint(), is_template=is_template, category=category)
    if is_template:
        return True, unit

    global_counter = get_or_create_global_counter(now)
    if global_counter.delivered_service < settings.FREE_SERVICE_MESSAGES_PER_MONTH:
        return False, Decimal("0")
    return True, unit


def check_can_send(
    person: Person,
    *,
    is_template: bool,
    transport: str | None = None,
    bypass_opt_in: bool = False,
    allow_quiet_hours: bool = False,
    now: datetime | None = None,
) -> tuple[bool, Decimal]:
    """Enforce opt-out, global budget kill-switch, per-user monthly cap, daily rate limits, and quiet hours.

    Returns (billable, est_cost) if permitted; raises BlockedSendError otherwise.
    """
    ref = now or timezone.now()

    if getattr(settings, "PAUSE_ALL", False):
        raise BlockedSendError("pause_all", "Global PAUSE_ALL kill-switch is enabled.")

    if person.opted_out and not bypass_opt_in:
        raise BlockedSendError("opted_out", f"{person.phone} has opted out.")

    if not allow_quiet_hours and is_in_quiet_hours(person, ref):
        raise BlockedSendError("quiet_hours", f"{person.phone} is in quiet hours.")

    billable, est_cost = estimate_message_cost(person, is_template=is_template, now=ref)

    global_counter = get_or_create_global_counter(ref)
    if global_counter.est_cost + est_cost > settings.MONTHLY_BUDGET_USD:
        raise BlockedSendError(
            "global_budget_exceeded",
            f"Monthly budget ${settings.MONTHLY_BUDGET_USD} reached (${global_counter.est_cost:.4f}).",
        )

    person_counter = get_or_create_person_counter(person, ref)
    # Use max(sent_total, delivered_total) so burst sends cannot overrun the cap before delivery webhooks arrive
    effective_monthly = max(person_counter.sent_total, person_counter.delivered_total)
    if effective_monthly >= settings.USER_MONTHLY_MSG_CAP:
        raise BlockedSendError(
            "user_monthly_cap",
            f"{person.phone} reached monthly cap ({settings.USER_MONTHLY_MSG_CAP}).",
        )

    # Daily rate limit (user <= 10/day, partner-only <= 2/day) — only enforced on proactive/scheduled sends
    # Interactive replies (allow_quiet_hours=True) still respect monthly cap & global budget
    if not allow_quiet_hours:
        local_dt = person.local_now(ref)
        start_of_local_day = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = MessageLog.objects.filter(
            person=person,
            direction=MessageLog.Direction.OUTBOUND,
            status__in=[
                MessageLog.Status.SENT,
                MessageLog.Status.DELIVERED,
                MessageLog.Status.READ,
            ],
            created_at__gte=start_of_local_day,
        ).count()
        daily_limit = (
            settings.PARTNER_DAILY_MSG_LIMIT
            if person.role == Person.Role.PARTNER
            else settings.USER_DAILY_MSG_LIMIT
        )
        if sent_today >= daily_limit:
            raise BlockedSendError(
                "daily_rate_limit",
                f"{person.phone} reached daily limit ({daily_limit}/day).",
            )

    return billable, est_cost


@transaction.atomic
def record_sent(person: Person | None, now: datetime | None = None) -> None:
    ref = now or timezone.now()
    global_counter = get_or_create_global_counter(ref)
    global_counter.sent_total += 1
    global_counter.save(update_fields=["sent_total", "updated_at"])
    if person is not None:
        pc = get_or_create_person_counter(person, ref)
        pc.sent_total += 1
        pc.save(update_fields=["sent_total", "updated_at"])


@transaction.atomic
def record_delivery(log: MessageLog, now: datetime | None = None) -> None:
    """Count a delivered outbound message once (idempotent against duplicate webhooks)."""
    if log.delivery_counted or log.direction != MessageLog.Direction.OUTBOUND:
        return

    ref = now or timezone.now()
    is_template = log.msg_type == MessageLog.MsgType.TEMPLATE
    global_counter = get_or_create_global_counter(ref)

    if is_template:
        unit = lookup_unit_rate(
            log.person.country_code_hint() if log.person else "default", is_template=True
        )
        billable = True
        cost = unit
        global_counter.delivered_template += 1
    else:
        if global_counter.delivered_service < settings.FREE_SERVICE_MESSAGES_PER_MONTH:
            billable = False
            cost = Decimal("0")
        else:
            billable = True
            cost = lookup_unit_rate(
                log.person.country_code_hint() if log.person else "default", is_template=False
            )
        global_counter.delivered_service += 1

    global_counter.est_cost += cost
    global_counter.save(
        update_fields=["delivered_service", "delivered_template", "est_cost", "updated_at"]
    )

    if log.person is not None:
        pc = get_or_create_person_counter(log.person, ref)
        if is_template:
            pc.delivered_template += 1
        else:
            pc.delivered_service += 1
        pc.est_cost += cost
        pc.save(update_fields=["delivered_service", "delivered_template", "est_cost", "updated_at"])

    log.billable = billable
    log.est_cost = cost
    log.delivery_counted = True
    log.save(update_fields=["billable", "est_cost", "delivery_counted", "updated_at"])
