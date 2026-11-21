# Dayleey – Baileys Prototype Build Guide

*Throwaway prototype for validating the accountability mechanic with a handful of friendly testers. Not the production path.*

## 0. Read this first

Baileys is an **unofficial, reverse-engineered** WhatsApp client. Using it breaks WhatsApp's terms and the number can be banned at any time without warning. So this prototype follows hard rules:

1. **Dedicated SIM / number only.** Never your personal number.
2. **≤ 5 testers**, all of whom know they are testing and have agreed to receive messages.
3. **Allowlist only.** The bot ignores and never messages anyone not on the allowlist.
4. **No cold messages.** Partners join by messaging the bot first (via a `wa.me` link). The bot never initiates contact with a stranger.
5. **Low volume.** Hard daily cap per recipient, randomised delays, typing indicator.
6. **Pin the official package.** Never install forks. A poisoned Baileys fork has stolen auth tokens before.
7. **Disposable.** If the number is banned, you lose nothing but the SIM. Do not put any real users, payments or promises on this.
8. **Swappable.** All WhatsApp I/O sits behind one `Transport` interface so you can move to the official Cloud API later by changing config.

**Non-goals:** scale, payments, templates, buttons/lists, admin polish, multi-tenant.

---

## 1. What the prototype must answer

| Question | How you'll know |
|----------|----------------|
| Will people plan a day via chat? | % of testers completing PLAN on ≥ 4 of 7 days |
| Will they answer check-ins? | % of boundary check-ins answered |
| Will partners join and reply? | Partner join rate; partner reply rate to the daily report |
| Does the partner report change behaviour? | Completion rate with partner vs self-report mode |
| Is message volume sane? | Messages per user per day (target ≤ 10) |
| Is the flow annoying? | Opt-outs, STOP usage, tester feedback |

**Stop criteria:** number banned or throttled, fewer than half the testers still active after week 1, or partner reply rate under ~30%. Any of these means rethink the mechanic before spending money on the Cloud API.

---

## 2. Architecture

```
Tester / Partner  <->  WhatsApp
                          |
              Node "bridge" (Baileys)
              - holds the WhatsApp session
              - POST inbound messages  ->  Django
              - POST /send (queued, delayed) <- Django
                          |
              Django + DRF (the brain)
              - Person, Pairing, Priority, MessageLog
              - state machine, quarter rules, daily report
              - scheduler (management command)
                          |
                   SQLite / Postgres
```

Why a sidecar: Baileys is Node-only, your stack is Django. Keeping all logic in Django means the engine, rules and tests carry over unchanged to the Cloud API. Only the bridge changes.

Run everything on your laptop or a small always-on VPS. The Node bridge needs a **persistent session** (the QR link), so it cannot live on a sleeping free tier.

---

## 3. Repo layout

```
dayleey-proto/
  bridge/                 # Node
    package.json
    index.js
    auth/                 # Baileys session (gitignored, back it up)
  server/                 # Django
    manage.py
    dayleey/              # project
    core/
      models.py
      transport.py        # Transport interface + BaileysTransport
      views.py            # /bridge/inbound/
      engine.py           # state machine
      rules.py            # quarter rules
      scheduler.py
      management/commands/run_scheduler.py
  .env.example
  .gitignore              # auth/, .env, db.sqlite3
```

`.env` (shared by both): `BRIDGE_SECRET`, `BRIDGE_URL=http://127.0.0.1:3001`, `DJANGO_INBOUND_URL=http://127.0.0.1:8000/bridge/inbound/`, `ALLOWLIST=...` (comma-separated numbers, digits only).

---

## 4. The Node bridge

**Setup**

```bash
mkdir bridge && cd bridge
npm init -y
npm i baileys express pino qrcode-terminal
# set "type": "module" in package.json
```

Check the Baileys README for the current package name, export style and JID format before you start. These have changed between major versions. Pin an exact version in `package.json`, no `^`.

**`bridge/index.js`**

