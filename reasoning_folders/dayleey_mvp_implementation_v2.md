# Dayleey AI – MVP Implementation Plan (v2)

*Revised 2026-11-20. Supersedes v1 (2026-11-19).*

## 0. What changed from v1 and why

| # | Change | Reason |
|---|--------|--------|
| 1 | Free-tier assumptions rewritten | Meta's pricing changed on Oct 1, 2026. Service (free-form) messages are now billable after 1,000 free delivered messages per phone number per month. Utility templates inside the 24h window are no longer free. "1,000 free conversations" no longer exists. |
| 2 | Partner accountability reduced to **one daily report to one partner** | The quarterly design sent 3–6 partner messages a day. That is costly, spam-prone and likely to hurt number quality. Quarters remain as the user's own planning structure. |
| 3 | Partner invite is now a **partner-initiated `wa.me` link**, not a cold template | Meta requires opt-in before messaging. A cold invite to a non-opted-in person is the main policy and quality risk. |
| 4 | **Partners must join** (lightweight, free role) | Reason is compliance, engagement and growth, not saving money. Window-trick savings largely disappeared on Oct 1. |
| 5 | Mutual pairing and self-report fallback added | Mutual pairs keep windows open and give both people a reason to stay. The fallback avoids churn at signup. |
| 6 | Message budget model and delivered-message counter added | Cost is per delivered message, so budgeting must be per message, not per template. |
| 7 | Hosting simplified | Per-minute Celery plus Redis does not fit free tiers. Use one process and a DB-backed job table. |
| 8 | "Auto-missed" becomes "unconfirmed" | Punishing users who are asleep or in meetings is harsh. |
| 9 | Templates cut from 7 to 4 | Fewer templates mean faster approval and less to maintain. |

---

## 1. Decisions

| # | Topic | Decision |
|---|-------|----------|
| 1 | Channel | Official WhatsApp Cloud API (Meta), approved utility templates, explicit opt-in, user-initiated check-ins to keep the 24h window open where possible |
| 2 | Quarters | Fixed 6h blocks in the user's timezone: Q1 06–12, Q2 12–18, Q3 18–24, Q4 00–06. Max 2 priorities per active quarter. Any two consecutive **active** quarters need ≥2 total. Q4 defaults to **off**. |
| 3 | Free MVP | Closed beta, hard message budget, global kill-switch, paid plan later. "Free" holds only for ~3 users (see §3). |
| 4 | Interaction | Rule-based state machine, buttons/lists, regex/keywords. No LLM. |
| 5 | Partners | **One partner per user** in v1. Partner joins by tapping a `wa.me` link and sending the first message (this is the opt-in). STOP honoured. Partner's reply to the daily report is the verification signal. |
| 6 | Cadence | User: merged boundary message (check-in for the quarter that ended plus reminder for the one starting) at 06:00, 12:00 and 18:00, plus a 21:00 daily wrap-up. Partner: **one daily report**. Weekly streak report is optional. |
| 7 | Stack | Django + DRF webhook, PostgreSQL (non-expiring free tier such as Neon or Supabase), single worker process with DB-backed job table (django-q or APScheduler). No Redis or Celery for the MVP. Render/Railway, ngrok for dev. |

---

## 2. Is WhatsApp suitable? Compliance

Suitable: it is where users and partners already are.

- **24h customer-service window.** Free-form messages are allowed only within 24h of the recipient's last message. Outside it, only approved templates are allowed.
- **Opt-in for every recipient, including partners.** Partners opt in by messaging the bot first through the user-shared link. The bot sends nothing to a partner who has not done this.
- **Template category.** Use Utility for transactional content with variables. Avoid promotional wording. Meta may recategorise, so budget for Marketing-rate fallbacks in the cost model.
- **Quality rating.** Honour STOP immediately. Rate-limit per recipient (target ≤10 messages/day per user, 1–2 per day per partner). Monitor block/report rates.
- **Keeping windows open.**
  - User: every check-in asks for a button tap, which opens a new 24h window.
  - Partner: the daily report invites a one-tap reply (👏 / Seen / Nudge). A reply is not guaranteed, so assume the partner report is often a template.
- **Fallbacks.**
  - Template send fails or the partner never joins: tell the user and suggest the self-report fallback or another partner.
  - Partner number is not on WhatsApp: same handling.
- **Requires:** Meta Business account, business verification, a dedicated phone number, Privacy Policy URL.
- **Privacy.** A user's priorities go to a third party. Get the user's explicit consent in onboarding and tell the partner what they will receive when they join. Check Nigeria's data protection law (NDPA) for handling partner phone numbers.

---

## 3. Can it run free? Conditions

### 3.1 Meta pricing (as of Oct 1, 2026)

- Each business phone number gets **1,000 free delivered service messages per month**. It resets monthly and does not roll over.
- Free-form (service) messages after the 1,000 are charged.
- Utility templates are charged, including when sent inside the 24h window.
- Inbound messages from users are free.
- Rates vary by country. Examples seen: about $0.0068 (Brazil) and $0.0014 (India) per message. **Nigeria / Rest of Africa rate not confirmed: look it up on Meta's rate card before launch.**

