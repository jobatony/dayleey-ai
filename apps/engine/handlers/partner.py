"""Partner join (wa.me link), acceptance, mutual pairing offer, daily report replies, and PARTNER command."""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Person
from apps.engine import states
from apps.engine.handlers.onboarding import start_onboarding
from apps.messaging.client import WhatsAppClient
from apps.network.models import Pairing
from apps.network.services import (
    accept_invite,
    build_wa_me_link,
    create_or_refresh_invite,
    decline_invite,
    enable_self_report,
    lookup_invite,
)
from apps.reports.models import DailyReport


def handle_join_token(partner: Person, token: str, client: WhatsAppClient) -> None:
    pairing = lookup_invite(token)
    if not pairing:
        client.send_text(
            partner,
            "That Dayleey invite link looks invalid or expired. Ask your friend to reply *PARTNER* for a fresh link.",
            bypass_opt_in=True,
            allow_quiet_hours=True,
        )
        return

    if pairing.user_id == partner.id:
        client.send_text(
            partner,
            "You can't be your own accountability partner! Forward that link to a friend instead.",
            allow_quiet_hours=True,
        )
        return

    partner.opted_in = True
    partner.opted_out = False
    partner.save(update_fields=["opted_in", "opted_out", "updated_at"])
    partner.set_state(states.PARTNER_PENDING_ACCEPT, {"pairing_id": pairing.id})

    client.send_buttons(
        partner,
        (
            f"Hi! *{pairing.user.display_name}* invited you to be their accountability partner on *{settings.BOT_DISPLAY_NAME}*.\n\n"
            "What you'll receive:\n"
            f"• *1 daily report* each evening showing {pairing.user.display_name}'s completed/missed priorities.\n"
            "• Quick buttons to cheer them on or nudge them.\n"
            "• Completely free for you. Reply *STOP* anytime to leave.\n\n"
            "Accept this invite?"
        ),
        [("p_accept", "Accept"), ("p_decline", "Stop / Decline")],
        allow_quiet_hours=True,
    )


def handle_partner_accept_response(partner: Person, choice: str, client: WhatsAppClient) -> None:
    pairing_id = (partner.state_data or {}).get("pairing_id")
    pairing = Pairing.objects.select_related("user").filter(id=pairing_id).first()
    if not pairing:
        partner.clear_state()
        client.send_text(partner, "That invite is no longer active.", allow_quiet_hours=True)
        return

    norm = choice.strip().lower()
    if norm in {"p_accept", "accept", "yes"}:
        ok, msg = accept_invite(partner, pairing)
        if not ok:
            partner.clear_state()
            client.send_text(partner, msg, allow_quiet_hours=True)
            return

        # Notify the user that their partner joined!
        client.send_text(
            pairing.user,
            f"🎉 *{partner.display_name}* accepted your accountability invite! "
            "They'll now receive your daily summary each evening.",
            allow_quiet_hours=False,
        )

        # If partner is not already a full user, offer mutual pairing
        if partner.role == Person.Role.PARTNER:
            partner.set_state(states.PARTNER_OFFER_MUTUAL, {"paired_user_id": pairing.user_id})
            client.send_buttons(
                partner,
                (
                    f"Awesome — you're now *{pairing.user.display_name}*'s accountability partner!\n\n"
                    "Want to track your own daily quarters too (mutual accountability)?"
                ),
                [("mutual_yes", "Yes, track mine"), ("mutual_no", "Partner only")],
                allow_quiet_hours=True,
            )
        else:
            partner.clear_state()
            client.send_text(
                partner,
                f"You're now linked as *{pairing.user.display_name}*'s accountability partner!",
                allow_quiet_hours=True,
            )
    elif norm in {"p_decline", "stop / decline", "decline", "no"}:
        decline_invite(partner, pairing)
        partner.clear_state()
        client.send_text(
            partner,
            "No worries — you've declined the invite and won't receive any updates.",
            allow_quiet_hours=True,
        )
        client.send_text(
            pairing.user,
            "Your accountability invite was declined. Reply *PARTNER* to generate a new link or switch to self-report.",
            allow_quiet_hours=False,
        )
    else:
        client.send_buttons(
            partner,
            "Tap *Accept* to become their accountability partner, or *Stop / Decline* to opt out.",
            [("p_accept", "Accept"), ("p_decline", "Stop / Decline")],
            allow_quiet_hours=True,
        )


