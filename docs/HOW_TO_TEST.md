# How to Test Dayleey AI From Here

All commands run in PowerShell from `c:\Users\ANTHONY\Desktop\Dayleey AI`. The virtualenv `.venv` already exists with dependencies installed; migrations are applied (SQLite `db.sqlite3`).

## 1. Automated tests (fastest)
```powershell
.venv\Scripts\pytest
```
Expected: `19 passed`. Coverage:
- `test_rules.py` – quarter rules, mid-day locking, boundaries.
- `test_webhook_and_budget.py` – verify challenge, HMAC signature, dedupe, free allowance, kill-switch, 24h window fallback, delivery counting.
- `test_flows_and_scheduler.py` – full day lifecycle with partner, nudge/unconfirmed, cross-timezone deferral, self-report fallback, STOP, DELETE MY DATA.

Run one file: `.venv\Scripts\pytest tests\test_rules.py`. Sanity check: `.venv\Scripts\python manage.py check`.

## 2. Manual test with the local simulator (no Meta needed)
```powershell
.venv\Scripts\python manage.py simulate_chat --phone +2348011111111
```
Type text, or a number to tap a button/list item shown in the last bot message.

| Simulator command | Meaning |
|---|---|
| `/as +2348022222222` | Switch to another phone (e.g. the partner) |
| `/tick 2026-11-21 12:00` | Run the scheduler at that time (naive = Africa/Lagos) |
| `/quit` | Exit |

> [!NOTE]
> Time-based behaviour depends on simulated time, so use `/tick`. Real inbound messages use the real clock unless you have ticked (after a `/tick`, later messages use that simulated time).

### Scenario A – Onboarding and planning (user)
1. `hi` → consent → `1` (I Agree).
2. Type your name → `1` (Lagos) → `1` (Q1–Q3) → `1` (Get Invite Link). Copy the `JOIN-XXXXXXXX` token from the link.
3. `PLAN` → pick tomorrow if offered. Enter Q1 `Morning run; Write tests`, Q2 tap Skip, Q3 `Read; Cook`. Expect a rule warning (Q1+Q2 ok, Q2+Q3 = 2 is ok; to see a failure try Q1 `A`, Q2 skip, Q3 `B; C` → Q1+Q2 < 2). Use `Fix` and confirm.
4. `TODAY`, `STATS`, `HELP`.

### Scenario B – Partner joins
1. `/as +2348022222222`.
2. Send: `Hi Dayleey, I'm joining as Anthony's accountability partner [JOIN-XXXXXXXX]` (use your token).
3. Tap `1` Accept → choose `2` Partner only (or `1` to track yourself).
4. `/as +2348011111111` – you should see "accepted" (check the message output; it is shown when you switch phones only if still pending; otherwise inspect admin message logs).

### Scenario C – A day of check-ins (use ticks, planned date = the day after you planned)
Use the date of the plan you saved, e.g. `2026-11-21`:
```
/tick 2026-11-21 06:00   # Q1 reminder
/tick 2026-11-21 12:00   # Q1 check-in (3 buttons if 2 priorities) + Q2 reminder
```
Then tap `2` Review Each → `1` Done → `3` Missed → pick a reason.
```
/tick 2026-11-21 12:35   # nudge (if you did not answer)
/tick 2026-11-21 14:05   # unanswered items become 'unconfirmed'
/tick 2026-11-21 21:00   # wrap-up with confirm buttons
/tick 2026-11-21 21:30   # partner daily report
```
Switch to the partner (`/as`) and tap `1` (👏), `2` (👀) or `3` (💪). Switch back: you receive the reaction.
Re-running the same `/tick` must produce **no duplicate** messages.

### Scenario D – Safety commands
- `PAUSE` then `RESUME`.
- Partner sends `STOP` → partner opted out; user told they are in self-report mode.
- User sends `DELETE MY DATA` → `1` Yes → data wiped.
- 3-day fallback: in the Django shell set `Pairing.invited_at` 4 days back (see §4), then `/tick` any time.

