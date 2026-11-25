"""URL configuration for Dayleey AI."""
from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path

from apps.engine.health import collect_system_health
from apps.engine.status_views import system_status_view
from apps.messaging.webhook import WhatsAppWebhookView


def healthz(_request):
    return JsonResponse({"status": "ok", "service": "dayleey"})


def readyz(_request):
    health = collect_system_health()
    return JsonResponse(health, status=200 if health["healthy"] else 503)


urlpatterns = [
    path(f"{settings.ADMIN_URL}status/", system_status_view, name="system-status"),
    path(settings.ADMIN_URL, admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("webhooks/whatsapp/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
]