### 3.2 Message budget per active user per day (target)

| Item | Messages/day |
|------|-------------|
| Plan-day flow (prompt, priorities, confirm) | ~4 |
| Boundary messages at 06/12/18 | 3 |
| 21:00 wrap-up (includes day summary) | 1 |
| Bot acknowledgements to taps | ~1 |
| Partner daily report | 1 |
| **Total** | **~10** |

That is about **300 messages/month per user**. Treat 250–400 as the working range.

- The free 1,000 covers roughly **3 users**.
- A 50-user beta is about 15,000 messages. At the two example rates that is roughly **$20–$100/month**. Recompute with the Nigeria rate.
- v1's design (about 15–25 messages/day) cost roughly double this.

### 3.3 Free-tier hosting

- Free web services sleep and free Postgres tiers can expire. Check current limits.
- Use a non-expiring free Postgres and an always-on worker (cheap paid hobby tier), or an external cron pinging a `/tick` endpoint every minute with a catch-up job on restart.

### 3.4 Conditions

- Closed beta (≤50 users).
- Per-user monthly message cap, enforced before every send.
- 1 partner per user. Partner messages count toward the cap.
- Hard global monthly budget kill-switch, driven by **delivered** message counts from status webhooks.
- Paid plan later (see §13).

---

## 4. What else the MVP needs

Onboarding and consent, timezone capture, STOP/HELP/PAUSE commands, idempotent webhook processing, message audit log, scheduler reliability, admin dashboard, privacy (`DELETE MY DATA`), cost/usage tracking, analytics (completion rate per quarter, partner reply rate).

---

## 5. Architecture

```
User / Partner  <->  WhatsApp Cloud API  <->  Django + DRF webhook
                                                  |
                                    Conversation state machine
                                                  |
                          PostgreSQL  <->  Worker (DB-backed jobs, per-minute tick)
                                                  |
                                             Django Admin
```

Django apps: `accounts` (Person), `quarters` (priorities and rules), `network` (pairings), `messaging` (WhatsApp client, templates, logs, budget), `engine` (state machine, scheduler jobs), `reports` (daily report).

---

## 6. Data model

- **Person:** phone (E.164, unique), name, timezone, role (user/partner/both), opted_in, opted_out, last_inbound_at, window_expires_at, plan, state (JSON/enum).
- **Pairing:** user → Person, partner → Person (nullable), status (invited/joined/declined/removed), mutual (bool), invite_token, joined_at, mode (partner/self-report).
- **Priority:** owner → Person, date, quarter (1–4), title, status (planned/done/partial/missed/unconfirmed).
- **QuarterConfig:** person, quarter, active (bool), start/end local time. Q4 default off.
- **Checkin:** priority or boundary, kind (boundary/wrapup), sent_at, response, responded_at.
- **DailyReport:** user, date, summary (counts), sent_at, partner_reply (ack/nudge/none).
- **MessageLog:** person, direction, wa_message_id (unique), type (service/utility template), template_name, payload, status (sent/delivered/read/failed), billable (bool), est_cost.
- **UsageCounter:** phone number (business), month, delivered_service, delivered_template, est_cost; plus per-user monthly counts.

---

## 7. Business rules

1. ≤2 priorities per active quarter.
2. For every pair of consecutive **active** quarters in a day, total ≥2 (validated at "Confirm day plan", and the user is told which pair is short). Q4→Q1 wraps across days only if both are active.
3. **One partner per user**, partner ≠ self. A partner can cover many users.
4. Plans are set for today or tomorrow (editable until the quarter starts).
5. Partner messages only after the partner has messaged the bot (opted in). STOP removes the pairing and informs the user.
6. Monthly message cap and the global budget are enforced before **every** send, not only templates.
7. Unanswered check-ins become **unconfirmed** after 2h, not missed. The user can correct them until the 21:00 wrap-up.

---

## 8. Conversation flows

- **Onboarding:** opt-in → name → timezone → active quarters → partner choice → how it works.
- **Partner choice:**
  1. Bot gives the user a `wa.me` link with prefilled text for the partner (for example: "Hi, I'm joining {user} on Dayleey as their accountability partner").
  2. The partner taps it and sends the message, which is the opt-in.
  3. The bot replies once explaining what they will receive, with [Accept] and [Stop].
  4. Optional: "Want to track your own goals too?" → become a user (mutual pairing).
- **Self-report fallback:** if no partner joins within N days, the user continues in self-report mode with periodic prompts to invite someone.
- **Plan day:** per active quarter: add priority → add another or next → validation → confirm.
- **Check-in:** [Done] [Partial] [Missed]. On Missed, ask one reason (Forgot / Time / Emergency / Other).
- **Commands:** PLAN, TODAY, PARTNER, STATS, PAUSE, STOP, HELP, DELETE MY DATA.

---

## 9. Scheduler (per-minute tick, DB-backed jobs)