```js
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from 'baileys'
import qrcode from 'qrcode-terminal'
import express from 'express'
import pino from 'pino'

const SECRET = process.env.BRIDGE_SECRET
const INBOUND = process.env.DJANGO_INBOUND_URL
const ALLOW = new Set((process.env.ALLOWLIST || '').split(',').filter(Boolean))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let sock
const queue = []
let draining = false

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState('./auth')
  const { version } = await fetchLatestBaileysVersion()
  sock = makeWASocket({ version, auth: state, logger: pino({ level: 'warn' }) })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) qrcode.generate(qr, { small: true }) // scan with the dedicated phone
    if (connection === 'open') console.log('WhatsApp connected')
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode
      if (code === DisconnectReason.loggedOut) {
        console.error('Logged out. Delete ./auth and re-scan the QR.')
      } else {
        setTimeout(start, 5000) // reconnect
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return
    for (const m of messages) {
      if (!m.message || m.key.fromMe) continue
      const jid = m.key.remoteJid
      if (!jid || jid.endsWith('@g.us') || jid === 'status@broadcast') continue
      // Allowlist: ignore everyone else. Never reply to strangers.
      const number = jid.split('@')[0]
      if (ALLOW.size && !ALLOW.has(number)) continue
      const text =
        m.message.conversation || m.message.extendedTextMessage?.text || ''
      if (!text) continue
      try {
        await fetch(INBOUND, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Bridge-Secret': SECRET },
          body: JSON.stringify({ id: m.key.id, jid, text, ts: m.messageTimestamp }),
        })
      } catch (e) {
        console.error('forward failed', e.message)
      }
    }
  })
}

// Outbound: queued, typing indicator, randomised delays
async function drain() {
  if (draining) return
  draining = true
  while (queue.length) {
    const { jid, text } = queue.shift()
    try {
      await sock.sendPresenceUpdate('composing', jid)
      await sleep(800 + Math.random() * 1500)
      await sock.sendMessage(jid, { text })
      await sock.sendPresenceUpdate('paused', jid)
    } catch (e) {
      console.error('send failed', e.message)
    }
    await sleep(1500 + Math.random() * 2500)
  }
  draining = false
}

const app = express()
app.use(express.json())
app.post('/send', (req, res) => {
  if (req.get('X-Bridge-Secret') !== SECRET) return res.sendStatus(401)
  const { jid, text } = req.body
  const number = (jid || '').split('@')[0]
  if (ALLOW.size && !ALLOW.has(number)) return res.status(403).json({ error: 'not allowlisted' })
  queue.push({ jid, text })
  drain()
  res.json({ queued: true })
})
app.listen(3001, '127.0.0.1')

start()
```

Notes:
- Bind to `127.0.0.1` only. The bridge must never be publicly reachable.
- First run prints a QR. Scan it from the dedicated phone (Linked devices). Baileys also supports a pairing-code login if QR is awkward. Check the README.
- Newer Baileys versions may deliver anonymised IDs (`@lid`) instead of phone-number JIDs. For the prototype, treat the **JID string as the identity key** and don't assume it is a phone number. Capture the person's name in onboarding.
- Back up `./auth/`. Losing it means re-scanning the QR.

---

## 5. Django side

### 5.1 Models (minimal)

```python
class Person(models.Model):
    jid = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=80, blank=True)
    tz = models.CharField(max_length=40, default="Africa/Lagos")
    role = models.CharField(max_length=10, default="user")  # user/partner/both
    state = models.CharField(max_length=30, default="new")
    state_data = models.JSONField(default=dict)
    opted_out = models.BooleanField(default=False)
    last_inbound_at = models.DateTimeField(null=True)

class Pairing(models.Model):
    user = models.ForeignKey(Person, related_name="partners", on_delete=models.CASCADE)
    partner = models.ForeignKey(Person, null=True, related_name="partnering", on_delete=models.SET_NULL)
    token = models.CharField(max_length=12, unique=True)
    status = models.CharField(max_length=10, default="invited")  # invited/joined/removed
    mode = models.CharField(max_length=12, default="partner")    # partner/self_report

class Priority(models.Model):
    owner = models.ForeignKey(Person, on_delete=models.CASCADE)
    date = models.DateField()
    quarter = models.PositiveSmallIntegerField()  # 1-3 (Q4 off by default)
    title = models.CharField(max_length=140)
    status = models.CharField(max_length=12, default="planned")  # planned/done/partial/missed/unconfirmed

class MessageLog(models.Model):
    person = models.ForeignKey(Person, on_delete=models.CASCADE)
    direction = models.CharField(max_length=3)  # in/out
    wa_id = models.CharField(max_length=64, blank=True, db_index=True)
    text = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
```

