# Live WhatsApp Cloud API Setup (Meta Test Number + AWS / Local)

Goal: connect Dayleey to Meta's **Official WhatsApp Cloud API** using their free `+1 555...` test phone number and a **non-expiring Permanent System User Token** (no Business Verification required yet, up to 5 verified tester phones).

> [!NOTE]
> Meta's dashboard changes occasionally, so menu names may differ slightly. Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/get-started.

---

## Part 1 – Meta Setup + Permanent Token (~20 min)

1. **Create the Meta App**
   - [ ] Log in at https://developers.facebook.com and click **Create App**.
   - [ ] Select **Other** / **Business** (or **Connect with customers through WhatsApp**), name it `Dayleey`.
   - [ ] Add the **WhatsApp** product and pick/create a Meta Business Portfolio when prompted.

2. **Grab Test Number IDs & Add Testers (up to 5 phones)**
   - [ ] Open **WhatsApp → API Setup**.
   - [ ] Copy **Phone number ID** → `WA_PHONE_NUMBER_ID`.
   - [ ] Copy the **Test phone number** (digits only with country code, e.g. `15551234567`) → `WA_BOT_PHONE_NUMBER`.
   - [ ] Under **To → Manage phone number list**, add your phone and your tester/partner phones (up to 5 total) and confirm each via the WhatsApp OTP.
   - [ ] Click **Send message** (`hello_world`) to your own phone to confirm delivery works.

3. **Get the App Secret & Generate a Verify Token**
   - [ ] Open **App settings → Basic**, click **Show** next to **App secret** → `WA_APP_SECRET`.
   - [ ] Pick a long random string for webhook verification → `WA_VERIFY_TOKEN` (e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).

4. **Create a Permanent System User Token (never expires every 24h)**
   - [ ] Open **Meta Business Settings** (`https://business.facebook.com/settings/`) for that same Business Portfolio.
   - [ ] Go to **Users → System Users → Add**. Name it `dayleey-bot`, role **Admin**.
   - [ ] Click **Add Assets → Apps** → select your `Dayleey` app → toggle **Full Control / Manage App** → Save.
   - [ ] Also assign your **WhatsApp Account** under **Add Assets → WhatsApp Accounts** if listed.
   - [ ] Click **Generate New Token**, choose your `Dayleey` app, select **Never** expire (if prompted), and check:
     - `whatsapp_business_messaging`
     - `whatsapp_business_management`
   - [ ] Copy the token → `WA_ACCESS_TOKEN`.

---

## Part 2A – Connect Directly to Your AWS Server *(Recommended — no ngrok)*

Since your AWS server already has public HTTPS via Caddy:

1. Edit `/opt/dayleey/dayleey-ai/.env.prod`:
   ```dotenv
   WA_TRANSPORT=cloud_api
   WA_DRY_RUN=False
   WA_TEMPLATES_APPROVED=False

   WA_PHONE_NUMBER_ID=<from API Setup>
   WA_BOT_PHONE_NUMBER=<test number digits e.g. 15551234567>
   WA_ACCESS_TOKEN=<permanent system user token>
   WA_VERIFY_TOKEN=<your random verify token>
   WA_APP_SECRET=<from App settings -> Basic>

   BETA_ALLOWLIST_ENABLED=True
   BETA_ALLOWLIST_PHONES=2347025133613,2348147611480
   ```
