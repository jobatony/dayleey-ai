"""Approved Meta Utility template specifications & payload builders.

Submit these 4 templates early in Meta Business Manager (Utility category):
1. partner_daily_report
2. quarter_boundary_reminder
3. user_daily_wrapup
4. weekly_report (optional)
"""
from __future__ import annotations

TEMPLATE_SPECS = {
    "partner_daily_report": {
        "category": "UTILITY",
        "language": "en",
        "body": (
            "Dayleey daily update for {{1}} ({{2}}): {{3}}. "
            "Tap a quick reply below to cheer them on or send a gentle nudge."
        ),
    },
    "quarter_boundary_reminder": {
        "category": "UTILITY",
        "language": "en",
        "body": "Hi {{1}}, {{2}} check-in & {{3}} priorities: {{4}}. Reply here to update your progress.",
    },
    "user_daily_wrapup": {
        "category": "UTILITY",
        "language": "en",
        "body": "Hi {{1}}, daily wrap-up ({{2}}): {{3}}. Reply here to confirm any pending priorities or plan tomorrow.",
    },
    "weekly_report": {
        "category": "UTILITY",
        "language": "en",
        "body": "Hi {{1}}, your weekly Dayleey summary: {{2}} completed ({{3}}% rate), {{4}}-day streak.",
    },
}


def build_template_payload(template_name: str, body_params: list[str], language: str = "en") -> dict:
    components = []
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)[:1000]} for p in body_params],
            }
        )
    return {
        "name": template_name,
        "language": {"code": language},
        "components": components,
    }
