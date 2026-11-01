"""텔레그램 봇 API: 메시지 발송(4096자 분할, 재시도, HTML 실패 시 평문 재전송), 업데이트 조회."""
from __future__ import annotations

import re
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"
TEXT_LIMIT = 4096
SAFE_LIMIT = 3900  # HTML 태그 여유분을 두고 자르는 기준
TAG_RE = re.compile(r"<[^>]+>")


class TelegramError(RuntimeError):
    def __init__(self, method: str, status: int, description: str):
        super().__init__(f"텔레그램 {method} 실패 {status}: {description}")
        self.status = status
        self.description = description


def _call(token: str, method: str, retries: int = 3, **params) -> dict:
    """API 호출. 429/5xx/네트워크 오류는 retry_after 또는 지수 백오프로 재시도."""
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            resp = requests.post(API.format(token=token, method=method), json=params, timeout=30)
        except requests.RequestException as exc:
            if attempt == retries:
                raise TelegramError(method, 0, f"네트워크 오류: {exc}") from exc
            time.sleep(delay)
            delay *= 2
            continue
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code == 200 and body.get("ok"):
            return body["result"]
        desc = body.get("description") or resp.text[:200]
        retryable = resp.status_code == 429 or resp.status_code >= 500
        if retryable and attempt < retries:
            wait = (body.get("parameters") or {}).get("retry_after") or delay
            time.sleep(float(wait))
            delay *= 2
            continue
        raise TelegramError(method, resp.status_code, desc)
    raise TelegramError(method, 0, "재시도 횟수 초과")  # pragma: no cover


def get_me(token: str) -> dict:
    return _call(token, "getMe")


def delete_webhook(token: str) -> None:
    """getUpdates 와 webhook 은 동시에 못 쓰므로, 설정 때 webhook 을 지워 둔다."""
    _call(token, "deleteWebhook", drop_pending_updates=False)


def get_updates(token: str, offset: int | None = None) -> list[dict]:
    params = {"timeout": 0, "allowed_updates": ["message", "edited_message"]}
    if offset is not None:
        params["offset"] = offset
    return _call(token, "getUpdates", **params)


def _split_long_line(line: str, limit: int) -> list[str]:
    """한 줄이 limit 를 넘을 때 공백 경계로 자른다. <a>…</a> 와 그 외 태그는 한 토큰으로 묶어 안에서 끊지 않는다."""
    tokens = [tok for tok in re.split(r"(<a\b[^>]*>.*?</a>|<[^>]+>|\s+)", line) if tok]
    out, cur = [], ""
    for tok in tokens:
        if len(cur) + len(tok) <= limit:
            cur += tok
        else:
            if cur.strip():
                out.append(cur.rstrip())
            cur = tok if len(tok) <= limit else tok[:limit]
    if cur.strip():
        out.append(cur.rstrip())
    return out


def split_message(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """빈 줄(단락) 경계 우선, 그다음 줄 경계, 마지막으로 공백 경계로 limit 이하 조각으로 나눈다."""
    chunks: list[str] = []
    cur = ""

    def flush():
        nonlocal cur
        if cur:
            chunks.append(cur)
            cur = ""

    for para in text.split("\n\n"):
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) <= limit:
            cur = candidate
            continue
        flush()
        if len(para) <= limit:
            cur = para
            continue
        for line in para.split("\n"):
            pieces = [line] if len(line) <= limit else _split_long_line(line, limit)
            for piece in pieces:
                candidate = f"{cur}\n{piece}" if cur else piece
                if len(candidate) <= limit:
                    cur = candidate
                else:
                    flush()
                    cur = piece
    flush()
    return chunks


def strip_html(text: str) -> str:
    import html as _html
    return _html.unescape(TAG_RE.sub("", text))


def send_chunk(token: str, chat_id: str | int, chunk: str, parse_mode: str | None = "HTML") -> None:
    """메시지 하나 발송. HTML 파싱 오류(400)면 태그를 벗겨 평문으로 한 번 더 보낸다."""
    params = {"chat_id": chat_id, "text": chunk, "link_preview_options": {"is_disabled": True}}
    if parse_mode:
        params["parse_mode"] = parse_mode
    try:
        _call(token, "sendMessage", **params)
    except TelegramError as exc:
        if parse_mode and exc.status == 400 and "parse" in exc.description.lower():
            print(f"  ! HTML 파싱 실패, 평문으로 재전송: {exc.description[:120]}")
            params.pop("parse_mode")
            params["text"] = strip_html(chunk)
            _call(token, "sendMessage", **params)
        else:
            raise


def send_message(token: str, chat_id: str | int, text: str, parse_mode: str = "HTML") -> int:
    """긴 글은 여러 메시지로 나눠 보낸다. 보낸 메시지 수를 반환."""
    chunks = split_message(text)
    for chunk in chunks:
        send_chunk(token, chat_id, chunk, parse_mode)
    return len(chunks)
