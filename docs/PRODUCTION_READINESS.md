# Moving Dayleey AI from Development to Production

This lists what must change, and why, to run the current codebase on a production host (Render/Railway or similar). **None of these changes have been applied yet.** Each section says where in the code it applies.

Priority legend: 🔴 must do before real users · 🟠 should do before beta · 🟢 later / scaling.

---

## 1. Settings and secrets

| Pri | Item | Today | Change |
|---|---|---|---|
| 🔴 | `SECRET_KEY` | Falls back to an insecure default in `settings/base.py` | Require it in prod: raise an error if unset. Generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| 🔴 | Settings module | `manage.py` defaults to `dev`; `wsgi.py` defaults to `prod` | Set `DJANGO_SETTINGS_MODULE=dayleey.settings.prod` explicitly on **web and worker** |
| 🔴 | `DEBUG` | `prod.py` forces `False` | Keep. Never set `DEBUG=True` in prod env |
| 🔴 | `ALLOWED_HOSTS` | localhost + ngrok/render wildcards | Only your real domain(s) |
| 🔴 | `WA_DRY_RUN` | Defaults to `True` | Set `False` in prod, otherwise nothing is really sent |
| 🔴 | `WA_APP_SECRET` | Defaults to `dev-app-secret` | Real Meta app secret. Without it webhooks are rejected, or worse, verifiable with a known secret |
| 🔴 | `WA_VERIFY_TOKEN` | Default `dayleey-verify-token` | Long random value |
| 🔴 | `.env` | Local file | Don't deploy it. Use the host's environment variables / secret manager. Keep `.env` in `.gitignore` (already is) |
| 🔴 | `WA_ACCESS_TOKEN` | Temporary 24 h token | Permanent **System User** token from Meta Business settings |

Recommended additions to `settings/prod.py`:
```python
import os
from .base import *  # noqa

DEBUG = False
if SECRET_KEY.startswith("dev-insecure"):
    raise RuntimeError("SECRET_KEY must be set in production")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True          # exempt /healthz/ if the host health-checks over http
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = ["https://" + h.lstrip(".") for h in ALLOWED_HOSTS if "." in h]
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
```
Test with `python manage.py check --deploy` using prod settings and fix every warning.

## 2. Database

- 🔴 **Use PostgreSQL**, not SQLite. Set `DATABASE_URL`. `psycopg` and `dj-database-url` are already in `requirements.txt`.
- 🔴 Use a database that does not expire or pause (verify the free-tier terms of Neon/Supabase/Render before relying on them).
- 🔴 **Backups:** enable automated daily backups and test a restore. The database contains people's phone numbers and daily priorities.
- 🟠 If you use a pooled connection string (pgbouncer, Neon pooler), set `conn_max_age` appropriately (currently 600 in `base.py`); transaction pooling may require disabling server-side cursors.
- 🟠 Run migrations as part of deploy, before new code serves traffic (`python manage.py migrate --noinput`).
- 🟢 Add indexes if tables grow: `MessageLog(person, created_at)` and `Priority(owner, date)` are the hot paths.

## 3. Processes: web + worker (the biggest change)

Dev settings run queued tasks **inline** (`Q_CLUSTER["sync"] = True` in `dev.py`). Production uses `sync=False`, so:

