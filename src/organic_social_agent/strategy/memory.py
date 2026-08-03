"""Historical KPI context from Postgres — patterns beyond Meta's 90-day API limit.

Pulls stored KPI snapshots and summarises the trend direction for each of the
4 tracked KPIs so Claude can ground suggestions in longer-term performance data,
not just the current 30-day window.
"""

from __future__ import annotations

from loguru import logger


async def fetch_history_context(lookback_windows: int = 12) -> str:
    """Summarise stored KPI snapshots to expose performance trends over time."""
    try:
        from organic_social_agent.reporting.history import load_snapshots
        snapshots = await load_snapshots(platform="instagram")
    except Exception as exc:
        logger.warning("history fetch failed: {!r}", exc)
        return "(Historical KPI data unavailable)"

    if not snapshots:
        return "(No historical KPI snapshots stored yet — run a few weekly/monthly reports first)"

    recent = sorted(snapshots, key=lambda s: s.kpis.since)[-lookback_windows:]

    lines = [
        f"## Historical KPI snapshots (last {len(recent)} stored windows, oldest → newest)",
    ]
    for snap in recent:
        k = snap.kpis
        lines.append(
            f"• {k.since} → {k.until}: "
            f"views {k.views:,} | "
            f"NFR {k.non_follower_reach_pct:.1f}% | "
            f"resonance {k.saves_shares_per_reach:.2f} | "
            f"click-through {k.profile_to_click_pct:.1f}%"
        )

    # Trend signals — flag direction of each KPI across stored history
    if len(recent) >= 3:
        first, last = recent[0].kpis, recent[-1].kpis
        lines.append("")
        lines.append("KPI trend direction (first stored window vs latest):")
        for label, attr in [
            ("Views", "views"),
            ("Non-follower reach %", "non_follower_reach_pct"),
            ("Resonance (saves+shares/1k reach)", "saves_shares_per_reach"),
            ("Profile→click %", "profile_to_click_pct"),
        ]:
            fv = float(getattr(first, attr, 0) or 0)
            lv = float(getattr(last, attr, 0) or 0)
            if fv > 0:
                pct = 100.0 * (lv - fv) / fv
                arrow = "↑" if pct > 5 else ("↓" if pct < -5 else "→")
                lines.append(f"  {arrow} {label}: {pct:+.0f}%")

    return "\n".join(lines)