def handle_mutual_offer(partner: Person, choice: str, client: WhatsAppClient) -> None:
    norm = choice.strip().lower()
    if norm in {"mutual_yes", "yes, track mine", "yes"}:
        partner.role = Person.Role.BOTH
        partner.save(update_fields=["role", "updated_at"])
        start_onboarding(partner, client)
    else:
        partner.clear_state()
        client.send_text(
            partner,
            "Got it — partner-only mode! You'll get one daily report each evening. Reply *STOP* anytime to opt out.",
            allow_quiet_hours=True,
        )


def handle_partner_report_reply(partner: Person, choice: str, client: WhatsAppClient) -> bool:
    """Handle partner's one-tap response to a daily report (👏 / Seen / Nudge).

    Returns True if handled as a report reply, False otherwise.
    """
    norm = choice.strip().lower()
    mapping = {
        "rep_cheer": (DailyReport.PartnerReply.ACK, "👏", "sent you a cheer for today's progress!"),
        "👏 great job": (DailyReport.PartnerReply.ACK, "👏", "sent you a cheer for today's progress!"),
        "cheer": (DailyReport.PartnerReply.ACK, "👏", "sent you a cheer for today's progress!"),
        "rep_seen": (DailyReport.PartnerReply.SEEN, "👀", "reviewed your daily report."),
        "👀 seen": (DailyReport.PartnerReply.SEEN, "👀", "reviewed your daily report."),
        "seen": (DailyReport.PartnerReply.SEEN, "👀", "reviewed your daily report."),
        "rep_nudge": (DailyReport.PartnerReply.NUDGE, "💪", "sent you a gentle nudge — let's crush tomorrow!"),
        "💪 nudge": (DailyReport.PartnerReply.NUDGE, "💪", "sent you a gentle nudge — let's crush tomorrow!"),
        "nudge": (DailyReport.PartnerReply.NUDGE, "💪", "sent you a gentle nudge — let's crush tomorrow!"),
    }

    # Support button IDs like rep_cheer:<report_id>
    report_id = None
    base_key = norm
    if ":" in norm and norm.split(":", 1)[0] in {"rep_cheer", "rep_seen", "rep_nudge"}:
        base_key, raw_id = norm.split(":", 1)
        if raw_id.isdigit():
            report_id = int(raw_id)

    if base_key not in mapping:
        return False

    reply_enum, icon, user_msg_suffix = mapping[base_key]
    qs = DailyReport.objects.select_related("user").filter(partner=partner)
    report = qs.filter(id=report_id).first() if report_id else qs.order_by("-date", "-sent_at").first()
    if not report:
        return False

    report.partner_reply = reply_enum
    report.partner_replied_at = timezone.now()
    report.save(update_fields=["partner_reply", "partner_replied_at", "updated_at"])

    client.send_text(
        partner,
        f"{icon} Thanks! I've passed your response on to *{report.user.display_name}*.",
        allow_quiet_hours=True,
    )
    client.send_text(
        report.user,
        f"{icon} *{partner.display_name}* {user_msg_suffix}",
        allow_quiet_hours=False,
    )
    return True


def handle_partner_command(person: Person, client: WhatsAppClient) -> None:
    pairing = Pairing.objects.select_related("partner").filter(user=person).first()
    if pairing and pairing.has_active_partner:
        person.set_state(states.ASK_PARTNER, {})
        client.send_buttons(
            person,
            (
                f"Your current accountability partner is *{pairing.partner.display_name}* "
                f"({'mutual pair' if pairing.mutual else 'partner'}).\n\n"
                "What would you like to do?"
            ),
            [("partner_invite", "Invite New Partner"), ("partner_skip", "Switch Self-Report")],
            allow_quiet_hours=True,
        )
    else:
        pairing = create_or_refresh_invite(person)
        link = build_wa_me_link(person, pairing.invite_token)
        person.set_state(states.ASK_PARTNER, {})
        client.send_buttons(
            person,
            (
                "You don't have an active partner linked yet.\n\n"
                f"Share this link with a friend on WhatsApp:\n{link}\n\n"
                "Or switch to self-report mode:"
            ),
            [("partner_skip", "Use Self-Report")],
            allow_quiet_hours=True,
        )
