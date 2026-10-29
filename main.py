"""아침 뉴스 브리핑: 수집 → Claude 요약 → 웹페이지 생성 → 카카오톡 발송.

사용법:
  python main.py                 # 실제 실행 (GitHub Actions에서 매일 아침 호출)
  python main.py --dry-run       # 카톡은 보내지 않고 메시지 내용만 출력
  python main.py --skip-claude   # Claude 요약 없이 제목만으로 테스트 (API 키 불필요)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from common import ROOT, load_config, load_dotenv, page_url
from kakao import TEXT_LIMIT, clip, send_text
from news_fetch import collect_all
from page import WEEKDAYS, write_pages
from summarize import summarize, summarize_without_claude


def build_messages(sections: list[dict], now: datetime, top: int) -> list[tuple[str, str]]:
    """섹션별 카톡 메시지 (본문, 앵커) 목록. 본문은 200자 이내로 맞춘다."""
    date_tag = f"{now.month}/{now.day}({WEEKDAYS[now.weekday()]})"
    messages = []
    for sec in sections:
        items = sec.get("items", [])[:top]
        if not items:
            continue
        header = f"{sec['emoji']} {sec['name']} · {date_tag}"
        lines = [f"{i + 1}. {it['headline']}" for i, it in enumerate(items)]
        text = "\n".join([header, *lines])
        while len(text) > TEXT_LIMIT and len(lines) > 1:  # 넘치면 마지막 줄부터 제거
            lines.pop()
            text = "\n".join([header, *lines])
        messages.append((clip(text), sec["key"]))
    return messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="카톡 전송 생략")
    parser.add_argument("--skip-claude", action="store_true", help="Claude 요약 생략 (테스트용)")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()
    now = datetime.now(ZoneInfo(cfg["timezone"]))

    print("[1/4] 뉴스 수집")
    sections = collect_all(cfg)
    for s in sections:
        print(f"  - {s['name']}: 후보 {len(s['candidates'])}건")
    if not any(s["candidates"] for s in sections):
        print("수집된 기사가 없습니다. 종료.")
        return 1

    print("[2/4] 요약")
    if args.skip_claude:
        sections = summarize_without_claude(sections, cfg)
    else:
        try:
            sections = summarize(sections, cfg)
        except Exception as exc:  # 요약이 실패해도 제목만으로라도 브리핑은 보낸다
            print(f"  ! Claude 요약 실패, 제목만으로 진행합니다: {exc}")
            sections = summarize_without_claude(sections, cfg)

    print("[3/4] 웹페이지 생성")
    out = write_pages(sections, now)
    print(f"  - {out}")
    (ROOT / "state").mkdir(exist_ok=True)
    (ROOT / "state" / "last_digest.json").write_text(
        json.dumps(sections, ensure_ascii=False, default=str, indent=1), encoding="utf-8")

    print("[4/4] 카카오톡 발송")
    url = page_url(cfg)
    if not url:
        print("  ! page_url을 알 수 없습니다. config.yaml의 page_url 또는 PAGE_URL 환경변수를 설정하세요.")
    messages = build_messages(sections, now, cfg["kakao_top"])
    if not messages:
        print("  ! 보낼 기사가 없습니다.")
        return 1
    if args.dry_run:
        for text, key in messages:
            print(f"----- ({len(text)}자) 링크: {url}#{key}\n{text}")
        print("(dry-run: 전송 생략)")
        return 0

    from tokens import get_access_token  # 토큰 파일/키가 필요하므로 여기서 import
    token = get_access_token()
    sent = 0
    for text, key in messages:
        send_text(token, text, f"{url}#{key}" if url else "https://news.google.com/?hl=ko&gl=KR&ceid=KR:ko")
        sent += 1
    print(f"  - {sent}건 전송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
