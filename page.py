"""브리핑 웹페이지(HTML) 생성: docs/index.html + docs/archive/YYYY-MM-DD.html."""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from common import ROOT

DOCS = ROOT / "docs"
WEEKDAYS = "월화수목금토일"

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1a1d21;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8eb;--muted:#9aa1ab;--line:#2a2f3a;--accent:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}.date{color:var(--muted);margin:0 0 24px;font-size:14px}
h2{font-size:18px;margin:28px 0 12px;padding-top:12px;border-top:1px solid var(--line)}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 10px}
article h3{font-size:16px;margin:0 0 8px;line-height:1.4}article h3 a{color:var(--fg);text-decoration:none}article h3 a:hover{color:var(--accent)}
article ul{margin:0;padding-left:18px;font-size:15px}article li{margin:2px 0}
.meta{color:var(--muted);font-size:12.5px;margin-top:8px}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 8px}nav a{font-size:13px;color:var(--accent);text-decoration:none;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 10px}
footer{color:var(--muted);font-size:13px;margin-top:32px}footer a{color:var(--accent)}
"""


def _fmt_time(dt: datetime | None, tz) -> str:
    if not dt:
        return ""
    return dt.astimezone(tz).strftime("%m/%d %H:%M")


def render(sections: list[dict], now: datetime, archive_dates: list[str]) -> str:
    tz = now.tzinfo
    date_label = f"{now.year}년 {now.month}월 {now.day}일 ({WEEKDAYS[now.weekday()]}) {now:%H:%M} 기준"
    parts = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>아침 뉴스 브리핑 {now:%Y-%m-%d}</title><style>{CSS}</style></head><body><main>",
             "<h1>☀️ 아침 뉴스 브리핑</h1>", f"<p class='date'>{html.escape(date_label)}</p>"]
    parts.append("<nav>" + "".join(
        f"<a href='#{html.escape(s['key'])}'>{html.escape(s['emoji'] + ' ' + s['name'])}</a>" for s in sections) + "</nav>")
    for sec in sections:
        parts.append(f"<h2 id='{html.escape(sec['key'])}'>{html.escape(sec['emoji'] + ' ' + sec['name'])}</h2>")
        if not sec.get("items"):
            parts.append("<p class='meta'>오늘은 수집된 기사가 없습니다.</p>")
        for it in sec.get("items", []):
            lines = "".join(f"<li>{html.escape(l)}</li>" for l in it.get("summary", []))
            meta = " · ".join(x for x in [it.get("source", ""), _fmt_time(it.get("published"), tz)] if x)
            parts.append(
                f"<article><h3><a href='{html.escape(it['link'])}' target='_blank' rel='noopener'>"
                f"{html.escape(it.get('headline') or it['title'])}</a></h3>"
                f"{'<ul>' + lines + '</ul>' if lines else ''}"
                f"<div class='meta'>{html.escape(meta)}</div></article>")
    if archive_dates:
        links = " · ".join(f"<a href='archive/{d}.html'>{d}</a>" for d in archive_dates)
        parts.append(f"<footer>지난 브리핑: {links}</footer>")
    parts.append("</main></body></html>")
    return "".join(parts)


def write_pages(sections: list[dict], now: datetime, keep_archive: int = 14) -> Path:
    DOCS.mkdir(exist_ok=True)
    archive = DOCS / "archive"
    archive.mkdir(exist_ok=True)
    today = now.strftime("%Y-%m-%d")
    existing = sorted(p.stem for p in archive.glob("*.html") if p.stem != today)
    page = render(sections, now, existing[::-1][:7])
    (archive / f"{today}.html").write_text(page.replace("href='archive/", "href='"), encoding="utf-8")
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    # 아카이브 정리: 오늘 포함 최근 keep_archive개만 유지
    all_dates = sorted(p.stem for p in archive.glob("*.html"))
    for old in all_dates[: max(0, len(all_dates) - keep_archive)]:
        (archive / f"{old}.html").unlink(missing_ok=True)
    return DOCS / "index.html"
