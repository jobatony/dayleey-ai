# Dayleey AI – What Has Been Built (MVP Core, M1–M6)

*Status as of 2026-11-20. 19 automated tests passing. Runs fully locally in dry-run mode; not yet connected to a live Meta WhatsApp number.*

## 1. Summary
Dayleey AI is a rule-based (no LLM) WhatsApp accountability assistant. Users set up to 2 priorities per quarter of the day, and one accountability partner gets a daily report. Built with Django 5, DRF, `django-q2` (DB-backed jobs, no Redis) and the WhatsApp Cloud API client (with a dry-run mode).

## 2. Project layout
```
dayleey/settings/{base,dev,prod}.py   urls.py  wsgi.py
apps/
  accounts/   Person model, admin (CSV export)
  quarters/   rules.py (pure validators), models, services
  network/    Pairing model, invite/accept/fallback services
  messaging/  client, budget, templates, webhook, status, models
  engine/     router, states, scheduler, handlers/, management commands
  reports/    DailyReport, report formatting
tests/        test_rules.py, test_webhook_and_budget.py, test_flows_and_scheduler.py
Procfile  render.yaml  .env.example  pytest.ini  README.md
```

## 3. Quarter system (`apps/quarters`)
| Quarter | Local time | Default |
|---|---|---|
| Q1 | 06:00–12:00 | on |
| Q2 | 12:00–18:00 | on |
| Q3 | 18:00–24:00 | on |
| Q4 | 00:00–06:00 | **off** |

Rules enforced by `rules.validate_day()` (pure, no DB):
1. Max 2 priorities per quarter.
2. Any two consecutive **active** quarters need ≥2 priorities combined. Q4→Q1 only applies when Q4 is active and the whole day is being planned.
3. Mid-day planning: quarters that already started are locked; only remaining active quarters are validated. If only one remains, it needs ≥1 priority.
4. Inactive/locked quarters can't receive priorities.

Models: `QuarterConfig`, `Priority` (planned/done/partial/missed/unconfirmed + missed reason), `Checkin`.

## 4. Conversation engine (`apps/engine`)
`router.handle_inbound()` flow: dedupe by `wa_message_id` → upsert Person → log inbound → refresh 24h window → route.

Priority order: `STOP` → `DELETE MY DATA` (+confirm) → `JOIN-<token>` partner join → closed-beta allowlist → `RESUME` → opted-out notice → partner states → partner report replies → onboarding states → global commands (`HELP`, `PAUSE`, `PLAN`, `TODAY`, `PARTNER`, `STATS`) → active sub-flows (plan, check-in, wrap-up).

### Flows
- **Onboarding:** consent buttons → name → timezone list (Lagos/London/New York/Other IANA) → active quarters (Q1–Q3 or all 4) → partner choice (invite link or self-report).
- **Planning (`PLAN`):** choose today/tomorrow → per-quarter input (semicolon shortcut `A; B` for 2) → rule validation with specific error and "Fix" → confirm → saved.
- **Check-ins:** 2 priorities → `[All Done] [Review Each] [Missed Both]`; 1 priority → `[Done][Partial][Missed]`; Missed → reason list (Forgot / Ran out of time / Emergency / Other).
- **Wrap-up (21:00):** confirms unconfirmed items (`All Done / Review Each / Leave As Is`).
- **Commands:** `HELP`, `STATS` (7-day completion % and streak), `TODAY`, `PAUSE`/`RESUME`, `STOP`, `DELETE MY DATA`.

## 5. Partner network (`apps/network`, `engine/handlers/partner.py`)
- One partner per user. The user gets a `wa.me` link with prefilled text containing `JOIN-<TOKEN>`; the partner sending that message is the opt-in.
- Partner sees what they'll receive and taps `[Accept]` / `[Stop / Decline]`. User is notified.
- Partner-only users are offered mutual tracking (becomes role `both`).
- Self-pairing is rejected. Partner `STOP` or data deletion removes the pairing and tells the user.
- Invites with no partner after `PARTNER_FALLBACK_DAYS` (3) auto-switch the user to self-report mode.
- Partner replies to the daily report with 👏 / 👀 / 💪; reply is stored and forwarded to the user.

