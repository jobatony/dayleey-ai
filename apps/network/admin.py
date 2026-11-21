from django.contrib import admin

from .models import Pairing


@admin.register(Pairing)
class PairingAdmin(admin.ModelAdmin):
    list_display = ("user", "partner", "mode", "status", "mutual", "invite_token", "invited_at", "joined_at")
    list_filter = ("mode", "status", "mutual")
    search_fields = ("user__phone", "user__name", "partner__phone", "partner__name", "invite_token")
