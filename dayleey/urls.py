"""URL configuration for Dayleey AI."""
from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import path
from django_q.models import Schedule

from apps.messaging.bridge import BridgeInboundView
from apps.messaging.webhook import WhatsAppWebhookView


def healthz(_request):
    return JsonResponse({"status": "ok", "service": "dayleey"})


def readyz(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        scheduler_registered = Schedule.objects.filter(
            name="dayleey_per_minute_tick"
        ).exists()
        return JsonResponse(
            {
                "status": "ready",
                "db": "ok",
                "scheduler_registered": scheduler_registered,
            },
            status=200,
        )
    except Exception as exc:
        return JsonResponse({"status": "error", "detail": str(exc)}, status=503)


urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("webhooks/whatsapp/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
    path("bridge/inbound/", BridgeInboundView.as_view(), name="bridge-inbound"),
]
