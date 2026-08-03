"""Reporting data models — the shapes the KPI engine produces and persists.

Three objects:
    Kpis             the 4 tracked KPIs' raw components for one window/platform
    PostPerformance  one post's metrics (for standout detection)
    KpiSnapshot      a Kpis reading stamped with when it was captured (history row)

The 4 KPIs (see progress.md) are stored as their RAW components, never as the
derived ratio — so history stays re-computable and auditable. Ratios are derived
on read via the properties below.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Kpis(BaseModel):
    """Raw KPI components for one date window on one platform."""

    platform: str = "instagram"
    since: date
    until: date

    # KPI 1 — non-follower reach %  (reach split by follow_type)
    reach_total: int = 0
    reach_follower: int = 0
    reach_non_follower: int = 0

    # KPI 2 — total views
    views: int = 0

    # KPI 3 — saves + shares per reach
    saves: int = 0
    shares: int = 0

    # KPI 4 — profile visits → product clicks
    profile_views: int = 0
    website_clicks: int = 0

    # ── derived ratios (never persisted; computed on read) ──
    @property
    def non_follower_reach_pct(self) -> float:
        base = self.reach_follower + self.reach_non_follower
        base = base or self.reach_total
        return 100.0 * self.reach_non_follower / base if base else 0.0

    @property
    def saves_shares_per_reach(self) -> float:
        """Per 1,000 reached — a readable scale for a small ratio."""
        return 1000.0 * (self.saves + self.shares) / self.reach_total if self.reach_total else 0.0

    @property
    def profile_to_click_pct(self) -> float:
        return 100.0 * self.website_clicks / self.profile_views if self.profile_views else 0.0


class PostPerformance(BaseModel):
    """One post's metrics, for ranking standouts within a window."""

    media_id: str
    caption: str = ""
    media_type: str = ""       # IMAGE / VIDEO / CAROUSEL_ALBUM
    product_type: str = ""     # FEED / REELS / AD / STORY
    permalink: str = ""
    timestamp: str = ""

    reach: int = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    saved: int = 0
    shares: int = 0
    total_interactions: int = 0

    @property
    def engagement(self) -> int:
        return self.likes + self.comments + self.saved + self.shares

    @property
    def saves_shares_per_reach(self) -> float:
        return 1000.0 * (self.saved + self.shares) / self.reach if self.reach else 0.0

    def short_caption(self, n: int = 80) -> str:
        cap = " ".join(self.caption.split())
        return cap if len(cap) <= n else cap[: n - 1].rstrip() + "…"


class KpiSnapshot(BaseModel):
    """A Kpis reading plus capture metadata — one row of long-term history."""

    captured_at: str          # ISO-8601 UTC
    kpis: Kpis


class Standout(BaseModel):
    """A post worth calling out, with the plain-English reason(s) it stood out."""

    post: PostPerformance
    reasons: list[str] = []


class Report(BaseModel):
    """Everything a weekly/monthly summary needs, ready to render for Slack."""

    label: str                       # "Weekly" / "Monthly" / custom
    platform: str = "instagram"
    since: date
    until: date
    kpis: Kpis
    previous: Kpis | None = None     # prior equal-length window, for deltas
    post_count: int = 0
    standouts: list[Standout] = []
