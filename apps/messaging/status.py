"""Processes Meta delivery status webhooks (sent, delivered, read, failed)."""
from __future__ import annotations

import logging

from apps.messaging.budget import record_delivery
from apps.messaging.client import WhatsAppClient
from apps.messaging.models import MessageLog
from apps.network.models import Pairing

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "sent": MessageLog.Status.SENT,
    "delivered": MessageLog.Status.DELIVERED,
    "read": MessageLog.Status.READ,
    "failed": MessageLog.Status.FAILED,
}


def process_status_updates(payload: dict) -> int:
    processed = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for status_obj in value.get("statuses", []):
                wa_id = status_obj.get("id")
                raw_status = status_obj.get("status", "").lower()
                if not wa_id or raw_status not in STATUS_MAP:
                    continue

                log = MessageLog.objects.select_related("person").filter(wa_message_id=wa_id).first()
                if not log:
                    continue

                new_status = STATUS_MAP[raw_status]
                log.status = new_status
                errors = status_obj.get("errors") or []
                if errors:
                    first = errors[0]
                    log.error_code = str(first.get("code", ""))[:32]
                    log.error_message = str(first.get("title") or first.get("message") or "")[:500]
                log.save(update_fields=["status", "error_code", "error_message", "updated_at"])

                if new_status in {MessageLog.Status.DELIVERED, MessageLog.Status.READ}:
                    record_delivery(log)
                elif new_status == MessageLog.Status.FAILED:
                    _handle_delivery_failure(log)

                processed += 1
    return processed


def _handle_delivery_failure(log: MessageLog) -> None:
    """If a partner daily report fails to deliver, notify the paired user so they aren't left hanging."""
    if not log.person or log.template_name != "partner_daily_report":
        return

    client = WhatsAppClient()
    pairings = Pairing.objects.select_related("user").filter(
        partner=log.person, status=Pairing.Status.JOINED
    )
    for pairing in pairings:
        logger.warning(
            "Partner report failed for %s -> notifying user %s",
            log.person.phone,
            pairing.user.phone,
        )
        client.send_text(
            pairing.user,
            f"Heads up: we couldn't deliver today's report to your partner ({log.person.display_name}). "
            "Reply PARTNER if you'd like to invite someone else or switch to self-report mode.",
            allow_quiet_hours=False,
        )
