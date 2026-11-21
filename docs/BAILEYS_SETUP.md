# Baileys Prototype Setup & 7-Day Test Guide

> [!WARNING]
> Baileys is an unofficial WhatsApp client and violates WhatsApp's Terms of Service.
> Use a **dedicated disposable SIM**, keep testers to **≤5 consenting people**, keep `BAILEYS_ALLOWLIST` populated, and never send cold messages.

## 1. Local Windows Setup

1. Copy `.env.example` to `.env` and configure:
   ```dotenv
   WA_TRANSPORT=baileys
   WA_DRY_RUN=False
   DJANGO_Q_SYNC=False
   PAUSE_ALL=False
   WA_BOT_PHONE_NUMBER=2348000000000
   BRIDGE_SECRET=pick-a-long-random-secret
   BRIDGE_URL=http://127.0.0.1:3001
   DJANGO_INBOUND_URL=http://127.0.0.1:8000/bridge/inbound/
   BAILEYS_ALLOWLIST=2348011111111,2348022222222
   # Optional: set dedicated SIM digits to link via 8-digit pairing code instead of QR
   WA_PAIRING_PHONE=
   ```
2. Start all 3 local processes (Web, `django-q2` worker with catch-up, and Node bridge):
   ```powershell
   .\scripts\start_baileys_local.ps1
   ```
3. In the Node bridge window, scan the QR code from your dedicated SIM (**WhatsApp → Linked Devices → Link a Device**).
4. Verify health:
   - Bridge: `http://127.0.0.1:3001/health` (`connected: true`)
   - Django: `http://127.0.0.1:8000/healthz/`
5. Message the bot number from an allowlisted phone (`hi`). Reply with numbers (`1`, `2`, `3`) to pick menu options, or type commands (`PLAN`, `TODAY`, `PARTNER`, `STATS`, `STOP`).

## 2. Emergency Stop
- Set `PAUSE_ALL=True` in `.env` and restart the processes to halt all outbound sends immediately.
- Testers can reply `STOP` anytime.

## 3. Minimal Linux VPS Option (if moving off laptop mid-week)

No public ports are needed — all traffic stays on `127.0.0.1`.

1. Install Python 3.12 + Node 22 on an Ubuntu VPS, clone the repo, install `.venv` + `bridge/node_modules`, and copy `.env` (and `bridge/auth/` if already linked, or set `WA_PAIRING_PHONE` for headless pairing).
2. Create 3 `systemd` units in `/etc/systemd/system/`:
   - `dayleey-web.service`: `gunicorn dayleey.wsgi --bind 127.0.0.1:8000 --workers 2` (with `DJANGO_SETTINGS_MODULE=dayleey.settings.dev` or `prod` + `DJANGO_Q_SYNC=False`)
   - `dayleey-worker.service`: `ExecStartPre=/path/.venv/bin/python manage.py run_tick --catchup` + `ExecStart=/path/.venv/bin/python manage.py qcluster`
   - `dayleey-bridge.service`: `WorkingDirectory=/path/bridge` + `ExecStart=/usr/bin/node index.js`
3. Enable and start all three: `sudo systemctl enable --now dayleey-web dayleey-worker dayleey-bridge`.

## 4. Retiring Baileys (Moving to Official Cloud API)
1. Set `WA_TRANSPORT=cloud_api` and `WA_DRY_RUN=False` in `.env`.
2. Delete `bridge/` and `scripts/start_baileys_local.ps1`.
3. All rules, conversation flows, scheduler logic, and tests continue unchanged.