| Event | To | Message |
|-------|----|---------|
| 06:00 / 12:00 / 18:00 local | User | Merged: check-in for the ended quarter plus priorities for the next (free-form if in window, else template) |
| 21:00 local | User | Wrap-up and day summary, confirm unconfirmed items |
| ~21:30 local | Partner | **Daily report** (template if the window is closed). Invites a one-tap reply. |
| Sunday | User (+ partner, optional) | Weekly streak report (optional, only if budget allows) |

Rules:
- Unanswered check-in: one reminder after 30 min, then mark unconfirmed after 2h.
- Jobs are idempotent (unique key per event). A catch-up job on restart sends only events still relevant.
- Before any send: check cap, global budget, opt-in and quiet hours.

---

## 10. API / modules

- `GET/POST /webhooks/whatsapp/`: verify token and HMAC signature (`X-Hub-Signature-256`), dedupe by `wa_message_id`, respond 200 fast, process in the worker.
- `messaging/client.py`: `send_text`, `send_buttons`, `send_list`, `send_template`. Chooses service vs template from `window_expires_at`, checks the budget first, and logs `billable` and `est_cost`.
- `messaging/budget.py`: counts delivered messages from status webhooks and trips the kill-switch.
- `engine/handlers.py`: state → handler map. `engine/rules.py`: quarter validators (unit-tested).
- Status webhooks update `MessageLog` and usage counters.

---

## 11. Templates to submit early (Utility)

1. `partner_daily_report`: summary of the user's day for the partner, with a reply prompt.
2. `quarter_boundary_reminder`: for users whose window is closed.
3. `user_daily_wrapup`: for users whose window is closed.
4. `weekly_report` (optional).

The partner invite is no longer a template. Submit in M1 because approval takes time. Keep wording transactional and keep all variables meaningful.

---

## 12. Milestones

0. **M0 – Prerequisites (parallel, start day 1):** Meta business verification, phone number, Privacy Policy, Nigeria/Rest-of-Africa rate lookup, budget model signed off.
1. **M1 – Foundations (2–3 days):** Django project, models, admin, Meta app and test number, webhook verification/signature, ngrok, **submit templates**.
2. **M2 – Messaging core (3 days):** WhatsApp client, logging, budget counters, state machine skeleton, onboarding.
3. **M3 – Planning and rules (3 days):** plan-day flow, quarter rule validation and tests.
4. **M4 – Partner network (3 days):** `wa.me` join flow, pairing, STOP, mutual-pair offer, self-report fallback.
5. **M5 – Scheduler and daily report (3–4 days):** DB-backed jobs, merged boundary messages, wrap-up, partner daily report, catch-up job.
6. **M6 – Limits and hardening (3 days):** caps, kill-switch, rate limits, retries, privacy commands, logging/Sentry.
7. **M7 – Deploy and beta (2 days):** Render/Railway, env config, 10–20 testers.

Total is the same ~20 working days. Verification and template approval may add calendar time.

---

## 13. Business model notes

- **Who pays:** the person being held accountable. Partners are free.
- **Margin:** at ~300 messages/month per user, cost is on the order of $0.40–$2 depending on the country rate. A $2–5 subscription leaves a margin only if usage stays near the budget.
- **Growth loop:** each partner is a warm lead. Mutual pairs bring two users per invite.
- **Risks:**
  - Meta's rates have just changed once and could change again.
  - A Telegram bot has no per-message fee, so competitors can undercut.
  - Willingness to pay locally is unvalidated.
- **Upsell candidates:** quarterly partner pings, partner weekly digest, extra partners.

---

## 14. Testing and success metrics

- **Unit:** quarter rules, timezone boundaries/DST, state transitions, service-vs-template selection, budget and cap enforcement.
- **Integration:** webhook replay, duplicate delivery, failed sends, partner join via link, STOP flow.
- **Metrics:** onboarding completion, % boundary check-ins answered, **partner join rate within 3 days**, **partner reply rate to the daily report**, priority completion rate, **delivered messages and cost per active user per month**, opt-out rate, number quality rating.

---

## 15. Risks

| Risk | Mitigation |
|------|-----------|
| Message costs exceed the free tier | Budget model, per-user caps, kill-switch on delivered counts, closed beta |
| Meta pricing changes again | Isolate pricing in config, review the rate card monthly |
| Template rejection or recategorisation to Marketing | Utility wording, early submission, cost model includes the Marketing rate |
| Partner sees it as spam | Partner-initiated opt-in, clear identity, STOP, max 1 report/day |
| Partners never join | Self-report fallback, nudges, mutual-pair offer |
| Free-tier sleep or expiry misses reminders | Always-on worker or external cron ping, catch-up job, non-expiring Postgres |
| Number quality downgrade | Rate limits, monitor quality rating |
| Privacy complaint (third-party data) | Explicit user consent, partner told what they receive, `DELETE MY DATA` |
| Rules too rigid | Quarter "off" toggle, rule override once a week |

---

## 16. Open questions

1. Nigeria / Rest of Africa per-message rate: what is it, and does it change the price point?
2. Pricing after beta: flat monthly, or tiered by message volume?
3. Should partners get an optional weekly digest (paid)?
4. Mutual pairs: should the app encourage them by default?
5. How many days before a partner-less user is moved to self-report mode?