### 5.2 Transport (the swap point)

```python
class Transport:
    def send(self, jid: str, text: str): ...

class BaileysTransport(Transport):
    def send(self, jid, text):
        if not under_daily_cap(jid):            # e.g. 12/day per recipient
            return
        requests.post(f"{settings.BRIDGE_URL}/send",
                      json={"jid": jid, "text": text},
                      headers={"X-Bridge-Secret": settings.BRIDGE_SECRET}, timeout=5)
        log_out(jid, text)
```

A later `CloudApiTransport` implements the same `send` (plus window/template logic). Nothing else changes.

### 5.3 Inbound view

```python
@csrf_exempt
def inbound(request):
    if request.headers.get("X-Bridge-Secret") != settings.BRIDGE_SECRET:
        return HttpResponse(status=401)
    d = json.loads(request.body)
    if MessageLog.objects.filter(wa_id=d["id"]).exists():   # dedupe
        return JsonResponse({"dup": True})
    p, _ = Person.objects.get_or_create(jid=d["jid"])
    p.last_inbound_at = timezone.now(); p.save(update_fields=["last_inbound_at"])
    MessageLog.objects.create(person=p, direction="in", wa_id=d["id"], text=d["text"])
    engine.handle(p, d["text"].strip())
    return JsonResponse({"ok": True})
```

Return fast. For five testers, processing inline is fine.

---

## 6. Conversation design (text-only, no buttons)

Interactive buttons and lists are unreliable on unofficial clients (my understanding, so verify). Use **numbered menus and keywords** everywhere:

```
How did Q1 go?
1 Done   2 Partial   3 Missed
```

**Commands (case-insensitive):** PLAN, TODAY, PARTNER, STATS, PAUSE, STOP, HELP, DELETE MY DATA.

**State machine sketch (`engine.handle`):**

| State | Expects | Next |
|-------|---------|------|
| `new` | any message | Welcome and consent text, ask name → `ask_name` |
| `ask_name` | name | Ask timezone (default Lagos) → `ask_tz` |
| `ask_tz` | city or "ok" | Ask active quarters (default Q1–Q3) → `ask_quarters` |
| `ask_quarters` | e.g. `1 2 3` | Ask partner choice → `ask_partner` |
| `ask_partner` | 1 (invite), 2 (self-report) | Send invite link text or set mode → `idle` |
| `idle` | `PLAN` | Start plan flow → `plan_q1` |
| `plan_qN` | priority title, or `next` | Next quarter, then validate → `confirm_plan` |
| `confirm_plan` | `yes` / `edit` | Save and schedule → `idle` |
| `checkin` | 1 / 2 / 3 | Record, ask reason if Missed → `idle` |

Global commands (STOP, HELP, PAUSE, DELETE MY DATA) are checked before state dispatch.

**Quarter rules** (`rules.py`, unit-test these first):
- ≤ 2 priorities per active quarter.
- Any two consecutive **active** quarters in a day total ≥ 2. Tell the user which pair is short.
- Q4 (00–06) off by default.

**Partner join flow (no cold messages):**
1. User picks "invite a partner". Bot replies with a ready-to-forward text containing a `wa.me/<bot number>?text=JOIN%20<token>` link.
2. Partner taps the link and sends `JOIN <token>`. This is the opt-in.
3. Bot (now allowed to reply) explains what they'll receive, and says STOP to leave. Mark the pairing `joined`.
4. The partner's number must also be on `ALLOWLIST`. Add testers' partners by hand for the prototype.

