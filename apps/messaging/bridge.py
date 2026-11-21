"""Inbound HTTP endpoint for the local Baileys Node sidecar (/bridge/inbound/)."""
from __future__ import annotations

import hmac
import logging

from django.conf import settings
from django_q.tasks import async_task
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def _verify_bridge_secret(provided: str | None) -> bool:
    expected = settings.BRIDGE_SECRET or ""
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def _is_allowlisted(phone_digits: str, jid: str) -> bool:
    allowlist: set[str] = getattr(settings, "BAILEYS_ALLOWLIST", set())
    if not allowlist:
        return True
    return phone_digits in allowlist or jid in allowlist


def process_bridge_inbound(payload: dict) -> bool:
    """Worker entrypoint: converts bridge JSON into InboundMessage and runs router."""
    from apps.engine.router import InboundMessage, handle_inbound, normalize_phone

    wa_id = str(payload.get("id") or "").strip()
    jid = str(payload.get("jid") or "").strip()
    phone_raw = str(payload.get("phone") or "").strip()
    text = str(payload.get("text") or "").strip()
    profile_name = str(payload.get("pushName") or "").strip()

    if not wa_id or not text or not (phone_raw or jid):
        return False

    phone = normalize_phone(phone_raw or jid.split("@")[0])
    if not phone:
        return False

    msg = InboundMessage(
        wa_message_id=wa_id,
        phone=phone,
        wa_jid=jid,
        text=text,
        profile_name=profile_name,
        raw=payload,
    )
    return handle_inbound(msg)


class BridgeInboundView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if not _verify_bridge_secret(request.headers.get("X-Bridge-Secret")):
            logger.warning("Rejected /bridge/inbound/ with invalid secret.")
            return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

        payload = request.data if isinstance(request.data, dict) else {}
        jid = str(payload.get("jid") or "").strip()
        from apps.engine.router import normalize_phone_digits

        phone_digits = normalize_phone_digits(str(payload.get("phone") or jid.split("@")[0]))

        if not _is_allowlisted(phone_digits, jid):
            logger.info("Ignored non-allowlisted Baileys inbound from %s (%s)", phone_digits, jid)
            return Response({"detail": "Not allowlisted"}, status=status.HTTP_403_FORBIDDEN)

        async_task(
            "apps.messaging.bridge.process_bridge_inbound",
            payload,
            sync=settings.Q_CLUSTER.get("sync", False),
        )
        return Response({"status": "accepted"}, status=status.HTTP_200_OK)
