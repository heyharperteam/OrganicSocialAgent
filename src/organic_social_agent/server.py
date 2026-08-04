"""FastAPI app — Slack bot surface + health + library viewer.

Endpoints:
    GET  /health                  liveness probe (Railway)
    GET  /library                 rendered content-library HTML page
    POST /slack/events            Events API: app_mention → strategy Q&A
    POST /slack/commands/report   /report slash command → KPI report
    POST /slack/commands/social   /social slash command → config / brief stubs
    POST /slack/interactive       Button callbacks (future: brief expand, etc.)
    GET  /meta/webhook            Meta subscription handshake
    POST /meta/webhook            Instagram @mention events (stories + captions)

All Slack endpoints verify the request signature before touching the payload.
The scheduler (weekly Mon 09:00 + monthly 1st 09:00) starts with the app.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import truststore
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from loguru import logger

from organic_social_agent.settings import settings
from organic_social_agent.slack.verify import verify_slack_request

truststore.inject_into_ssl()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from organic_social_agent.db import init_pool, close_pool, create_tables
    from organic_social_agent.slack.scheduler import start_scheduler, stop_scheduler
    await init_pool()
    await create_tables()
    start_scheduler()
    yield
    stop_scheduler()
    await close_pool()


app = FastAPI(title="Organic Social Agent", lifespan=_lifespan)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "organic-social-agent"}


# ── Library viewer ────────────────────────────────────────────────────────────

@app.get("/library", response_model=None)
async def library() -> FileResponse | HTMLResponse:
    """Serve the rendered content-library page. Regenerate with `index-library`."""
    page = Path(settings.output_dir) / "library" / "library.html"
    if not page.exists():
        return HTMLResponse(
            "<h1>Library not indexed yet</h1><p>Run <code>index-library</code> first.</p>",
            status_code=404,
        )
    return FileResponse(page, media_type="text/html")


# ── Slack: Events API ─────────────────────────────────────────────────────────

@app.post("/slack/events")
async def slack_events(request: Request):
    """Receive app_mention events from #social-media-strategy and dispatch to Claude."""
    from organic_social_agent.slack.events import handle_event
    body = await verify_slack_request(request)
    result = await handle_event(body)
    return JSONResponse(result)


# ── Slack: Slash commands ─────────────────────────────────────────────────────

@app.post("/slack/commands/report")
async def slack_report(request: Request):
    """/report — build and post an Instagram KPI report."""
    from organic_social_agent.slack.commands import handle_report
    await verify_slack_request(request)
    form = dict(await request.form())
    result = await handle_report(form)
    return JSONResponse(result)


@app.post("/slack/commands/social")
async def slack_social(request: Request):
    """/social — config, brief, calendar subcommands."""
    from organic_social_agent.slack.commands import handle_social
    await verify_slack_request(request)
    form = dict(await request.form())
    result = await handle_social(form)
    return JSONResponse(result)


# ── Meta webhooks ─────────────────────────────────────────────────────────────

@app.get("/meta/webhook", response_model=None)
async def meta_webhook_verify(request: Request):
    """Meta subscription handshake — echo hub.challenge if the token matches."""
    from organic_social_agent.slack.meta_webhook import verify_subscription
    q = request.query_params
    challenge = verify_subscription(
        q.get("hub.mode", ""), q.get("hub.verify_token", ""), q.get("hub.challenge", "")
    )
    if challenge is None:
        return JSONResponse({"error": "verification failed"}, status_code=403)
    return PlainTextResponse(challenge)


@app.post("/meta/webhook")
async def meta_webhook_event(request: Request):
    """Receive Instagram @mention events (stories + captions) in real time."""
    from organic_social_agent.slack.meta_webhook import dispatch_event, verify_signature
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        logger.warning("Meta webhook: bad signature")
        return JSONResponse({"error": "bad signature"}, status_code=403)
    dispatch_event(body)
    return JSONResponse({"ok": True})


# ── Slack: Interactivity ──────────────────────────────────────────────────────

@app.post("/slack/interactive")
async def slack_interactive(request: Request):
    """Button/select callbacks — stub until the strategy engine is built."""
    await verify_slack_request(request)
    logger.info("slack interactivity received (not yet handled)")
    return JSONResponse({"ok": True})


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import os
    import uvicorn
    # Railway injects PORT; fall back to settings value for local dev
    port = int(os.environ.get("PORT", settings.server_port))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
