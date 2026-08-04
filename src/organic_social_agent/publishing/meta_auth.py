"""Meta auth & token bootstrap — the part that works with just APP_ID + SECRET.

App credentials identify the *app*; they don't grant access to any account.
This module covers everything reachable before an account admin logs in, plus
the helpers that consume a user token once you have one:

    get_app_access_token()          app-level token (client_credentials)
    debug_token(token)              inspect any token's scopes / expiry / validity
    login_url(scopes)               the OAuth URL an admin opens to authorize
    exchange_for_long_lived(tok)    short-lived user token -> ~60-day token
    discover_page_and_ig(user_tok)  resolve META_PAGE_ID + META_IG_USER_ID

Run `uv run --system-certs meta-auth` to check credentials and, if a user token
is present in .env, print the Page ID and Instagram Business Account ID.
"""

from __future__ import annotations

import argparse
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import httpx
import truststore
from loguru import logger

from organic_social_agent.settings import settings

truststore.inject_into_ssl()

_GRAPH = "https://graph.facebook.com"

# Scopes the agent ultimately needs (reporting + publishing).
DEFAULT_SCOPES = [
    "instagram_basic",
    "instagram_manage_insights",   # KPI reporting
    "instagram_content_publish",   # publishing
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
]


def _client() -> httpx.Client:
    return httpx.Client(base_url=_GRAPH, timeout=30)


def get_app_access_token() -> str:
    """App-level token from client_credentials. Works with APP_ID+SECRET alone."""
    with _client() as c:
        r = c.get(
            "/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "grant_type": "client_credentials",
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


def debug_token(input_token: str) -> dict:
    """Inspect a token (scopes, expiry, validity) using the app token."""
    app_tok = get_app_access_token()
    with _client() as c:
        r = c.get(
            f"/{settings.meta_api_version}/debug_token",
            params={"input_token": input_token, "access_token": app_tok},
        )
        r.raise_for_status()
        return r.json().get("data", {})


def login_url(scopes: list[str] | None = None) -> str:
    """URL an account admin opens to authorize the app and mint a user token."""
    scopes = scopes or DEFAULT_SCOPES
    q = urllib.parse.urlencode(
        {
            "client_id": settings.meta_app_id,
            "redirect_uri": f"{settings.public_base_url}/meta/oauth/callback",
            "scope": ",".join(scopes),
            "response_type": "code",
        }
    )
    return f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth?{q}"


def exchange_for_long_lived(short_lived_user_token: str) -> dict:
    """Exchange a short-lived user token for a ~60-day long-lived one."""
    with _client() as c:
        r = c.get(
            f"/{settings.meta_api_version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_lived_user_token,
            },
        )
        r.raise_for_status()
        return r.json()  # {access_token, token_type, expires_in}


def discover_page_and_ig(user_token: str) -> dict:
    """From a user token, resolve the Page ID and linked IG Business Account ID."""
    with _client() as c:
        pages = c.get(
            f"/{settings.meta_api_version}/me/accounts",
            params={"access_token": user_token},
        )
        pages.raise_for_status()
        data = pages.json().get("data", [])
        if not data:
            raise RuntimeError("No Pages found for this user token.")
        out = []
        for page in data:
            ig = c.get(
                f"/{settings.meta_api_version}/{page['id']}",
                params={
                    "fields": "instagram_business_account",
                    "access_token": user_token,
                },
            ).json()
            out.append(
                {
                    "page_id": page["id"],
                    "page_name": page.get("name"),
                    "ig_user_id": (ig.get("instagram_business_account") or {}).get("id"),
                }
            )
        return {"pages": out}


def _fmt_expiry(ts: int | None) -> str:
    if not ts:
        return "never (long-lived / page token)"
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _find_env_file() -> Path:
    """Locate the project .env (cwd first, then walk up), for write-back."""
    here = Path(".env")
    if here.exists():
        return here.resolve()
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return here.resolve()


def _write_env_var(key: str, value: str) -> Path:
    """Replace (or append) KEY=value in .env, leaving every other line intact."""
    path = _find_env_file()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def check_token(token: str | None = None) -> dict:
    """Print a token's validity / app / scopes / expiry. Returns the debug data."""
    token = token or settings.meta_access_token
    if not token:
        logger.warning("No token to check (META_ACCESS_TOKEN is empty).")
        return {}
    d = debug_token(token)
    logger.info(
        "token: valid={} type={} app_id={} expires_at={} data_access_expires_at={}",
        d.get("is_valid"), d.get("type"), d.get("app_id"),
        _fmt_expiry(d.get("expires_at")), _fmt_expiry(d.get("data_access_expires_at")),
    )
    scopes = d.get("scopes") or []
    logger.info("scopes ({}): {}", len(scopes), ", ".join(sorted(scopes)))
    return d


def exchange_and_persist() -> None:
    """Exchange the current short-lived META_ACCESS_TOKEN for a long-lived one and
    write it back to .env. Prints lifetime before and after."""
    current = settings.meta_access_token
    if not current:
        logger.error("META_ACCESS_TOKEN is empty — paste a short-lived token first.")
        return

    logger.info("Before exchange:")
    try:
        check_token(current)
    except httpx.HTTPStatusError as exc:
        logger.warning("could not inspect current token ({}): continuing to exchange", exc)

    try:
        result = exchange_for_long_lived(current)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        logger.error("Exchange failed ({}). {}", exc.response.status_code, body)
        if "expired" in body.lower() or "session has expired" in body.lower():
            logger.error("The short-lived token already expired. Generate a fresh one in "
                         "Graph API Explorer (app 1983236249732383) and re-run --exchange.")
        elif "does not match" in body.lower() or "client_id" in body.lower():
            logger.error("APP_ID/SECRET in .env don't match the token's app. Align them and retry.")
        return

    new_token = result["access_token"]
    expires_in = result.get("expires_in")
    path = _write_env_var("META_ACCESS_TOKEN", new_token)
    logger.success(
        "Wrote long-lived META_ACCESS_TOKEN to {} (expires_in≈{} days).",
        path, round((expires_in or 0) / 86400, 1) if expires_in else "?",
    )
    logger.info("After exchange:")
    check_token(new_token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Meta auth: verify creds, check/exchange tokens.")
    parser.add_argument("--check", action="store_true",
                        help="show the current token's validity, scopes, and expiry")
    parser.add_argument("--exchange", action="store_true",
                        help="exchange the short-lived token for a ~60-day one and write it to .env")
    args = parser.parse_args()

    if args.exchange:
        exchange_and_persist()
        return
    if args.check:
        check_token()
        return

    # default diagnostic
    d = debug_token(get_app_access_token())
    logger.info("App token valid={} app_id={} name={}", d.get("is_valid"), d.get("app_id"), d.get("application"))
    if settings.meta_access_token:
        info = discover_page_and_ig(settings.meta_access_token)
        for p in info["pages"]:
            logger.info("Page {!r} id={} ig_user_id={}", p["page_name"], p["page_id"], p["ig_user_id"])
    else:
        logger.info("No META_ACCESS_TOKEN yet. Open this URL as a Page admin to authorize:")
        logger.info(login_url())


if __name__ == "__main__":
    main()
