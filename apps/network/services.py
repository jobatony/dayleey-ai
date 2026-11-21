from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Person
from apps.network.models import Pairing, _generate_token

JOIN_TOKEN_RE = re.compile(r"JOIN[-:\s]([A-Za-z0-9]{6,24})", re.IGNORECASE)


def extract_join_token(text: str) -> str | None:
    match = JOIN_TOKEN_RE.search(text or "")
    return match.group(1).upper() if match else None


def create_or_refresh_invite(user: Person) -> Pairing:
    pairing, created = Pairing.objects.get_or_create(
        user=user,
        defaults={
            "status": Pairing.Status.INVITED,
            "mode": Pairing.Mode.PARTNER,
            "invite_token": _generate_token(),
        },
    )
    if not created:
        pairing.partner = None
        pairing.status = Pairing.Status.INVITED
        pairing.mode = Pairing.Mode.PARTNER
        pairing.mutual = False
        pairing.invite_token = _generate_token()
        pairing.invited_at = timezone.now()
        pairing.joined_at = None
        pairing.fallback_notified_at = None
        pairing.save()
    return pairing


def enable_self_report(user: Person) -> Pairing:
    pairing, _ = Pairing.objects.get_or_create(user=user)
    pairing.partner = None
    pairing.mode = Pairing.Mode.SELF_REPORT
    if pairing.status == Pairing.Status.JOINED:
        pairing.status = Pairing.Status.REMOVED
    pairing.mutual = False
    pairing.save()
    return pairing


def build_wa_me_link(user: Person, token: str) -> str:
    bot_phone = settings.WA_BOT_PHONE_NUMBER.lstrip("+")
    bot_name = settings.BOT_DISPLAY_NAME
    prefilled = (
        f"Hi {bot_name}, I'm joining as {user.display_name}'s accountability partner [JOIN-{token}]"
    )
    return f"https://wa.me/{bot_phone}?text={quote(prefilled)}"


def lookup_invite(token: str) -> Pairing | None:
    return Pairing.objects.select_related("user", "partner").filter(invite_token=token.upper()).first()


def accept_invite(partner: Person, pairing: Pairing) -> tuple[bool, str]:
    if pairing.user_id == partner.id:
        return False, "You cannot be your own accountability partner. Share the invite link with a friend!"

    pairing.partner = partner
    pairing.status = Pairing.Status.JOINED
    pairing.mode = Pairing.Mode.PARTNER
    pairing.joined_at = timezone.now()

    # Detect if mutual pairing already exists in the opposite direction
    reverse = Pairing.objects.filter(
        user=partner, partner=pairing.user, status=Pairing.Status.JOINED
    ).first()
    if reverse:
        pairing.mutual = True
        reverse.mutual = True
        reverse.save(update_fields=["mutual", "updated_at"])

    pairing.save()

    partner.opted_in = True
    partner.opted_out = False
    if partner.consent_partner_sharing or partner.role == Person.Role.BOTH:
        partner.role = Person.Role.BOTH
    else:
        partner.role = Person.Role.PARTNER
    partner.save(update_fields=["opted_in", "opted_out", "role", "updated_at"])
    return True, "accepted"


def decline_invite(partner: Person, pairing: Pairing) -> None:
    pairing.partner = None
    pairing.status = Pairing.Status.DECLINED
    pairing.mutual = False
    pairing.save(update_fields=["partner", "status", "mutual", "updated_at"])


def remove_pairings_for_partner(partner: Person) -> list[Person]:
    """Remove partner from all users they are paired with; return affected users."""
    pairings = list(
        Pairing.objects.select_related("user").filter(partner=partner, status=Pairing.Status.JOINED)
    )
    affected_users: list[Person] = []
    for pairing in pairings:
        pairing.partner = None
        pairing.status = Pairing.Status.REMOVED
        pairing.mode = Pairing.Mode.SELF_REPORT
        pairing.mutual = False
        pairing.save(update_fields=["partner", "status", "mode", "mutual", "updated_at"])
        affected_users.append(pairing.user)
    return affected_users


def get_due_self_report_fallbacks(now=None) -> list[Pairing]:
    ref = now or timezone.now()
    cutoff = ref - timedelta(days=settings.PARTNER_FALLBACK_DAYS)
    return list(
        Pairing.objects.select_related("user").filter(
            mode=Pairing.Mode.PARTNER,
            status=Pairing.Status.INVITED,
            partner__isnull=True,
            invited_at__lte=cutoff,
            fallback_notified_at__isnull=True,
            user__opted_in=True,
            user__opted_out=False,
        )
    )
