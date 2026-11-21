from django.contrib import admin

from .models import DailyReport


@admin.register(DailyReport)
class DailyReportAdmin(admin.ModelAdmin):
    list_display = ("user", "partner", "date", "sent_at", "partner_reply", "partner_replied_at")
    list_filter = ("date", "partner_reply")
    search_fields = ("user__phone", "user__name", "partner__phone", "partner__name")