- 🔴 A **web** process (gunicorn) and a separate always-on **worker** process (`python manage.py qcluster`) must both run. Without the worker, inbound WhatsApp messages are accepted (HTTP 200) but **never processed**, and the per-minute scheduler never fires.
- 🔴 Run `python manage.py setup_schedules` once per deploy (idempotent). It registers the per-minute `tick`. `Procfile` has this in a `release` step, but `render.yaml` runs it in the web build command only; make sure it runs somewhere on every platform you use.
- 🔴 The worker and the web service must use the **same** `DATABASE_URL` (django-q2 uses the database as the queue).
- 🟠 **Concurrency hazard:** `Q_CLUSTER["workers"] = 2`. Two messages from the same person can be handled at the same time and race on `Person.state`. Fix by either setting workers to 1 for the MVP, or locking the person row at the start of `handle_inbound` (`select_for_update()` inside a transaction).
- 🟠 **Counter races:** budget counters in `messaging/budget.py` read-modify-write (`counter.sent_total += 1`). Under concurrency they can undercount. Replace with `F()` expressions (`update(sent_total=F("sent_total") + 1)`).
- 🟠 **Restart/outage gap:** `tick()` only fires boundary events during the first 5 minutes of the hour. If the worker is down longer than that, those reminders are skipped. Run `python manage.py run_tick --catchup` on worker start (e.g. wrapper start command), and add alerting if no tick has run for 5+ minutes (check latest `ScheduledEvent`/django-q `Success` rows).
- 🟠 Task timeouts: `timeout=90`, `retry=120`. A slow Meta call plus retries (up to ~3 attempts) must stay under the timeout, or django-q may re-run a task. Message dedupe protects inbound, but test it.

Suggested start commands:
```
web:    gunicorn dayleey.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 30
worker: python manage.py run_tick --catchup && python manage.py qcluster
```
(Use a small shell wrapper if your platform needs `&&` support.)

## 4. Hosting and cost reality

- 🔴 Free web tiers **sleep**. A sleeping web service delays or drops Meta webhooks, and a sleeping worker misses all reminders. For real users you need an always-on web service **and** an always-on worker. `render.yaml` uses `plan: starter` (paid) for both for this reason.
- 🟠 To reduce cost you can run the worker in the same service via a process manager (e.g. `honcho`/supervisord), at the cost of weaker isolation.
- 🟠 Choose a region close to your users and to your database (reduces latency of every webhook round trip).
- 🟠 Pin the Python version (`runtime.txt` or `PYTHON_VERSION` env var; local dev uses 3.12).

## 5. Webhook hardening

Current behaviour: the signature is verified, then the payload is queued and `200` returned (`messaging/webhook.py`).

- 🔴 Keep signature verification on. Never log full request bodies in production (they contain phone numbers and message text).
- 🔴 Meta retries unanswered webhooks. The webhook must stay fast, which it is as long as the worker is running and the queue is healthy. Monitor queue depth (django-q `OrmQ` row count).
- 🟠 Add rate limiting or a WAF rule for the webhook path, and restrict the admin path.
- 🟠 Ignore unknown event types gracefully (already the case); add a metric for payloads that parse to zero messages.
- 🟢 Add a dead-letter view: tasks that fail repeatedly (django-q **Failed tasks** in admin).

## 6. Meta / WhatsApp production readiness

- 🔴 Business verification, a real phone number registered to the Cloud API, and a display name approved.
- 🔴 Permanent System User access token with `whatsapp_business_messaging` and `whatsapp_business_management` permissions.
- 🔴 Submit and get **approved** the four Utility templates (`partner_daily_report`, `quarter_boundary_reminder`, `user_daily_wrapup`, `weekly_report`). Template names and the number/order of `{{n}}` parameters in Meta must match `messaging/templates.py` exactly.
- 🔴 Put the app in **Live** mode and point the webhook at the production domain; subscribe to `messages`.
- 🔴 Privacy Policy and Terms URLs (required for verification).
- 🔴 Replace the placeholder `RATE_CARD_JSON` with the real Nigeria/Rest of Africa rates from Meta's rate card. The budget kill-switch depends on it.
- 🟠 Watch the **quality rating** and **messaging tier** (unverified/new numbers have low unique-recipient limits per 24 h, which caps beta size).
- 🟠 Consider enabling `BETA_ALLOWLIST_ENABLED=True` at first so only invited numbers can use the bot.

## 7. Cost and abuse controls (tune for production values)

- 🔴 Set `MONTHLY_BUDGET_USD` to a number you can afford; the kill-switch blocks sends beyond it.
- 🔴 Review `USER_MONTHLY_MSG_CAP` (350), `USER_DAILY_MSG_LIMIT` (10), `PARTNER_DAILY_MSG_LIMIT` (2).
- 🟠 Know the loopholes: scheduled boundary and wrap-up messages bypass the **daily** limit (they use quiet-hours bypass), but still respect monthly cap and global budget. Decide whether to enforce the daily cap for them.
- 🟠 Set up a budget alert (e.g. email at 80% of `MONTHLY_BUDGET_USD`) rather than discovering the kill-switch after the fact.

