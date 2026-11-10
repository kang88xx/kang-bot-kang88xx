"""RSS 피드에서 기사 후보를 수집합니다 (외부 의존성 없음: 표준 라이브러리 + requests)."""
from __future__ import annotations

import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import requests

UA = "Mozilla/5.0 (compatible; MorningNewsBot/1.0)"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SEP_RE = re.compile(r"\s+[-|–:]\s+")
FEED_SUFFIX_RE = re.compile(r"\s+(RSS|최신기사)\s*$", re.IGNORECASE)
RESOLVE_BUDGET_SEC = 45  # 구글뉴스 링크 변환에 쓸 수 있는 총 시간
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")


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
        source = _clean(item.findtext("source"), 50)
        if is_google:
            # 구글뉴스 제목은 "제목 - 매체명" 형식
            if " - " in title:
                title, _, tail = title.rpartition(" - ")
                source = source or tail
            # 구글뉴스 description은 관련 기사 링크 모음이라 요약에 부적합
            desc = ""
        if title and link:
            items.append({"title": title, "link": link, "description": desc,
                          "published": pub, "source": source or channel_title})

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


def _host(link: str) -> str:
    try:
        host = (urlparse(link or "").hostname or "").lower()
    except ValueError:  # 깨진 주소(예: "http://[bad")가 하루치 실행을 죽이지 않도록
        return ""
    return host[4:] if host.startswith("www.") else host


def source_name(source: str, link: str, names: dict) -> str:
    """표시용 매체명: 도메인 매핑 우선, 없으면 피드 제목에서 구분자 뒤와 RSS/최신기사 꼬리를 걷어낸다."""
    host = _host(link)
    for domain, name in names.items():
        if host == domain or host.endswith("." + domain):
            return name
    name = SEP_RE.split(source or "", 1)[0]
    name = FEED_SUFFIX_RE.sub("", name).strip()
    return name or host or "원문"


def resolve_google_link(link: str, session: requests.Session, timeout: int = 6) -> str | None:
    """구글뉴스 RSS 링크를 원문 주소로 변환. 실패하면 None (네트워크 오류는 예외로 올라갈 수 있음)."""
    art_id = urlparse(link).path.rstrip("/").split("/")[-1]
    r = session.get(f"https://news.google.com/rss/articles/{art_id}",
                    headers={"User-Agent": BROWSER_UA}, timeout=timeout)
    sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
    if not (sg and ts):
        return None
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
             f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art_id}",{ts.group(1)},"{sg.group(1)}"]')
    body = "f.req=" + quote(json.dumps([[["Fbv4je", inner]]]))
    p = session.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                     headers={"User-Agent": BROWSER_UA,
                              "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                     data=body, timeout=timeout)
    try:
        payload = json.loads(p.text.split("\n\n", 1)[1])
        url = json.loads(payload[0][2])[1]
    except Exception:
        return None
    if isinstance(url, str) and url.startswith(("http://", "https://")) and _host(url) != "news.google.com":
        return url
    return None


def resolve_links(sections: list[dict]) -> None:
    """선별된 기사 중 구글뉴스 링크를 원문 주소로 바꾼다. 실패해도 예외 없이 원래 링크 유지, 연속 3회 실패하면 중단."""
    targets = [it for sec in sections for it in sec.get("items", [])
               if _host(it.get("link") or "") == "news.google.com"]
    if not targets:
        return
    done = fails = 0
    deadline = time.monotonic() + RESOLVE_BUDGET_SEC
    with requests.Session() as session:
        for it in targets:
            if fails >= 3 or time.monotonic() > deadline:  # 구글이 막았거나 시간 초과면 포기
                break
            try:
                url = resolve_google_link(it["link"], session)
            except Exception:
                url = None
            if url:
                it["link"] = url
                done += 1
                fails = 0
            else:
                fails += 1
    print(f"  - 구글뉴스 링크 {len(targets)}건 중 {done}건을 원문 주소로 변환")


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
    for sec in sections:
        for it in sec["candidates"]:
            it["source"] = source_name(it["source"], it["link"], cfg["source_names"])
    return sections