2. Rebuild and start (cleaning up any old Baileys container):
   ```bash
   cd /opt/dayleey/dayleey-ai
   git pull origin production-server
   docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
   ```
   *(If you're using the on-server Postgres database, include `--profile localdb`.)*
3. In **Meta Developers → WhatsApp → Configuration → Webhook → Edit**:
   - **Callback URL:** `https://<YOUR_DOMAIN>/webhooks/whatsapp/`
   - **Verify token:** exact value of `WA_VERIFY_TOKEN`
   - Click **Verify and save**, then **Subscribe** to the **`messages`** field.

---

## Part 2B – Optional Local PC Testing with ngrok

If testing from Windows without AWS:
1. Copy `.env.example` to `.env` and set `WA_TRANSPORT=cloud_api`, `WA_DRY_RUN=False`, `WA_TEMPLATES_APPROVED=False`, `DJANGO_Q_SYNC=True`, and your Meta credentials above.
2. Start Django and ngrok in two terminals:
   ```powershell
   .venv\Scripts\python manage.py runserver 8000
   ngrok http 8000
   ```
3. Set Meta's Callback URL to `https://<your-ngrok-url>/webhooks/whatsapp/`, verify with `WA_VERIFY_TOKEN`, and subscribe to `messages`.

---

## Part 3 – Examining Server Health Online

You can check your live AWS server from any phone or browser without SSH:

1. **Visual Status Dashboard (Admin login required):**
   - Visit `https://<YOUR_DOMAIN>/<ADMIN_URL>status/` (e.g. `https://dayleeyai.abrdns.com/ops-dayleey-7k3m/status/`).
   - Shows PostgreSQL status, `qcluster` worker heartbeat (`last_tick_age_seconds`), queue depth, failed tasks in 24h, active users/partners, monthly free-tier/budget usage, 24h traffic, and the last 10 `blocked`/`failed` sends.
2. **Automated Uptime Alerting (`/readyz/`):**
   - Point a free **UptimeRobot** or **Better Stack** monitor (every 5 minutes) at:
     `https://<YOUR_DOMAIN>/readyz/`
   - Returns HTTP `200` when DB, scheduler, and worker heartbeat (<5 min old) are healthy, and HTTP `503` if the worker stalls or the DB drops.

---

## Part 4 – First Live Test & Troubleshooting

- [ ] Message `hi` to the Meta test number from one of your verified phones.
- [ ] Complete onboarding (`I Agree` → name → timezone → quarters → partner invite link).
- [ ] Run `PLAN` and `TODAY`, then confirm outbound messages show `sent` → `delivered` / `read` in the Status Dashboard or Django Admin **Message logs**.
- [ ] **Partner test:** forward the `wa.me` invite link to your second verified phone → tap `Accept`.
- [ ] **Keeping the 24h window open on the test number:** Because `WA_TEMPLATES_APPROVED=False` until custom templates are approved, any scheduled send when a person hasn't tapped anything in >24 hours is safely logged as `blocked (templates_unapproved_window_closed)` instead of hitting Meta error `132001`. Remind testers/partners to tap a button (`All Done`, `👏 Great job`, etc.) daily to keep their 24h window open.

| Symptom | Likely cause |
|---|---|
| Webhook verification fails | `WA_VERIFY_TOKEN` mismatch, missing trailing `/` in `/webhooks/whatsapp/`, or server/HTTPS down |
| `403` on webhook POST | Wrong `WA_APP_SECRET` (HMAC signature check failed) |
| Bot replies *"closed beta"* | Sender not listed in `BETA_ALLOWLIST_PHONES` (or `is_allowlisted` unchecked in Admin) |
| No reply at all | Webhook not subscribed to `messages`, sender not verified in Meta *API Setup*, or `WA_DRY_RUN=True` |
| Outbound `failed` code `190` / `401` | Token invalid/expired — use a Permanent System User Token |
| Outbound `failed` code `131030` | Recipient number not verified in Meta's test-number recipient list |
| Outbound `blocked`: `templates_unapproved_window_closed` | Person's 24h window closed while `WA_TEMPLATES_APPROVED=False`; have them send `hi` or tap a button |

---

## Part 5 – Cutting Over to Your Real Phone Number Later

1. Submit the 4 Utility templates (`partner_daily_report`, `quarter_boundary_reminder`, `user_daily_wrapup`, `weekly_report`) from `apps/messaging/templates.py` in Meta Manager.
2. Delete the WhatsApp account on your dedicated SIM (`09022603655`) if registered in the mobile app, register it to your WABA in Meta Business Manager (via SMS OTP), and add a payment method + update `RATE_CARD_JSON`.
3. Update `.env.prod` with the new `WA_PHONE_NUMBER_ID`, `WA_BOT_PHONE_NUMBER`, and `WA_TEMPLATES_APPROVED=True` (and turn off `BETA_ALLOWLIST_ENABLED=False` when ready for open users).
4. Recreate `web` and `worker`: `docker compose -f docker-compose.prod.yml up -d`.