## 8. Static files and admin

- 🔴 Run `python manage.py collectstatic --noinput` in the build (WhiteNoise is configured; `render.yaml` already does this).
- 🔴 Create the admin user with `createsuperuser` (strong password). Admin shows personal data.
- 🟠 Change the admin URL from `/admin/`, and add 2FA (e.g. `django-otp`) or restrict by IP/VPN.
- 🟠 Create a read-only staff role for support so not everyone can export or delete people.

## 9. Logging, monitoring, alerting

- 🔴 Set `SENTRY_DSN` (already wired in `base.py`) and confirm an exception shows up.
- 🟠 Switch logging to structured/JSON or at least include request ids; log level via env.
- 🟠 Alerts: no `tick` in 5+ minutes, queue depth growing, failed-task count, spike in `blocked`/`failed` message logs, quality rating drop, 4xx/5xx rate on `/webhooks/whatsapp/`.
- 🟠 Uptime check on `/healthz/` (it currently returns OK without checking the DB or worker; consider a deeper `/readyz/`).
- 🟢 Dashboard on usage and cost (admin **Usage counters** already shows delivered counts and estimated cost).

## 10. Data protection

- 🔴 Privacy Policy covering that priorities are shared with the chosen partner and that partner phone numbers are stored (Nigeria NDPA / GDPR if EU users).
- 🟠 `MessageLog.payload` stores full message content. Define a retention period (e.g. delete or redact after 30–90 days) with a scheduled job.
- 🟠 Encrypt backups and restrict who can access the database.
- 🟠 Document the `DELETE MY DATA` process, including what stays (billing counters, anonymised logs).

## 11. Performance and scaling notes

- The scheduler `tick()` loads all active users every minute and runs several queries per user. Fine for a ~50-user beta. For hundreds of users: filter users whose local time is at a boundary (precompute by timezone), use `select_related`/`prefetch_related`, and batch.
- `get_active_quarters` calls `ensure_quarter_configs` on every tick per user; cache or create configs only at onboarding.
- Outbound sends happen inline in the worker; if volume grows, queue each send as its own task with retry.

## 12. CI/CD and release process

- 🟠 Run `pytest` and `manage.py check --deploy` in CI (GitHub Actions) on each push.
- 🟠 Deploy steps: build → migrate → collectstatic → `setup_schedules` → restart web and worker together.
- 🟠 Keep a staging environment with a second Meta test number before changing prod templates or flows.
- 🟠 Rollback plan: keep the previous release available; make migrations backward compatible.
- 🟢 Add `requirements.lock` / pinned hashes for reproducible builds.

## 13. Runbook essentials

| Situation | Action |
|---|---|
| Stop all sending immediately | Set `MONTHLY_BUDGET_USD=0` (or `WA_DRY_RUN=True`) and restart web and worker |
| Token expired / revoked | Replace `WA_ACCESS_TOKEN`, redeploy |
| Worker was down | Run `python manage.py run_tick --catchup` |
| Meta quality rating drops | Pause new onboarding (`BETA_ALLOWLIST_ENABLED=True`), review `MessageLog` for blocks/reports |
| Bad deploy | Roll back release; check Sentry and failed tasks |

## 14. Go-live checklist
- [ ] `check --deploy` clean on prod settings
- [ ] Postgres + backups + restore test
- [ ] Web **and** worker running, `setup_schedules` applied, catch-up on start
- [ ] Secrets set in the host, `WA_DRY_RUN=False`, permanent token
- [ ] Business verified, templates approved, webhook verified, app Live
- [ ] Real rate card, budget and caps set, budget alert in place
- [ ] Admin secured, superuser created
- [ ] Sentry and uptime alerts working
- [ ] Privacy Policy published, retention rule decided
- [ ] Smoke test with 2 real phones (onboarding → partner → boundary → report), then widen the allowlist
