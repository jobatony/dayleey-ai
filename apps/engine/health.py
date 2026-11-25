"""System health & worker-heartbeat diagnostics for /readyz/, Docker HEALTHCHECK, and Admin Status."""
from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django_q.models import Failure, OrmQ, Schedule, Task


def collect_system_health(now: datetime | None = None) -> dict:
    ref = now or timezone.now()
    db_ok = False
    db_error = ""

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_ok = True
    except Exception as exc:
        db_error = str(exc)[:300]

    scheduler_registered = False
    worker_ok = False
    worker_mode = "db_unavailable"
    last_tick_at = None
    last_tick_age_seconds: int | None = None
    queue_depth = 0
    failed_tasks_24h = 0

    if db_ok:
        schedule = Schedule.objects.filter(name="dayleey_per_minute_tick").first()
        scheduler_registered = schedule is not None

        last_tick = (
            Task.objects.filter(func="apps.engine.scheduler.tick", success=True)
            .order_by("-stopped")
            .first()
        )
        if last_tick is not None and last_tick.stopped is not None:
            last_tick_at = last_tick.stopped.isoformat()
            last_tick_age_seconds = max(0, int((ref - last_tick.stopped).total_seconds()))

        queue_depth = OrmQ.objects.count()
        failed_tasks_24h = Failure.objects.filter(stopped__gte=ref - timedelta(hours=24)).count()

        if settings.Q_CLUSTER.get("sync", False):
            worker_ok = True
            worker_mode = "inline_sync"
        elif last_tick is not None and last_tick_age_seconds is not None:
            worker_ok = last_tick_age_seconds <= 300
            worker_mode = "qcluster"
        elif schedule is not None and schedule.next_run is not None:
            worker_ok = schedule.next_run >= ref - timedelta(minutes=3)
            worker_mode = "starting_grace"
        else:
            worker_ok = False
            worker_mode = "stale_or_unstarted"

    healthy = db_ok and scheduler_registered and worker_ok and (queue_depth < 100)

    return {
        "status": "healthy" if healthy else "unhealthy",
        "healthy": healthy,
        "checked_at": ref.isoformat(),
        "db_ok": db_ok,
        "db_error": db_error,
        "scheduler_registered": scheduler_registered,
        "worker_ok": worker_ok,
        "worker_mode": worker_mode,
        "last_tick_at": last_tick_at,
        "last_tick_age_seconds": last_tick_age_seconds,
        "queue_depth": queue_depth,
        "failed_tasks_24h": failed_tasks_24h,
        "transport": getattr(settings, "WA_TRANSPORT", "dry_run"),
        "templates_approved": getattr(settings, "WA_TEMPLATES_APPROVED", False),
        "pause_all": getattr(settings, "PAUSE_ALL", False),
    }
