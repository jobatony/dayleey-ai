# Dayleey AI — WhatsApp Accountability MVP

Rule-based WhatsApp accountability assistant built on Django 5, DRF, `django-q2` (PostgreSQL ORM broker — no Redis), and the official Meta WhatsApp Cloud API.

## Quick Start (Local)

```powershell
# 1. Create and activate venv (already created if you followed setup)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Copy env config
copy .env.example .env

# 3. Run migrations & register per-minute scheduler
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py setup_schedules

# 4. Run the test suite
.venv\Scripts\pytest
```

## Try the Bot Locally (No Meta Credentials Needed)

Use the interactive dry-run simulator to test onboarding, day planning, partner joins, quarter check-ins, and daily reports right in your terminal:

```powershell
.venv\Scripts\python manage.py simulate_chat
```

Inside the simulator:
- Type any text (e.g. `hi`, `PLAN`, `TODAY`, `PARTNER`, `STATS`, `STOP`)
- Type a number (`1`, `2`, `3`) to tap interactive buttons or list options
- Switch phones with `/as +2348022222222` (e.g. paste the `JOIN-<token>` invite message as the partner!)
- Fast-forward time and trigger the scheduler with `/tick 2026-11-21 12:00`, `/tick 2026-11-21 21:00`, `/tick 2026-11-21 21:30`

## Connecting to Meta WhatsApp Cloud API

1. Run the local server + worker + ngrok:
   ```powershell
   .venv\Scripts\python manage.py runserver 8000
   .venv\Scripts\python manage.py qcluster
   ngrok http 8000
   ```
2. Set `WA_DRY_RUN=False`, `WA_PHONE_NUMBER_ID`, `WA_BOT_PHONE_NUMBER`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN`, and `WA_APP_SECRET` in `.env`.
3. In Meta Developers → WhatsApp → Configuration:
   - Callback URL: `https://<your-ngrok-or-prod-domain>/webhooks/whatsapp/`
   - Verify token: matches `WA_VERIFY_TOKEN`
   - Subscribe to `messages` webhook fields.
4. Submit the 4 Utility templates defined in `apps/messaging/templates.py` early in Meta Business Manager:
   - `partner_daily_report`
   - `quarter_boundary_reminder`
   - `user_daily_wrapup`
   - `weekly_report` (optional)
5. **Before launch:** Update `RATE_CARD_JSON` in `.env` with Meta's current Nigeria / Rest of Africa rates.

## Production Runbook

- **Pause all outbound sending immediately:** Set `MONTHLY_BUDGET_USD=0` in env and restart, or set `WA_DRY_RUN=True`.
- **Rotate Meta access token:** Update `WA_ACCESS_TOKEN` in Render/Railway env vars and redeploy.
- **After a worker restart:** Run `python manage.py run_tick --catchup` to send only still-relevant events from the last 2 hours without duplicates.
- **Monitor costs & quality:** Open `/admin/messaging/usagecounter/` and `/admin/messaging/messagelog/` to inspect delivered counts, estimated spend, and blocked/failed sends.
