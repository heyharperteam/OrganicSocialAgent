"""Render the library index as a self-contained HTML page, grouped by vertical.

Written to {output_dir}/library/library.html on every index run. Self-contained
(inline CSS, no external assets) so it can be served by the FastAPI `/library`
route and linked straight into Slack — Paul/Barbara open one URL and browse the
whole postable library, grouped by product vertical.
"""

from __future__ import annotations

import html
from collections import defaultdict

from organic_social_agent.library.schema import AssetDescription

# Order verticals sensibly; anything unlisted is appended alphabetically.
_VERTICAL_ORDER = [
    "charms", "chains", "rings", "earrings", "bracelets", "necklaces",
    "watches", "mixed", "other",
]

_FORMAT_LABEL = {
    "carousel": "Carousel", "reel": "Reel", "story": "Story", "tiktok_video": "TikTok",
}
_TYPE_LABEL = {"campaign": "Campaign", "ugc": "UGC", "series": "Series"}


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _chip(text: str, kind: str = "") -> str:
    cls = f"chip chip--{kind}" if kind else "chip"
    return f'<span class="{cls}">{_esc(text)}</span>'


def _card(d: AssetDescription) -> str:
    formats = "".join(_chip(_FORMAT_LABEL.get(f, f), "fmt") for f in d.best_format) or "—"
    styles = "".join(_chip(t) for t in d.style_tags) or "—"
    colors = "".join(
        f'<span class="chip chip--color">{_esc(c)}</span>' for c in d.dominant_colors
    )
    detected = (
        f'<blockquote class="detected">“{_esc(d.detected_text)}”</blockquote>'
        if d.text_overlay and d.detected_text else ""
    )
    type_chip = _chip(_TYPE_LABEL.get(d.content_type_signal, d.content_type_signal or "—"), "type")
    kind_chip = _chip(d.media_kind.upper(), "kind")
    pillar = f' · <span class="muted">{_esc(d.suggested_pillar)}</span>' if d.suggested_pillar else ""
    return f"""
    <article class="card">
      <header class="card__head">
        <h3 class="card__title" title="{_esc(d.name)}">{_esc(d.name)}</h3>
        <div class="badges">{kind_chip}{type_chip}</div>
      </header>
      <div class="row"><span class="lbl">Best for</span><span class="vals">{formats}</span></div>
      <div class="row"><span class="lbl">Scene</span><span class="vals">{_esc(d.people)} · {_esc(d.setting)}{pillar}</span></div>
      <div class="row"><span class="lbl">Style</span><span class="vals">{styles}</span></div>
      {'<div class="row"><span class="lbl">Colors</span><span class="vals">' + colors + '</span></div>' if colors else ''}
      {detected}
      <p class="caption">{_esc(d.caption)}</p>
      <footer class="card__foot"><code>{_esc(d.rel_path)}</code></footer>
    </article>"""


def _sorted_verticals(keys: list[str]) -> list[str]:
    known = [v for v in _VERTICAL_ORDER if v in keys]
    extra = sorted(k for k in keys if k not in _VERTICAL_ORDER)
    return known + extra


def render_html(records: list[AssetDescription], *, base_path: str, generated_at: str) -> str:
    groups: dict[str, list[AssetDescription]] = defaultdict(list)
    for d in records:
        groups[(d.vertical or "other").lower()].append(d)

    verticals = _sorted_verticals(list(groups.keys()))
    total = len(records)

    nav = "".join(
        f'<a href="#v-{_esc(v)}">{_esc(v.title())} <span class="count">{len(groups[v])}</span></a>'
        for v in verticals
    )

    sections = []
    for v in verticals:
        cards = "".join(
            _card(d) for d in sorted(groups[v], key=lambda x: x.rel_path.lower())
        )
        sections.append(f"""
      <section id="v-{_esc(v)}" class="vertical">
        <h2 class="vertical__title">{_esc(v.title())} <span class="count">{len(groups[v])}</span></h2>
        <div class="grid">{cards}</div>
      </section>""")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hey Harper — Content Library</title>
