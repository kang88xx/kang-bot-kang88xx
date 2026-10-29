"""RSS 피드에서 기사 후보를 수집합니다 (외부 의존성 없음: 표준 라이브러리 + requests)."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

UA = "Mozilla/5.0 (compatible; MorningNewsBot/1.0)"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _clean(text: str | None, limit: int = 300) -> str:
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", text))
    text = WS_RE.sub(" ", text).strip()
    return text[:limit]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _norm_title(title: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", title.lower())


def fetch_feed(url: str, timeout: int = 20) -> list[dict]:
    """하나의 RSS/Atom 피드를 읽어 기사 dict 목록으로 변환."""
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    channel_title = _clean(root.findtext("./channel/title")) or _clean(
        root.findtext("{http://www.w3.org/2005/Atom}title")
    )
    is_google = "news.google.com" in url
    items: list[dict] = []

    # RSS 2.0
    for item in root.iter("item"):
        title = _clean(item.findtext("title"), 200)
        link = (item.findtext("link") or "").strip()
        desc = _clean(item.findtext("description"))
        pub = _parse_date(item.findtext("pubDate"))
        source = _clean(item.findtext("source"), 50) or channel_title
        if is_google:
            # 구글뉴스 제목은 "제목 - 매체명" 형식
            if " - " in title:
                title, _, tail = title.rpartition(" - ")
                source = source or tail
            # 구글뉴스 description은 관련 기사 링크 모음이라 요약에 부적합
            desc = ""
        if title and link:
            items.append({"title": title, "link": link, "description": desc,
                          "published": pub, "source": source})

    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        title = _clean(entry.findtext("a:title", namespaces=ns), 200)
        link_el = entry.find("a:link", ns)
        link = (link_el.get("href") if link_el is not None else "") or ""
        desc = _clean(entry.findtext("a:summary", namespaces=ns) or entry.findtext("a:content", namespaces=ns))
        pub = _parse_date(entry.findtext("a:published", namespaces=ns) or entry.findtext("a:updated", namespaces=ns))
        if title and link:
            items.append({"title": title, "link": link, "description": desc,
                          "published": pub, "source": channel_title})
    return items


def google_search_feed(keyword: str) -> str:
    return f"https://news.google.com/rss/search?q={quote(keyword)}&hl=ko&gl=KR&ceid=KR:ko"


def collect_section(feeds: list[str], max_age_hours: int, max_candidates: int) -> list[dict]:
    """여러 피드를 합쳐 최신순 정렬, 중복 제거, 후보 수 제한."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    seen_titles: set[str] = set()
    seen_links: set[str] = set()
    merged: list[dict] = []
    for url in feeds:
        try:
            items = fetch_feed(url)
        except Exception as exc:  # 피드 하나가 죽어도 전체는 계속
            print(f"[warn] 피드 실패 {url}: {exc}")
            continue
        for it in items:
            if it["published"] and it["published"] < cutoff:
                continue
            key = _norm_title(it["title"])
            if not key or key in seen_titles or it["link"] in seen_links:
                continue
            seen_titles.add(key)
            seen_links.add(it["link"])
            merged.append(it)
    # 날짜 없는 항목은 뒤로
    merged.sort(key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return merged[:max_candidates]


def collect_all(cfg: dict) -> list[dict]:
    """config의 sections + keywords를 모두 수집해 섹션 목록으로 반환."""
    sections: list[dict] = []
    for sec in cfg["sections"]:
        items = collect_section(sec["feeds"], cfg["max_age_hours"], cfg["max_candidates"])
        sections.append({"key": sec["key"], "name": sec["name"], "emoji": sec.get("emoji", "📰"),
                         "candidates": items})
    for i, kw in enumerate(cfg["keywords"]):
        items = collect_section([google_search_feed(kw)], cfg["max_age_hours"], cfg["max_candidates"])
        sections.append({"key": f"kw{i}", "name": f"키워드: {kw}", "emoji": "🔎", "candidates": items})
    return sections
