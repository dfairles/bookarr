# Copyright (C) 2024-2026 Bookarr Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_STATUS_LABELS = {
    "pending_approval": ("⏳", "Pending approval"),
    "sent": ("✅", "Sent to Listenarr"),
}


def _get_webhook_url() -> str:
    """Return the active webhook URL: DB value takes precedence over env var."""
    from app.database import SessionLocal
    from app.models import AppSetting

    db = SessionLocal()
    try:
        db_url = AppSetting.get(db, "discord_webhook_url")
    finally:
        db.close()
    return db_url or get_settings().discord_webhook_url


async def notify_new_request(
    *,
    title: str,
    author: str,
    cover_url: str,
    user_name: str,
    status: str,
) -> None:
    webhook_url = _get_webhook_url()
    if not webhook_url:
        return

    icon, status_label = _STATUS_LABELS.get(status, ("📬", status))
    description = f"**{author}**" if author else ""

    embed: dict = {
        "title": title,
        "description": description,
        "color": 0x5865F2,
        "fields": [
            {"name": "Requested by", "value": user_name, "inline": True},
            {"name": "Status", "value": f"{icon} {status_label}", "inline": True},
        ],
    }
    if cover_url:
        embed["thumbnail"] = {"url": cover_url}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"embeds": [embed]})
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Discord notification failed: %s", exc)


async def send_test_notification(webhook_url: str) -> str | None:
    """Send a test ping. Returns an error string on failure, None on success."""
    embed = {
        "title": "Bookarr notifications active",
        "description": "This webhook is correctly configured.",
        "color": 0x57F287,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"embeds": [embed]})
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return f"Discord returned {exc.response.status_code}"
    except Exception as exc:
        return str(exc)
    return None