## 3. Test the webhook with signed HTTP requests
Start the server (dry-run stays on, so no real sends):
```powershell
.venv\Scripts\python manage.py runserver 8000
```
In a second terminal:
```powershell
# Verify handshake (expects: 12345)
curl "http://127.0.0.1:8000/webhooks/whatsapp/?hub.mode=subscribe&hub.verify_token=dayleey-verify-token&hub.challenge=12345"

# Bad signature (expects 403)
curl -X POST http://127.0.0.1:8000/webhooks/whatsapp/ -H "Content-Type: application/json" -H "X-Hub-Signature-256: sha256=bad" -d "{}"
```
Valid signed POSTs are exercised by `test_webhook_signature_and_deduplication` (secret defaults to `dev-app-secret` unless overridden in `.env`). In dev settings django-q runs tasks synchronously, so the handler runs inside the request. Check results in admin (§4).

## 4. Inspect data
```powershell
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver 8000
```
Open `http://127.0.0.1:8000/admin/`: check **Message logs** (statuses, `blocked` reasons), **Usage counters** (delivered counts, est. cost), **Scheduled events** (idempotency), **Priorities**, **Pairings**, **Daily reports**.

Django shell example (backdate an invite to test the fallback):
```powershell
.venv\Scripts\python manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.network.models import Pairing; Pairing.objects.update(invited_at=timezone.now()-timedelta(days=4))"
```
Reset all local data: delete `db.sqlite3`, then `.venv\Scripts\python manage.py migrate` and `.venv\Scripts\python manage.py setup_schedules`.

## 5. Scheduler from the CLI
```powershell
.venv\Scripts\python manage.py run_tick --at "2026-11-21T12:00:00+01:00"
.venv\Scripts\python manage.py run_tick --catchup
```
Note: the simulator and `run_tick` share the same database, but `run_tick` prints counts only; in dry-run the messages appear in logs/admin, not the console.

## 6. Test budget guardrails
In `.env` (copy from `.env.example` first) try:
- `MONTHLY_BUDGET_USD=0.01` → after a few messages, sends are logged `blocked: global_budget_exceeded`.
- `USER_MONTHLY_MSG_CAP=5` → `user_monthly_cap`.
- `QUIET_HOURS_START/END` → proactive sends blocked inside quiet hours.
Restart the simulator after editing `.env`. Reset counters by clearing **Usage counters** in admin.

## 7. Going live with Meta (when M0 is done)
1. Complete business verification; get number, token, app secret; set `.env`: `WA_DRY_RUN=False`, `WA_PHONE_NUMBER_ID`, `WA_BOT_PHONE_NUMBER`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET`.
2. Run three processes: `manage.py runserver 8000`, `manage.py qcluster` (also set `Q_CLUSTER sync` false by using prod settings or env `DJANGO_SETTINGS_MODULE=dayleey.settings.base`), and `ngrok http 8000`.
3. In Meta → WhatsApp → Configuration set callback `https://<ngrok>/webhooks/whatsapp/` and verify token; subscribe to `messages`.
4. Submit the 4 templates from `apps/messaging/templates.py` (Utility).
5. Test with your own phone and one friend as partner; confirm statuses (`delivered`) appear in Message logs and Usage counters.
6. Put the real Nigeria rate in `RATE_CARD_JSON`.

## 8. Pass checklist
- [ ] `pytest` → 19 passed
- [ ] Simulator onboarding completes; rule violation message names the failing pair
- [ ] Partner joins via `JOIN-` link; user is notified
- [ ] 12:00 tick shows 3-button check-in; review flow records statuses
- [ ] Re-running a tick sends nothing new
- [ ] 21:30 tick delivers partner report; partner reaction reaches the user
- [ ] `STOP`, `PAUSE/RESUME`, `DELETE MY DATA` behave as described
- [ ] Admin shows logs, counters, events; budget caps block sends