## 6. Scheduler (`engine/scheduler.py`)
`tick(now)` runs every minute via django-q2 (`setup_schedules`). All events are idempotent through a `ScheduledEvent` ledger (unique `event_key`).

| Event | When (user local) | Behaviour |
|---|---|---|
| Boundary message | 00/06/12/18:00 (first 5 min) | Merged check-in for the ended quarter + reminder for the next; skipped if nothing planned |
| 30-min nudge | 30 min after unanswered check-in | One text, only if window open |
| Unconfirmed | 2 h unanswered | Items become `unconfirmed` (never auto-missed) |
| Wrap-up | 21:00 | Day summary + confirm pending |
| Partner report | 21:30 | One report; deferred to 08:00 partner-local if in partner quiet hours |
| Self-report fallback | any tick | After 3 days without partner |
| Weekly report | Sun 19:00 | Only if `WEEKLY_REPORT_ENABLED` |

`catchup()` replays the last 90 minutes after a restart; idempotency prevents duplicates.

## 7. Messaging, compliance and cost control (`apps/messaging`)
- **Client:** `send_text/buttons/list/template`. Free-form is only sent inside the 24h window; otherwise it falls back to a utility template if provided, else is logged as `blocked (window_closed)`. Retries with backoff on 429/5xx/network.
- **Dry-run:** `WA_DRY_RUN=True` (default) never calls Meta; messages go to an in-memory outbox used by the simulator and tests.
- **Webhook:** `GET` verify challenge; `POST` verifies `X-Hub-Signature-256`, returns 200, hands payload to a django-q task. Status webhooks update logs and delivery counters (idempotent).
- **Budget (`budget.py`), checked before every send:** opt-out, quiet hours (22:00–06:00), global monthly USD budget kill-switch, per-user monthly cap (350), daily limits (10 user / 2 partner for proactive sends). Costs follow the Oct 2026 model: 1,000 free service messages/month, templates always billable. Rates come from `RATE_CARD_JSON`.
- **Templates specified:** `partner_daily_report`, `quarter_boundary_reminder`, `user_daily_wrapup`, `weekly_report` (must still be submitted to Meta).
- **Admin:** Person, Priority, Pairing, MessageLog, UsageCounter, ScheduledEvent, DailyReport etc.

## 8. Privacy
`DELETE MY DATA` wipes name, priorities, check-ins, reports, pairing and redacts message payloads; the person is opted out. Consent text is shown at onboarding.

## 9. Configuration (`.env`, see `.env.example`)
`WA_DRY_RUN`, `WA_PHONE_NUMBER_ID`, `WA_BOT_PHONE_NUMBER`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET`, `DATABASE_URL` (blank = SQLite), `MONTHLY_BUDGET_USD` (25), `USER_MONTHLY_MSG_CAP` (350), `RATE_CARD_JSON`, `PARTNER_FALLBACK_DAYS`, quiet hours, `BETA_ALLOWLIST_ENABLED`, `WEEKLY_REPORT_ENABLED`, `SENTRY_DSN`.

## 10. Management commands
| Command | Purpose |
|---|---|
| `simulate_chat [--phone]` | Interactive local WhatsApp simulator |
| `setup_schedules` | Registers the per-minute tick in django-q2 |
| `run_tick [--at ISO] [--catchup]` | Run scheduler once / simulate a time |

## 11. Not done / known gaps
- Meta business verification, real phone number, template approval and Privacy Policy page (M0).
- Live end-to-end test with real WhatsApp (webhook code is tested only with signed fixtures).
- Nigeria/Rest of Africa rate is a placeholder in `RATE_CARD_JSON`.
- Production deploy (M7) not performed; `render.yaml`/`Procfile` are untested.
- Weekly digest for partners, pricing/payments, richer admin dashboard.
- Quiet hours also apply to partner reports and nudges but boundary/wrap-up messages bypass them by design.
- Static files directory warning in tests is harmless (`collectstatic` not run).
