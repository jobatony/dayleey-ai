"""Interactive dry-run WhatsApp simulator for testing Dayleey locally without Meta credentials."""
from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.engine.router import InboundMessage, handle_inbound, normalize_phone
from apps.engine.scheduler import tick
from apps.messaging.client import WhatsAppClient, pop_dry_run_outbox


class Command(BaseCommand):
    help = "Interactive local WhatsApp chat simulator (dry-run mode)."

    def add_arguments(self, parser):
        parser.add_argument("--phone", default="+2348011111111", help="Initial simulated phone number")

    def handle(self, *args, **options):
        client = WhatsAppClient(dry_run=True)
        current_phone = normalize_phone(options["phone"])
        last_buttons: list[tuple[str, str]] = []
        last_rows: list[tuple[str, str, str]] = []
        sim_now: datetime | None = None

        self.stdout.write(
            self.style.SUCCESS(
                f"\n=== {settings.BOT_DISPLAY_NAME} Local WhatsApp Simulator ===\n"
                f"Active phone: {current_phone}\n"
                "Commands:\n"
                "  /as <phone>             Switch phone (e.g. /as +2348022222222 for partner)\n"
                "  /tick YYYY-MM-DD HH:MM  Trigger scheduler tick at local WAT (+01:00) or ISO time\n"
                "  1, 2, 3...              Tap a numbered button/list item from the last message\n"
                "  /quit                   Exit simulator\n"
            )
        )

        while True:
            try:
                raw = input(f"\n[{current_phone}] > ").strip()
            except (EOFError, KeyboardInterrupt):
                self.stdout.write("\nBye!")
                break

            if not raw:
                continue
            if raw.lower() in {"/quit", "/exit"}:
                break

            if raw.startswith("/as "):
                current_phone = normalize_phone(raw[4:].strip())
                last_buttons, last_rows = [], []
                self.stdout.write(self.style.WARNING(f"Switched active phone to {current_phone}"))
                continue

            if raw.startswith("/tick "):
                ts = raw[6:].strip()
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=ZoneInfo(settings.DEFAULT_TIMEZONE))
                    sim_now = dt
                    res = tick(now=sim_now, client=client)
                    self.stdout.write(self.style.WARNING(f"Tick @ {sim_now.isoformat()} -> {res}"))
                    last_buttons, last_rows = self._flush_outbox(current_phone)
                except ValueError as exc:
                    self.stdout.write(self.style.ERROR(f"Invalid datetime: {exc}"))
                continue

            button_id = ""
            text = raw
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(last_buttons):
                    button_id, text = last_buttons[idx]
                elif 0 <= idx < len(last_rows):
                    button_id, text, _ = last_rows[idx]

            msg = InboundMessage(
                wa_message_id=f"sim_{uuid.uuid4().hex[:12]}",
                phone=current_phone,
                text=text,
                button_id=button_id,
            )
            handle_inbound(msg, client=client, now=sim_now)
            last_buttons, last_rows = self._flush_outbox(current_phone)

    def _flush_outbox(self, active_phone: str) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        msgs = pop_dry_run_outbox()
        buttons_for_active: list[tuple[str, str]] = []
        rows_for_active: list[tuple[str, str, str]] = []
        for m in msgs:
            header = f"\n--- [BOT -> {m['to']}] ({m['kind']}) ---"
            self.stdout.write(self.style.HTTP_INFO(header))
            if m["kind"] == "template":
                self.stdout.write(f"[TEMPLATE: {m['template_name']}] params={m['template_params']}")
            else:
                self.stdout.write(m["body"])
            if m["buttons"]:
                for i, (bid, label) in enumerate(m["buttons"], start=1):
                    self.stdout.write(f"  [{i}] {label}  (id={bid})")
                if m["to"] == active_phone:
                    buttons_for_active = m["buttons"]
            if m["list_rows"]:
                for i, (rid, title, desc) in enumerate(m["list_rows"], start=1):
                    self.stdout.write(f"  [{i}] {title} — {desc}  (id={rid})")
                if m["to"] == active_phone:
                    rows_for_active = m["list_rows"]
        return buttons_for_active, rows_for_active
