"""Meta (Instagram) webhook receiver — real-time @mention alerts.

Polling `/{ig-user-id}/tags` only returns feed/Reel photo-tags. Story @mentions
and caption @mentions are never in that list — Meta delivers them exclusively
through the `mentions` webhook topic, which is why this endpoint exists.

Flow:
  GET  /meta/webhook  — Meta subscription handshake (hub.challenge echo)
  POST /meta/webhook  — event delivery; verified via X-Hub-Signature-256 (HMAC
                        SHA-256 of the raw body keyed with the app secret), then
                        acked immediately and processed in the background.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import httpx
import truststore
from loguru import logger

from organic_social_agent.settings import settings

truststore.inject_into_ssl()


def verify_signature(body: bytes, header: str) -> bool:
    """Constant-time check of Meta's X-Hub-Signature-256 header."""
    if not settings.meta_app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.meta_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def verify_subscription(mode: str, token: str, challenge: str) -> str | None:
    """Return the challenge to echo back if the handshake is valid, else None."""
    if mode == "subscribe" and token and token == settings.meta_webhook_verify_token:
        logger.info("Meta webhook subscription verified")
        return challenge
    logger.warning("Meta webhook verification rejected (mode={!r})", mode)
    return None


async def _fetch_mentioned_media(media_id: str) -> dict | None:
    """Resolve a webhook media_id into the mentioning post/story details."""
    fields = (
        "mentioned_media.media_id(" + media_id + ")"
        "{id,caption,media_type,media_url,permalink,timestamp,username}"
    )
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{settings.meta_ig_user_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            params={"fields": fields, "access_token": settings.meta_access_token},
            timeout=15,
        )
    data = resp.json()
    if "error" in data:
        logger.warning("mentioned_media fetch failed for {}: {}", media_id,
                       data["error"].get("message", data["error"]))
        return None
    return data.get("mentioned_media")


async def _handle_mention(media_id: str) -> None:
    """Fetch the mentioning media and post a Slack alert."""
    media = await _fetch_mentioned_media(media_id)
    if not media:
        return

    username = media.get("username", "someone")
    permalink = media.get("permalink", "")
    caption = (media.get("caption") or "").replace("\n", " ")[:160]

    lines = [f"*\U0001f4e3 New @mention* — *@{username}* mentioned @heyharper"]
    if caption:
        lines.append(f"_{caption}_")
    if permalink:
        lines.append(permalink)

    if not (settings.slack_bot_token and settings.slack_channel_id):
        logger.warning("Slack not configured — dropping mention alert for {}", media_id)
        return

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            json={"channel": settings.slack_channel_id, "text": "\n".join(lines)},
            timeout=15,
        )
    if not resp.json().get("ok"):
        logger.error("mention alert post failed: {}", resp.json().get("error"))
    else:
        logger.info("mention alert posted for media {}", media_id)


def dispatch_event(body: bytes) -> None:
    """Parse a verified webhook payload and spawn handlers for mention events."""
    try:
        payload = json.loads(body)
    except ValueError:
        logger.warning("Meta webhook: unparseable body")
        return

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "mentions":
                continue
            value = change.get("value", {})
            media_id = value.get("media_id")
            if media_id:
                asyncio.create_task(_handle_mention(media_id))
