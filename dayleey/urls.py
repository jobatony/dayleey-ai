"""URL configuration for Dayleey AI."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path

from apps.messaging.bridge import BridgeInboundView
from apps.messaging.webhook import WhatsAppWebhookView


def healthz(_request):
    return JsonResponse({"status": "ok", "service": "dayleey"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("webhooks/whatsapp/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
    path("bridge/inbound/", BridgeInboundView.as_view(), name="bridge-inbound"),
]