<style>
  :root {{
    --bg:#faf8f5; --panel:#ffffff; --ink:#1c1a17; --muted:#6f6a63;
    --line:#e9e4dc; --accent:#b0885a; --chip:#f1ece4; --chipink:#544c40;
    --fmt:#e7efe9; --fmtink:#2f5c44; --type:#efe7f2; --typeink:#5a3f66;
    --kind:#efe9e2; --kindink:#6b5b45; --shadow:0 1px 2px rgba(0,0,0,.05),0 4px 16px rgba(0,0,0,.04);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg:#17150f; --panel:#201d16; --ink:#f0ece5; --muted:#a49c8f;
      --line:#332e25; --accent:#d0a878; --chip:#2b271f; --chipink:#cabfae;
      --fmt:#1f2f26; --fmtink:#9fd3b3; --type:#2a2130; --typeink:#c8a9d6;
      --kind:#2a2419; --kindink:#d6c3a0; --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.3);
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
  header.top {{ position:sticky; top:0; z-index:5; background:var(--bg);
    border-bottom:1px solid var(--line); padding:18px 24px 12px; }}
  h1 {{ margin:0 0 2px; font-size:20px; letter-spacing:.2px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  nav {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }}
  nav a {{ text-decoration:none; color:var(--chipink); background:var(--chip);
    padding:4px 10px; border-radius:999px; font-size:13px; border:1px solid var(--line); }}
  nav a:hover {{ border-color:var(--accent); color:var(--ink); }}
  nav .count, .vertical__title .count {{ color:var(--accent); font-weight:600; }}
  main {{ padding:8px 24px 48px; max-width:1280px; margin:0 auto; }}
  .vertical {{ margin-top:28px; scroll-margin-top:120px; }}
  .vertical__title {{ font-size:17px; margin:0 0 12px; padding-bottom:6px;
    border-bottom:2px solid var(--accent); display:inline-block; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:16px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:14px 16px; box-shadow:var(--shadow); display:flex; flex-direction:column; gap:8px; }}
  .card__head {{ display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }}
  .card__title {{ font-size:14px; margin:0; word-break:break-word; }}
  .badges {{ display:flex; gap:4px; flex-shrink:0; flex-wrap:wrap; justify-content:flex-end; }}
  .row {{ display:flex; gap:8px; font-size:13px; }}
  .lbl {{ color:var(--muted); min-width:58px; flex-shrink:0; }}
  .vals {{ display:flex; flex-wrap:wrap; gap:4px; align-items:center; }}
  .chip {{ background:var(--chip); color:var(--chipink); padding:2px 8px;
    border-radius:999px; font-size:12px; white-space:nowrap; }}
  .chip--fmt {{ background:var(--fmt); color:var(--fmtink); }}
  .chip--type {{ background:var(--type); color:var(--typeink); }}
  .chip--kind {{ background:var(--kind); color:var(--kindink); font-weight:600; letter-spacing:.3px; }}
  .chip--color {{ border:1px solid var(--line); }}
  .detected {{ margin:2px 0; padding:6px 10px; background:var(--chip);
    border-left:3px solid var(--accent); border-radius:6px; font-size:13px; font-style:italic; }}
  .caption {{ margin:4px 0 0; color:var(--ink); font-size:13.5px; }}
  .muted {{ color:var(--muted); }}
  .card__foot {{ margin-top:2px; }}
  .card__foot code {{ font-size:11px; color:var(--muted); word-break:break-all; }}
</style>
</head>
<body>
  <header class="top">
    <h1>Hey Harper — Content Library</h1>
    <div class="sub">{total} postable asset(s) · grouped by vertical · source: <code>{_esc(base_path)}</code> · generated {_esc(generated_at)}</div>
    <nav>{nav}</nav>
  </header>
  <main>{"".join(sections)}</main>
</body>
</html>"""
