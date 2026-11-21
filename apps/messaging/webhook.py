"""WhatsApp Cloud API webhook view with HMAC signature verification and async offload."""
from __future__ import annotations

import hashlib
import hmac
import logging

from django.conf import settings
from django.http import HttpResponse
from django_q.tasks import async_task
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def verify_signature(raw_body: bytes, header_value: str | None) -> bool:
    secret = (settings.WA_APP_SECRET or "").encode("utf-8")
    if not secret:
        return False
    if not header_value or not header_value.startswith("sha256="):
        return False
    provided = header_value.split("=", 1)[1].strip()
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def process_webhook_payload(payload: dict) -> dict:
    """Worker entrypoint: processes both status updates and inbound messages."""
    from apps.engine.router import handle_inbound, parse_webhook_messages
    from apps.messaging.status import process_status_updates

    statuses_handled = process_status_updates(payload)
    messages = parse_webhook_messages(payload)
    for msg in messages:
        try:
            handle_inbound(msg)
        except Exception:
            logger.exception("Error handling inbound message %s from %s", msg.wa_message_id, msg.phone)
    return {"statuses": statuses_handled, "messages": len(messages)}


class WhatsAppWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge", "")
        if mode == "subscribe" and token == settings.WA_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type="text/plain", status=200)
        return Response({"detail": "Invalid verify token"}, status=status.HTTP_403_FORBIDDEN)

    def post(self, request):
        sig = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(request.body, sig):
            logger.warning("Rejected WhatsApp webhook with invalid signature.")
            return Response({"detail": "Invalid signature"}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {}
        async_task(
            "apps.messaging.webhook.process_webhook_payload",
            payload,
            sync=settings.Q_CLUSTER.get("sync", False),
        )
        return Response({"status": "accepted"}, status=status.HTTP_200_OK)
