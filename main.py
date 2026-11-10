"""데일리 비서: 체크인 수집 → 뉴스 수집 → Claude 요약 → 웹페이지 생성 → 텔레그램 발송.

사용법:
  python main.py                 # 실제 실행 (GitHub Actions에서 매일 아침 호출)
  python main.py --dry-run       # 텔레그램은 보내지 않고 메시지 내용만 출력
  python main.py --skip-claude   # Claude 요약 없이 제목만으로 테스트 (API 키 불필요)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import checkin
from common import ROOT, load_config, load_dotenv, page_url
from news_fetch import collect_all, resolve_links
from page import WEEKDAYS, write_pages
from summarize import summarize, summarize_without_claude
from telegram import send_chunk, split_message, visible_len

DIVIDER = "─" * 12
CHECKIN_TEMPLATE = "운동: O/X (한 일)\n업무: 오늘 핵심 1~3개 / 진행 상황\n언어공부: O/X"


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def build_message(sections: list[dict], now: datetime, checkin_summary: dict, comment: str, url: str,
                  checkin_error: str | None = None) -> str:
    """텔레그램 HTML 본문. 길면 telegram.send_message가 단락 경계로 나눠 보낸다."""
    date_label = f"{now.month}월 {now.day}일 ({WEEKDAYS[now.weekday()]})"
    parts = [f"☀️ <b>{date_label} 데일리 브리핑</b>"]

    when = "어제" if checkin_summary.get("is_yesterday") else (
        f"{checkin_summary['date'][5:].replace('-', '/')}" if checkin_summary.get("date") else "어제")
    block = [f"✅ <b>{when} 체크인</b>", _esc(checkin.line_for(checkin_summary))]
    if comment:
        block.append(f"→ {_esc(comment)}")
    if checkin_error:
        block.append(f"⚠️ 답장 수집 실패: {_esc(checkin_error[:120])}")
    parts.append("\n".join(block))

    for sec in sections:
        items = sec.get("items", [])
        head = [DIVIDER, f"{sec['emoji']} <b>{_esc(sec['name'])}</b>"]
        if not items:
            head.append("특이사항 없음")
        parts.append("\n".join(head))
        for n, it in enumerate(items, 1):
            lines = [f"<b>{n}. {_esc(it.get('headline') or it['title'])}</b>"]
            lines += [f"• {_esc(s)}" for s in it.get("summary", [])[:3]]
            lines.append(f"출처: <a href=\"{html.escape(it['link'])}\">{_esc(it.get('source') or '원문')}</a>")
            parts.append("\n".join(lines))

    tail = [DIVIDER, f"📝 <b>오늘 밤 체크인</b> — 이 채팅에 답장으로:", f"<code>{_esc(CHECKIN_TEMPLATE)}</code>"]
    if url:
        tail.append(f'<a href="{html.escape(url)}">전체 브리핑 웹페이지</a>')
    parts.append("\n".join(tail))
    return "\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 생략")
    parser.add_argument("--skip-claude", action="store_true", help="Claude 요약 생략 (테스트용)")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    now = datetime.now(tz)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    print("[1/5] 체크인 답장 수집")
    state = checkin.load_state()
    checkin_error = None
    if token and chat_id:
        try:
            replies = checkin.fetch_replies(token, chat_id, state, tz)
            checkin.record(state, replies)
            print(f"  - 새 답장 {len(replies)}건")
            if not args.dry_run:
                checkin.save_state(state)  # 읽은 답장은 즉시 저장 (이후 단계가 죽어도 유실 방지)
        except Exception as exc:  # 체크인 수집이 실패해도 브리핑은 보낸다
            checkin_error = str(exc)
            print(f"  ! 답장 수집 실패, 기존 기록으로 진행: {exc}")
    else:
        print("  - TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 없음, 건너뜀")
    if args.dry_run:
        print("  - dry-run: 상태 파일은 저장하지 않음")
    checkin_summary = checkin.summary_for(state, now.date())
    print(f"  - {checkin.line_for(checkin_summary)}")

    print("[2/5] 뉴스 수집")
    sections = collect_all(cfg)
    for s in sections:
        print(f"  - {s['name']}: 후보 {len(s['candidates'])}건")
    if not any(s["candidates"] for s in sections):
        print("수집된 기사가 없습니다. 종료.")
        return 1

    print("[3/5] 요약")
    if args.skip_claude:
        sections, comment = summarize_without_claude(sections, cfg)
    else:
        try:
            sections, comment = summarize(sections, cfg, checkin_summary)
        except Exception as exc:  # 요약이 실패해도 제목만으로라도 브리핑은 보낸다
            print(f"  ! Claude 요약 실패, 제목만으로 진행합니다: {exc}")
            sections, comment = summarize_without_claude(sections, cfg)
    resolve_links(sections)

    print("[4/5] 웹페이지 생성")
    out = write_pages(sections, now)
    print(f"  - {out}")
    (ROOT / "state").mkdir(exist_ok=True)
    (ROOT / "state" / "last_digest.json").write_text(
        json.dumps(sections, ensure_ascii=False, default=str, indent=1), encoding="utf-8")

    print("[5/5] 텔레그램 발송")
    url = page_url(cfg)
    text = build_message(sections, now, checkin_summary, comment, url, checkin_error)
    chunks = split_message(text)
    if args.dry_run:
        print(f"----- (본문 {visible_len(text)}자, {len(chunks)}개 메시지)\n{text}\n(dry-run: 전송 생략)")
        return 0
    if not token or not chat_id:
        print("  ! TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다 (setup.py를 먼저 실행하세요).")
        return 1
    # 같은 날 재실행(수동 실행, 재시도)이면 이미 보낸 조각은 건너뛴다
    today = now.date().isoformat()
    sent = state.get("sent") or {}
    start = sent.get("chunks", 0) if sent.get("date") == today else 0
    if start >= len(chunks):
        print(f"  - 오늘({today}) 브리핑은 이미 {start}개 메시지로 발송됨. 건너뜀")
        return 0
    try:
        for i in range(start, len(chunks)):
            send_chunk(token, chat_id, chunks[i])
            state["sent"] = {"date": today, "chunks": i + 1}
        print(f"  - {len(chunks) - start}개 메시지 전송 완료")
    finally:
        checkin.save_state(state)  # 발송 실패 시에도 진행 상황은 저장
    return 0


if __name__ == "__main__":
    sys.exit(main())
