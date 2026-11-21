from django.contrib import admin

from .models import Checkin, Priority, QuarterConfig


@admin.register(QuarterConfig)
class QuarterConfigAdmin(admin.ModelAdmin):
    list_display = ("person", "quarter", "active", "start_hour", "end_hour")
    list_filter = ("quarter", "active")
    search_fields = ("person__phone", "person__name")


@admin.register(Priority)
class PriorityAdmin(admin.ModelAdmin):
    list_display = ("owner", "date", "quarter", "position", "title", "status", "missed_reason")
    list_filter = ("quarter", "status", "date")
    search_fields = ("owner__phone", "owner__name", "title")


@admin.register(Checkin)
class CheckinAdmin(admin.ModelAdmin):
    list_display = ("person", "date", "quarter", "kind", "sent_at", "response", "responded_at")
    list_filter = ("kind", "quarter", "date")
    search_fields = ("person__phone", "person__name")
