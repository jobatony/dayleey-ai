"""Mobile-friendly Django Admin Status Dashboard (/<ADMIN_URL>status/)."""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from apps.accounts.models import Person
from apps.engine.health import collect_system_health
from apps.messaging.budget import month_key
from apps.messaging.models import MessageLog, UsageCounter
from apps.network.models import Pairing


def _badge(ok: bool, yes_label: str = "OK", no_label: str = "ISSUE") -> str:
    bg = "#166534" if ok else "#991b1b"
    text = escape(yes_label if ok else no_label)
    return f'<span class="badge" style="background:{bg}">{text}</span>'


@staff_member_required
def system_status_view(request) -> HttpResponse:
    now = timezone.now()
    health = collect_system_health(now=now)
    since_24h = now - timedelta(hours=24)

    mk = month_key(now)
    global_counter = UsageCounter.objects.filter(month=mk, person__isnull=True).first()
    delivered_service = global_counter.delivered_service if global_counter else 0
    delivered_template = global_counter.delivered_template if global_counter else 0
    est_cost = global_counter.est_cost if global_counter else 0

    active_users = Person.objects.filter(
        role__in=[Person.Role.USER, Person.Role.BOTH],
        opted_in=True,
        opted_out=False,
    ).count()
    active_pairings = Pairing.objects.filter(
        mode=Pairing.Mode.PARTNER,
        status=Pairing.Status.JOINED,
    ).count()

    inbound_24h = MessageLog.objects.filter(
        direction=MessageLog.Direction.INBOUND,
        created_at__gte=since_24h,
    ).count()
    outbound_rows = (
        MessageLog.objects.filter(
            direction=MessageLog.Direction.OUTBOUND,
            created_at__gte=since_24h,
        )
        .values("status")
        .annotate(total=Count("id"))
    )
    outbound_counts = {row["status"]: row["total"] for row in outbound_rows}

    recent_errors = list(
        MessageLog.objects.filter(
            status__in=[MessageLog.Status.BLOCKED, MessageLog.Status.FAILED]
        ).order_by("-created_at")[:10]
    )

    admin_home_url = reverse("admin:index")
    overall_badge = _badge(health["healthy"], "HEALTHY", "UNHEALTHY")
    tick_age = (
        f"{health['last_tick_age_seconds']}s ago"
        if health["last_tick_age_seconds"] is not None
        else "never"
    )
    allowlist_phones_count = len(getattr(settings, "BETA_ALLOWLIST_PHONES", set()))

    error_rows_html = ""
    if recent_errors:
        parts = []
        for item in recent_errors:
            parts.append(
                "<tr>"
                f"<td>{escape(item.created_at.strftime('%Y-%m-%d %H:%M:%S'))}</td>"
                f"<td>{escape(item.phone)}</td>"
                f"<td>{escape(item.msg_type)}</td>"
                f"<td>{escape(item.status)}</td>"
                f"<td><code>{escape(item.error_code or '-')}</code></td>"
                f"<td>{escape((item.error_message or '')[:140])}</td>"
                "</tr>"
            )
        error_rows_html = "\n".join(parts)
    else:
        error_rows_html = '<tr><td colspan="6" class="muted">No blocked or failed sends logged.</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dayleey System Status</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 16px; line-height: 1.45; }}
    .wrap {{ max-width: 1024px; margin: 0 auto; }}
    .top {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 16px; }}
    h1 {{ font-size: 1.35rem; margin: 0; display: flex; align-items: center; gap: 10px; }}
    .actions a {{ color: #93c5fd; text-decoration: none; margin-left: 12px; font-size: 0.9rem; }}
    .actions a:hover {{ text-decoration: underline; }}
    .badge {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 0.78rem; font-weight: 600; color: #fff; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 14px; }}
    .card h2 {{ font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.04em; color: #94a3b8; margin: 0 0 10px; }}
    .kv {{ display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.9rem; border-bottom: 1px solid #293548; }}
    .kv:last-child {{ border-bottom: none; }}
    .muted {{ color: #94a3b8; font-size: 0.85rem; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
    th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #334155; vertical-align: top; }}
    th {{ color: #94a3b8; font-weight: 600; }}
    code {{ background: #0f172a; padding: 1px 5px; border-radius: 4px; color: #fca5a5; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h1>Dayleey Server Status {overall_badge}</h1>
      <div class="actions">
        <span class="muted">UTC: {escape(now.strftime('%Y-%m-%d %H:%M:%S'))}</span>
        <a href="">Refresh</a>
        <a href="{escape(admin_home_url)}">Django Admin</a>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Core Services</h2>
        <div class="kv"><span>Database</span>{_badge(health['db_ok'], 'OK', 'ERROR')}</div>
        <div class="kv"><span>Scheduler</span>{_badge(health['scheduler_registered'], 'REGISTERED', 'MISSING')}</div>
        <div class="kv"><span>Worker ({escape(health['worker_mode'])})</span>{_badge(health['worker_ok'], 'UP', 'STALE')}</div>
        <div class="kv"><span>Last Tick</span><strong>{escape(tick_age)}</strong></div>
        <div class="kv"><span>Queue Depth / Failed 24h</span><strong>{health['queue_depth']} / {health['failed_tasks_24h']}</strong></div>
      </div>

      <div class="card">
        <h2>WhatsApp Config</h2>
        <div class="kv"><span>Transport</span><strong>{escape(health['transport'])}</strong></div>
        <div class="kv"><span>Dry Run</span><strong>{escape(str(settings.WA_DRY_RUN))}</strong></div>
        <div class="kv"><span>Templates Approved</span><strong>{escape(str(health['templates_approved']))}</strong></div>
        <div class="kv"><span>PAUSE_ALL Kill-Switch</span>{_badge(not health['pause_all'], 'OFF', 'PAUSED')}</div>
        <div class="kv"><span>Beta Allowlist</span><strong>{escape(str(settings.BETA_ALLOWLIST_ENABLED))} ({allowlist_phones_count} env)</strong></div>
      </div>

      <div class="card">
        <h2>Monthly Usage ({escape(mk)})</h2>
        <div class="kv"><span>Free Service Used</span><strong>{delivered_service} / {settings.FREE_SERVICE_MESSAGES_PER_MONTH}</strong></div>
        <div class="kv"><span>Templates Delivered</span><strong>{delivered_template}</strong></div>
        <div class="kv"><span>Estimated Spend</span><strong>${est_cost:.4f} / ${settings.MONTHLY_BUDGET_USD}</strong></div>
        <div class="kv"><span>Active Users / Partners</span><strong>{active_users} / {active_pairings}</strong></div>
      </div>

      <div class="card">
        <h2>Last 24h Traffic</h2>
        <div class="kv"><span>Inbound Messages</span><strong>{inbound_24h}</strong></div>
        <div class="kv"><span>Outbound Sent</span><strong>{outbound_counts.get('sent', 0)}</strong></div>
        <div class="kv"><span>Outbound Delivered / Read</span><strong>{outbound_counts.get('delivered', 0)} / {outbound_counts.get('read', 0)}</strong></div>
        <div class="kv"><span>Outbound Blocked</span><strong>{outbound_counts.get('blocked', 0)}</strong></div>
        <div class="kv"><span>Outbound Failed</span><strong>{outbound_counts.get('failed', 0)}</strong></div>
      </div>
    </div>

    <div class="card">
      <h2>Last 10 Blocked or Failed Sends</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time (UTC)</th>
              <th>Phone</th>
              <th>Type</th>
              <th>Status</th>
              <th>Code</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {error_rows_html}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body>
</html>"""
    return HttpResponse(html)
