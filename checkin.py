"""체크인(운동/업무/언어공부): 텔레그램 답장 수집 → 파싱 → 기록/연속일수.

state/telegram_state.json 에 getUpdates offset, 날짜별 체크인 기록, 오늘 발송 진행 상황을 저장하고,
워크플로가 매일 커밋해 다음 실행에서 이어 읽는다.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from common import ROOT
from telegram import get_updates

STATE_FILE = ROOT / "state" / "telegram_state.json"
FIELDS = {"exercise": "운동", "work": "업무", "language": "언어공부"}
# 새벽 04:00 전에 보낸 답장은 전날 체크인으로 친다 (아침 발송 07:50 이전이면 어차피 전날 것)
DAY_CUTOFF_HOURS = 4

# 키워드는 줄 처음 또는 구분자(/ , ; | . !) 바로 뒤에서만 항목 시작으로 본다.
# 메모 안에 '회사', '영어' 같은 단어가 섞여도 항목이 끊기지 않게 하기 위함.
KEYWORD_RE = re.compile(r"(?:^|(?<=[\n/,;|.!]))\s*(?P<kw>언어\s*공부|언어|공부|영어|어학|운동|업무|작업|회사)")
KEY_OF = {"운동": "exercise", "업무": "work", "작업": "work", "회사": "work"}  # 나머지 키워드는 language
LEAD_RE = re.compile(r"^[\s:：은는도이가을를\-–—]+")
TRAIL_RE = re.compile(r"[\s/,;.!~|]+$")
YES_RE = re.compile(r"^(o|ㅇ|했|완|✅|✔|👍|yes|y|done|ok)", re.I)
NO_RE = re.compile(r"^(x|ㄴ|안|못|❌|✖|no|n|skip)", re.I)
# 업무는 메모가 곧 '했다'는 뜻이라, 전체가 짧은 부정 표현일 때만 X
WORK_NO_RE = re.compile(r"^(x+|ㄴ+|안\s*(함|했\S*)?|못\s*(함|했\S*)?|❌|✖|no|n|skip|없음|쉼|휴식)$", re.I)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"offset": None, "log": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def checkin_day(when: datetime) -> date:
    return (when - timedelta(hours=DAY_CUTOFF_HOURS)).date()


def fetch_replies(token: str, chat_id: str | int, state: dict, tz: ZoneInfo) -> list[dict]:
    """새 업데이트 중 내 채팅방의 텍스트 메시지만 (날짜, 본문, message_id) 목록으로 반환하고 offset을 전진.

    수정된 메시지(edited_message)도 포함한다 — 같은 message_id 라 record()에서 덧씌워진다.
    """
    updates = get_updates(token, state.get("offset"))
    replies = []
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        if str((msg.get("chat") or {}).get("id")) != str(chat_id) or not text or text.startswith("/"):
            continue
        when = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc).astimezone(tz)
        replies.append({"date": when, "text": text, "message_id": msg.get("message_id")})
    if updates:
        state["offset"] = max(u["update_id"] for u in updates) + 1
    return replies


def _verdict(value: str) -> str | None:
    v = value.strip()
    if not v:
        return None
    if YES_RE.match(v):
        return "O"
    if NO_RE.match(v):
        return "X"
    return None


def parse_checkin(text: str) -> dict | None:
    """'운동: O (달리기) / 업무: 보고서 / 언어 X', '운동했어, 업무는 회의만' 같은 자유 형식을 필드별로 분해.

    키워드가 줄 처음/구분자 뒤에 나오는 위치를 항목 시작으로 보고, 다음 항목 전까지를 값으로 본다.
    체크인으로 볼 수 없는 메시지면 None.
    """
    hits = list(KEYWORD_RE.finditer(text))
    if not hits:
        return None
    result: dict = {}
    for i, hit in enumerate(hits):
        key = KEY_OF.get(hit.group("kw").replace(" ", ""), "language")
        seg_end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        rest = TRAIL_RE.sub("", LEAD_RE.sub("", text[hit.end():seg_end])).strip()
        if key in result and not rest:
            continue
        if key == "work":
            result[key] = {"done": "X" if WORK_NO_RE.match(rest) else ("O" if rest else None), "note": rest}
        else:
            result[key] = {"done": _verdict(rest), "note": rest}
    return result


def record(state: dict, replies: list[dict]) -> None:
    """답장을 체크인 날짜(KST, 새벽 4시 기준) 아래 기록. 필드별로 합치고, 확정된 O/X는 None으로 덮어쓰지 않는다."""
    log = state.setdefault("log", {})
    for r in replies:
        parsed = parse_checkin(r["text"])
        if not parsed:
            continue
        day = log.setdefault(checkin_day(r["date"]).isoformat(), {})
        for key, val in parsed.items():
            prev = day.get(key) or {}
            if val.get("done") is None and prev.get("done") is not None:
                val = {**val, "done": prev["done"]}
            if not val.get("note") and prev.get("note"):
                val = {**val, "note": prev["note"]}
            day[key] = val
        raw = day.setdefault("raw", {})
        if isinstance(raw, str):  # 예전 형식 호환
            raw = {"_": raw}
            day["raw"] = raw
        raw[str(r.get("message_id") or len(raw))] = r["text"]


def _raw_text(entry: dict) -> str:
    raw = entry.get("raw", "")
    if isinstance(raw, dict):
        return "\n".join(raw.values())
    return raw or ""


def streak(log: dict, key: str, until: date) -> int:
    """until 날짜까지 끊기지 않고 key가 O인 연속 일수."""
    n = 0
    d = until
    while True:
        entry = (log.get(d.isoformat()) or {}).get(key) or {}
        if entry.get("done") != "O":
            return n
        n += 1
        d -= timedelta(days=1)


def summary_for(state: dict, today: date) -> dict:
    """어제(없으면 지난 3일 중 가장 최근) 체크인과 연속일수를 요약. Claude 코멘트 입력용."""
    log = state.get("log", {})
    yesterday = today - timedelta(days=1)
    picked_day = None
    for back in range(1, 4):
        d = today - timedelta(days=back)
        if d.isoformat() in log:
            picked_day = d
            break
    entry = log.get(picked_day.isoformat(), {}) if picked_day else {}
    return {
        "date": picked_day.isoformat() if picked_day else None,
        "is_yesterday": picked_day == yesterday,
        "fields": {k: {"label": FIELDS[k], **(entry.get(k) or {"done": None, "note": ""})} for k in FIELDS},
        "streaks": {k: streak(log, k, yesterday) for k in FIELDS},
        "raw": _raw_text(entry),
        "days_logged_total": len(log),
    }


def line_for(summary: dict) -> str:
    """'운동 O · 업무 O · 언어공부 X' 한 줄. 기록이 없으면 '답장 없음'."""
    if not summary.get("date"):
        return "답장 없음"
    parts = []
    for k, label in FIELDS.items():
        f = summary["fields"][k]
        mark = f.get("done") or "–"
        parts.append(f"{label} {mark}")
    return " · ".join(parts)
