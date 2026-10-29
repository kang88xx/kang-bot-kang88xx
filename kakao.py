"""카카오톡 '나에게 보내기' (텍스트 템플릿)."""
from __future__ import annotations

import json

import requests

SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
TEXT_LIMIT = 200  # 텍스트 템플릿 최대 글자 수


def clip(text: str, limit: int = TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def send_text(access_token: str, text: str, url: str, button_title: str = "전체 보기") -> None:
    link = {"web_url": url, "mobile_web_url": url}
    template = {
        "object_type": "text",
        "text": clip(text),
        "link": link,
        "buttons": [{"title": button_title[:8], "link": link}],
    }
    resp = requests.post(
        SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"카카오 전송 실패 {resp.status_code}: {resp.text}")
    body = resp.json()
    if body.get("result_code", 0) != 0:
        raise RuntimeError(f"카카오 전송 실패: {body}")
