"""WhatsApp Cloud API client with budget gating, 24h window awareness, retries, and dry-run support."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Literal

import httpx
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Person
from apps.messaging.budget import BlockedSendError, check_can_send, record_delivery, record_sent
from apps.messaging.models import MessageLog
from apps.messaging.templates import build_template_payload

logger = logging.getLogger(__name__)

# In-memory sink used by local CLI simulator & tests when WA_DRY_RUN=True / WA_TRANSPORT=dry_run
_DRY_RUN_OUTBOX: list[dict] = []


def pop_dry_run_outbox() -> list[dict]:
    items = list(_DRY_RUN_OUTBOX)
    _DRY_RUN_OUTBOX.clear()
    return items


def peek_dry_run_outbox() -> list[dict]:
    return list(_DRY_RUN_OUTBOX)


def clear_dry_run_outbox() -> None:
    _DRY_RUN_OUTBOX.clear()


class WhatsAppClient:
    def __init__(self, *, dry_run: bool | None = None, transport: str | None = None):
        if transport is not None:
            self.transport = transport.strip().lower()
        elif dry_run is True:
            self.transport = "dry_run"
        elif dry_run is False:
            configured = getattr(settings, "WA_TRANSPORT", "cloud_api").strip().lower()
            self.transport = "cloud_api" if configured == "dry_run" else configured
        else:
            configured = getattr(settings, "WA_TRANSPORT", "").strip().lower()
            if configured:
                self.transport = configured
            else:
                self.transport = "dry_run" if settings.WA_DRY_RUN else "cloud_api"
        self.dry_run = self.transport == "dry_run"

    @property
    def endpoint(self) -> str:
        return (
            f"https://graph.facebook.com/{settings.WA_API_VERSION}/"
            f"{settings.WA_PHONE_NUMBER_ID}/messages"
        )

    def send(
        self,
        person: Person,
        kind: Literal["text", "buttons", "list", "template"],
        *,
        body: str = "",
        buttons: list[tuple[str, str]] | None = None,
        list_button_text: str = "Choose",
        list_rows: list[tuple[str, str, str]] | None = None,
        template_name: str = "",
        template_params: list[str] | None = None,
        fallback_template: tuple[str, list[str]] | None = None,
        bypass_opt_in: bool = False,
        allow_quiet_hours: bool = False,
        now: datetime | None = None,
    ) -> MessageLog:
        ref = now or timezone.now()
        effective_kind = kind
        effective_template = template_name
        effective_params = template_params or []

        # Enforce 24h customer-service window for non-template messages
        if effective_kind != "template" and not person.is_window_open(ref):
            if fallback_template:
                effective_kind = "template"
                effective_template, effective_params = fallback_template
            else:
                return self._log_blocked(
                    person=person,
                    msg_type=effective_kind,
                    template_name=effective_template,
                    payload={"body": body, "buttons": buttons},
                    reason="window_closed",
                    detail="24h customer-service window is closed and no fallback template was provided.",
                )

        if (
            effective_kind == "template"
            and not self.dry_run
            and not getattr(settings, "WA_TEMPLATES_APPROVED", False)
        ):
            return self._log_blocked(
                person=person,
                msg_type=MessageLog.MsgType.TEMPLATE,
                template_name=effective_template,
                payload={"body": body, "template_params": effective_params},
                reason="templates_unapproved_window_closed",
                detail="24h window is closed and WA_TEMPLATES_APPROVED is False.",
            )

        is_template = effective_kind == "template"
        try:
            billable, est_cost = check_can_send(
                person,
                is_template=is_template,
                transport=self.transport,
                bypass_opt_in=bypass_opt_in,
                allow_quiet_hours=allow_quiet_hours,
                now=ref,
            )
        except BlockedSendError as exc:
            logger.warning("Blocked send to %s (%s): %s", person.phone, exc.reason, exc)
            return self._log_blocked(
                person=person,
                msg_type=effective_kind,
                template_name=effective_template,
                payload={"body": body, "buttons": buttons, "template_params": effective_params},
                reason=exc.reason,
                detail=str(exc),
            )

        wa_payload = self._build_wa_payload(
            phone=person.phone,
            kind=effective_kind,
            body=body,
            buttons=buttons,
            list_button_text=list_button_text,
            list_rows=list_rows,
            template_name=effective_template,
            template_params=effective_params,
        )

        log = MessageLog.objects.create(
            person=person,
            phone=person.phone,
            direction=MessageLog.Direction.OUTBOUND,
            msg_type=effective_kind,
            template_name=effective_template,
            payload=wa_payload,
            status=MessageLog.Status.QUEUED,
            billable=billable,
            est_cost=est_cost,
        )

        if self.dry_run:
            wa_id = f"dryrun_{uuid.uuid4().hex[:16]}"
            log.wa_message_id = wa_id
            log.status = MessageLog.Status.DELIVERED
            log.save(update_fields=["wa_message_id", "status", "updated_at"])
            record_sent(person, now=ref)
            record_delivery(log, now=ref)
            _DRY_RUN_OUTBOX.append(
                {
                    "wa_message_id": wa_id,
                    "to": person.phone,
                    "kind": effective_kind,
                    "body": body,
                    "buttons": buttons or [],
                    "list_rows": list_rows or [],
                    "template_name": effective_template,
                    "template_params": effective_params,
                    "payload": wa_payload,
                }
            )
            return log

        return self._post_with_retries(log, wa_payload, now=ref)

    def send_text(self, person: Person, body: str, **kwargs) -> MessageLog:
        return self.send(person, "text", body=body, **kwargs)

    def send_buttons(
        self, person: Person, body: str, buttons: list[tuple[str, str]], **kwargs
    ) -> MessageLog:
        trimmed = [(bid[:256], label[:20]) for bid, label in buttons[:3]]
        return self.send(person, "buttons", body=body, buttons=trimmed, **kwargs)

    def send_list(
        self,
        person: Person,
        body: str,
        button_text: str,
        rows: list[tuple[str, str, str]],
        **kwargs,
    ) -> MessageLog:
        trimmed = [(rid[:200], title[:24], desc[:72]) for rid, title, desc in rows[:10]]
        return self.send(
            person, "list", body=body, list_button_text=button_text[:20], list_rows=trimmed, **kwargs
        )

    def send_template(
        self, person: Person, template_name: str, params: list[str], **kwargs
    ) -> MessageLog:
        return self.send(
            person, "template", template_name=template_name, template_params=params, **kwargs
        )

    def _build_wa_payload(
        self,
        *,
        phone: str,
        kind: str,
        body: str,
        buttons: list[tuple[str, str]] | None,
        list_button_text: str,
        list_rows: list[tuple[str, str, str]] | None,
        template_name: str,
        template_params: list[str],
    ) -> dict:
        base = {"messaging_product": "whatsapp", "to": phone.lstrip("+")}
        if kind == "text":
            return {**base, "type": "text", "text": {"body": body[:4096]}}
        if kind == "buttons":
            return {
                **base,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body[:1024]},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": bid, "title": title}}
                            for bid, title in (buttons or [])
                        ]
                    },
                },
            }
        if kind == "list":
            return {
                **base,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": body[:1024]},
                    "action": {
                        "button": list_button_text,
                        "sections": [
                            {
                                "title": "Options",
                                "rows": [
                                    {"id": rid, "title": title, "description": desc}
                                    for rid, title, desc in (list_rows or [])
                                ],
                            }
                        ],
                    },
                },
            }
        if kind == "template":
            return {
                **base,
                "type": "template",
                "template": build_template_payload(template_name, template_params),
            }
        raise ValueError(f"Unsupported kind: {kind}")

    def _post_with_retries(
        self, log: MessageLog, wa_payload: dict, *, now: datetime, max_attempts: int = 3
    ) -> MessageLog:
        headers = {
            "Authorization": f"Bearer {settings.WA_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        last_error = ""
        last_code = ""

        for attempt in range(1, max_attempts + 1):
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(self.endpoint, json=wa_payload, headers=headers)
                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    wa_id = (data.get("messages") or [{}])[0].get("id")
                    log.wa_message_id = wa_id
                    log.status = MessageLog.Status.SENT
                    log.save(update_fields=["wa_message_id", "status", "updated_at"])
                    record_sent(log.person, now=now)
                    return log

                last_code = str(resp.status_code)
                last_error = resp.text[:500]
                if resp.status_code in {429, 500, 502, 503, 504} and attempt < max_attempts:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                break
            except httpx.HTTPError as exc:
                last_code = "NETWORK"
                last_error = str(exc)[:500]
                if attempt < max_attempts:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue

        log.status = MessageLog.Status.FAILED
        log.error_code = last_code
        log.error_message = last_error
        log.save(update_fields=["status", "error_code", "error_message", "updated_at"])
        logger.error("WhatsApp send failed (%s) to %s: %s", last_code, log.phone, last_error)
        return log

    def _log_blocked(
        self,
        *,
        person: Person,
        msg_type: str,
        template_name: str,
        payload: dict,
        reason: str,
        detail: str,
    ) -> MessageLog:
        return MessageLog.objects.create(
            person=person,
            phone=person.phone,
            direction=MessageLog.Direction.OUTBOUND,
            msg_type=msg_type,
            template_name=template_name,
            payload=payload,
            status=MessageLog.Status.BLOCKED,
            error_code=reason,
            error_message=detail,
            billable=False,
        )


default_client = WhatsAppClient()
