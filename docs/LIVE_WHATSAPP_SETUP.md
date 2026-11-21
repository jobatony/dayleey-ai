# Live WhatsApp Test Setup (Meta test number + ngrok)

Goal: message Dayleey from a real phone while the server runs on your PC. No deployment, business verification or template approval needed.

> [!NOTE]
> Meta's dashboard changes often, so menu names may differ slightly. Check against https://developers.facebook.com/docs/whatsapp/cloud-api/get-started.

## Part 1 – Meta setup (≈20 min)
- [ ] Log in at https://developers.facebook.com with a Facebook account (register as a developer if prompted).
- [ ] **Create App** → use case **Other** / app type **Business** → name it `Dayleey Dev`.
- [ ] On the app dashboard, **Add product → WhatsApp → Set up**. Accept creating/using a Meta Business portfolio if asked (a test one is fine).
- [ ] Open **WhatsApp → API Setup**. Note down:
  - [ ] **Phone number ID** of the test number → `WA_PHONE_NUMBER_ID`
  - [ ] The **test phone number** itself (digits only, with country code) → `WA_BOT_PHONE_NUMBER`
  - [ ] **Temporary access token** → `WA_ACCESS_TOKEN` (expires in ~24 h)
- [ ] Under **Send and receive messages → To**, add your phone (and your partner-tester's phone, up to 5 total). Verify each with the code WhatsApp sends.
- [ ] From the same page, send the `hello_world` template to your phone. If it arrives, your token and number ID work.
- [ ] Open **App settings → Basic**, click **Show** next to **App secret** → `WA_APP_SECRET`.
- [ ] Choose any random string as your verify token → `WA_VERIFY_TOKEN`.

## Part 2 – Local config
- [ ] Copy the example file: `copy .env.example .env`
- [ ] Edit `.env`:
```
WA_DRY_RUN=False
WA_PHONE_NUMBER_ID=<from Meta>
WA_BOT_PHONE_NUMBER=<test number digits>
WA_ACCESS_TOKEN=<temporary token>
WA_VERIFY_TOKEN=<your random string>
WA_APP_SECRET=<from Meta>
BETA_ALLOWLIST_ENABLED=False
```
- [ ] Optional for testing: raise `USER_DAILY_MSG_LIMIT` and `MONTHLY_BUDGET_USD` if you plan many messages.
- [ ] Apply DB setup: `.venv\Scripts\python manage.py migrate`

## Part 3 – Tunnel
- [ ] Install ngrok (https://ngrok.com/download), sign up (free) and run `ngrok config add-authtoken <token>` once.
- [ ] Start the server in terminal 1:
```powershell
.venv\Scripts\python manage.py runserver 8000
```
- [ ] Start the tunnel in terminal 2:
```powershell
ngrok http 8000
```
- [ ] Copy the `https://....ngrok-free.app` URL.
- [ ] Add that host to `ALLOWED_HOSTS` in `.env` if it isn't covered (`.ngrok-free.app` is in the example already), then restart the server.
- [ ] Sanity check in a browser: `https://<ngrok-url>/healthz/` shows `{"status":"ok"...}`. If ngrok shows a warning page, accept it.

## Part 4 – Connect the webhook
- [ ] In Meta: **WhatsApp → Configuration → Webhook → Edit**.
- [ ] **Callback URL:** `https://<ngrok-url>/webhooks/whatsapp/`
- [ ] **Verify token:** exactly your `WA_VERIFY_TOKEN` → **Verify and save**. (Your server terminal should log a GET request. If it fails, check the token and that the server is running.)
- [ ] Under **Webhook fields**, **Subscribe** to `messages`.

## Part 5 – First live test
In dev settings queue tasks run inline, so you don't need `qcluster` for inbound messages.
- [ ] From your verified phone, send `hi` to the test number.
- [ ] Expect the welcome message with **I Agree / Not Now** buttons.
- [ ] Complete onboarding (consent → name → timezone → quarters → partner).
- [ ] Send `PLAN`, save a plan, then `TODAY`.
- [ ] In the admin (`createsuperuser` first) open **Message logs**: outbound rows should be `sent` and later `delivered`/`read` once Meta's status webhooks arrive. **Usage counters** should increase.
- [ ] **Partner test:** with the second verified phone, tap the invite link (or send the `JOIN-…` text) → Accept → check the user phone gets the notice.
- [ ] **Scheduled messages:** run `.venv\Scripts\python manage.py run_tick --at "<ISO time>"` to trigger a boundary/wrap-up/partner report now. Both phones must have messaged the bot within the last 24 hours, otherwise the send falls back to a template, which isn't approved on the test number and will fail or be blocked.

## Part 6 – Troubleshooting
| Symptom | Likely cause |
|---|---|
| Webhook verification fails | Token mismatch, server or ngrok not running, wrong path (needs trailing `/webhooks/whatsapp/`) |
| 403 in server log on POST | Wrong `WA_APP_SECRET` (signature check) |
| No reply to `hi` | Webhook not subscribed to `messages`, sender not on the verified recipient list, or `WA_DRY_RUN` still `True` |
| Outbound `failed`, code 190/401 | Token expired (24 h), generate a new one and restart |
| Outbound `failed`, code 131030 | Recipient not on the allowed list |
| Outbound `failed` for templates (132001) | Template not approved or doesn't exist on this account |
| Messages `blocked` in admin | Read `error_code`: `window_closed`, `quiet_hours`, `daily_rate_limit`, `global_budget_exceeded` |
| ngrok URL changed | Free URLs change on restart; update the callback URL in Meta |

## Part 7 – When done
- [ ] Set `WA_DRY_RUN=True` again for local simulation.
- [ ] Keep notes on any differences between Meta's real payloads and the test fixtures; fix the parsing in `apps/engine/router.py` or `apps/messaging/status.py` if needed.
- [ ] Next for launch: business verification, real number, System User permanent token, template submission, Privacy Policy, Nigeria rate card.