**Daily partner report (~21:30 user-local):**

```
📋 {user}'s day: 4 priorities — 3 done, 1 partial.
Reply 👏 to cheer, or NUDGE to send a reminder. STOP to leave.
```

---

## 7. Scheduler

A simple management command that wakes every 30–60 seconds. For five testers, no Celery needed.

```python
# core/management/commands/run_scheduler.py
class Command(BaseCommand):
    def handle(self, *a, **k):
        while True:
            scheduler.tick(now_utc=timezone.now())
            time.sleep(30)
```

`tick` for each active user, using `zoneinfo.ZoneInfo(p.tz)`:
- 06:00 / 12:00 / 18:00 local: merged boundary message (previous quarter check-in plus next quarter's priorities).
- 21:00 local: wrap-up and day summary; mark unanswered items `unconfirmed`.
- ~21:30 local: daily report to the partner, or a self-report prompt if no partner.

Make every job **idempotent** with a unique key (`user, date, event`) so restarts and double ticks don't double-send. Keep a `SentEvent` table or a unique constraint on `MessageLog`.

Keep a catch-up rule: on restart, send only events still relevant (for example, don't send a 06:00 reminder at 11:00).

---

## 8. Safety guardrails to build in

- Daily cap per recipient (≤ 12 outbound/day).
- Global kill-switch env flag (`PAUSE_ALL=1`) that stops all outbound.
- Quiet hours: no sends 22:30–05:30 local except replies to the user's own messages.
- STOP removes the person from all scheduling immediately and confirms once.
- No group messages, no media, no links beyond the join link.
- Log everything in `MessageLog` so you can count messages per user per day.
- Watch for **silent throttling**: delivered ticks missing, or inbound messages going quiet. If that happens, stop sending, wait, and slow down.

---

## 9. Build order (about 5 working days)

| Day | Goal | Done when |
|-----|------|-----------|
| 1 | Bridge connects; echo bot | You message the bot number and get your text back via Django |
| 2 | Models, inbound view, onboarding states | A tester completes onboarding |
| 3 | Plan flow plus rules with unit tests | Invalid plans are rejected with a clear message |
| 4 | Partner join via `wa.me` link; daily report; scheduler | A partner joins and receives a report at the right local time |
| 5 | Guardrails, logging, STOP/DELETE, dry run with 2 people | 48h run without double-sends |

---

## 10. Test plan

- **Unit:** quarter rules, timezone/DST boundaries (`zoneinfo`), state transitions, idempotent scheduler keys.
- **Manual:** full day with two phones (user and partner), STOP and re-opt-in, duplicate inbound delivery, bridge restart mid-day, Django down while bridge is up (bridge logs the failure and carries on).
- **Metrics from `MessageLog`:** messages/user/day, onboarding completion, check-in answer rate, partner join and reply rate, opt-outs.

---

## 11. Moving to the official API later

If the mechanic works:
1. Implement `CloudApiTransport` with window tracking (`last_inbound_at`) and template sends.
2. Replace numbered menus with buttons and lists where useful.
3. Replace JID identity with E.164 phone numbers (collect them in onboarding).
4. Add the opt-in, budget and kill-switch logic from the v2 plan.
5. Retire the Node bridge.

The Django engine, rules and tests should carry over as they are.

---

## 12. Troubleshooting

| Symptom | Likely cause / action |
|---------|----------------------|
| QR keeps reappearing | Session invalid. Delete `./auth` and re-scan. |
| Connected but no inbound | Check allowlist format (digits only, no `+`), and that JIDs aren't `@lid`. |
| Sends succeed but no delivery ticks | Possible throttling. Stop, wait, reduce volume. |
| Logged out repeatedly | Number flagged or linked-device limit. Use a fresh SIM, reduce activity. |
| Library breaks after a WhatsApp update | Check the Baileys issues page. Update only to an official release. |
