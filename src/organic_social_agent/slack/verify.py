"""Slack request signature verification (v0 scheme).

Every inbound Slack request — events, slash commands, interactivity — must be
verified before we trust the payload. Drop anything that fails.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request

from organic_social_agent.settings import settings


async def verify_slack_request(request: Request) -> bytes:
    """Raise 403 if the request wasn't signed by our Slack app.

    Returns the raw body so callers don't need to read it again.
    """
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    body = await request.body()

    if not timestamp or not signature:
        raise HTTPException(status_code=403, detail="Missing Slack signature headers")

    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=403, detail="Bad timestamp")

    # Replay-attack guard: reject anything older than 5 minutes
    if abs(time.time() - ts) > 300:
        raise HTTPException(status_code=403, detail="Request timestamp too old")

    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    return body
