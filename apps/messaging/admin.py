from django.contrib import admin

from .models import MessageLog, ScheduledEvent, UsageCounter


@admin.register(MessageLog)
class MessageLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "direction",
        "phone",
        "msg_type",
        "template_name",
        "status",
        "billable",
        "est_cost",
    )
    list_filter = ("direction", "msg_type", "status", "billable")
    search_fields = ("phone", "wa_message_id", "template_name", "error_code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UsageCounter)
class UsageCounterAdmin(admin.ModelAdmin):
    list_display = (
        "month",
        "scope_label",
        "sent_total",
        "delivered_service",
        "delivered_template",
        "delivered_total",
        "est_cost",
    )
    list_filter = ("month",)
    search_fields = ("person__phone", "person__name")

    @admin.display(description="Scope")
    def scope_label(self, obj):
        return obj.person.display_name if obj.person else "GLOBAL (Business Number)"


@admin.register(ScheduledEvent)
class ScheduledEventAdmin(admin.ModelAdmin):
    list_display = ("executed_at", "event_type", "person", "local_date", "quarter", "status")
    list_filter = ("event_type", "status", "local_date")
    search_fields = ("event_key", "person__phone", "person__name")
