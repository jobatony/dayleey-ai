import csv

from django.contrib import admin
from django.http import HttpResponse

from .models import Person


@admin.action(description="Export selected people as CSV")
def export_people_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="dayleey_people.csv"'
    writer = csv.writer(response)
    writer.writerow(
        ["id", "phone", "name", "timezone", "role", "opted_in", "opted_out", "paused", "state", "created_at"]
    )
    for p in queryset:
        writer.writerow(
            [p.id, p.phone, p.name, p.timezone, p.role, p.opted_in, p.opted_out, p.paused, p.state, p.created_at]
        )
    return response


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = (
        "phone",
        "name",
        "role",
        "timezone",
        "opted_in",
        "opted_out",
        "paused",
        "state",
        "window_expires_at",
    )
    list_filter = ("role", "opted_in", "opted_out", "paused", "plan", "is_allowlisted")
    search_fields = ("phone", "name")
    readonly_fields = ("created_at", "updated_at", "last_inbound_at", "window_expires_at")
    actions = [export_people_csv]
