"""Central config, loaded from .env via pydantic-settings (PPB pattern)."""

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # ── Anthropic ──
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"          # reasoning / captioning
    anthropic_vision_model: str = "claude-haiku-4-5-20251001"  # bulk asset categorization

    # ── Meta / Instagram ──
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""
    meta_page_id: str = ""
    meta_ig_user_id: str = ""
    meta_api_version: str = "v21.0"

    # ── TikTok ──
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_access_token: str = ""
    tiktok_refresh_token: str = ""
    tiktok_redirect_uri: str = ""
    tiktok_business_id: str = ""
    tiktok_api_base: str = "https://open.tiktokapis.com"

    # ── Media hosting (S3 / Cloudflare R2) — Meta publish staging ──
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket: str = ""
    s3_region: str = "auto"
    s3_public_base_url: str = ""

    # ── Scheduler datastore ──
    database_url: str = ""

    # ── Content library ──
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_tenant_id: str = ""
    ms_drive_id_assets: str = ""
    onedrive_assets_base_path: str = ""
    figma_access_token: str = ""
    figma_file_key: str = ""

    # ── Competitor listening ──
    # Comma-separated IG handles (no @). Override via Railway env var.
    competitor_ig_handles: str = "gorjana,kinsleyarmelle,caitlynminimalist,puravidabracelets,mejuri"

    # ── Slack ──
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""             # #social-media-reporting (automated drops)
    slack_strategy_channel_id: str = ""    # #social-media-strategy (prompt-driven Q&A)

    # ── Library indexing (vision pass tuning — dev-side cost/quality knobs,
    #    NOT client config: see progress.md "Config convention") ──
    vision_image_max_px: int = 1024   # downscale long edge before the vision call
    vision_jpeg_quality: int = 80     # JPEG quality for compressed frames
    video_frame_samples: int = 5      # frames sampled per video for categorization
    pdf_max_pages: int = 8            # max PDF pages rasterized per creative

    # ── Meta webhooks ──
    # Set this to any secret string, then enter the same value in the Meta app
    # dashboard when subscribing to the mentions webhook topic.
    meta_webhook_verify_token: str = ""

    # ── Server ──
    server_port: int = 8767
    output_dir: str = "data"
    public_base_url: str = ""


settings = Settings()
