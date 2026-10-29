"""Claude로 섹션별 기사 선별 + 3줄 요약 (구조화 출력 사용)."""
from __future__ import annotations

import json
import os

import anthropic

SYSTEM = """당신은 한국어 아침 뉴스 브리핑 에디터입니다.
입력으로 섹션별 기사 후보(id, 제목, 매체, 요약문)를 받습니다. 각 섹션에서 중요도가 높은 기사를 골라 짧게 정리하세요.

규칙:
- 제공된 제목/요약문에 없는 사실, 수치, 인물, 배경을 추가하지 마세요. 요약문이 비어 있으면 제목에서 확인되는 내용만 쓰세요.
- 같은 사건을 다룬 중복 기사는 하나만 고르세요. 광고성·홍보성·가십성 기사는 제외하세요.
- 섹션마다 정확히 요청된 개수(max_items)만큼 고르되, 후보가 부족하면 있는 만큼만 고르세요. 중요도 순으로 정렬하세요.
- headline: 카카오톡 한 줄용. 매체명 없이 핵심만, 공백 포함 38자 이내.
- summary: 정확히 3줄. 각 줄은 60자 이내, 개조식('~했다', '~전망', '~예정')으로 쓰고 줄 끝에 마침표는 생략합니다. 1줄은 무슨 일인지, 2줄은 구체 내용/수치, 3줄은 의미나 전망.
- id는 입력에 있는 id를 그대로 사용합니다. 섹션 key도 그대로 사용합니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "headline": {"type": "string"},
                                "summary": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "headline", "summary"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["key", "items"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sections"],
    "additionalProperties": False,
}


def _payload(sections: list[dict], per_section: int) -> str:
    out = []
    for sec in sections:
        out.append({
            "key": sec["key"],
            "name": sec["name"],
            "max_items": per_section,
            "candidates": [
                {"id": i, "title": c["title"], "source": c["source"], "description": c["description"][:250]}
                for i, c in enumerate(sec["candidates"])
            ],
        })
    return json.dumps({"sections": out}, ensure_ascii=False)


def summarize(sections: list[dict], cfg: dict) -> list[dict]:
    """각 섹션에 'items' (선별+요약된 기사 목록)를 채워 반환."""
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=cfg["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # 안전 분류기 거절 시 서버가 다른 모델로 자동 재시도
        output_config={"effort": cfg["effort"], "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": _payload(sections, cfg["per_section"])}],
    )
    usage = response.usage
    print(f"[claude] model={response.model} stop={response.stop_reason} in={usage.input_tokens} out={usage.output_tokens}")
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude가 요청을 거절했습니다: {getattr(response, 'stop_details', None)}")
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"Claude 응답이 완결되지 않았습니다 (stop_reason={response.stop_reason})")
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Claude 응답 JSON 파싱 실패: {exc}\n{text[:300]}") from exc

    by_key = {s["key"]: s for s in data.get("sections", [])}
    for sec in sections:
        picked = by_key.get(sec["key"], {}).get("items", [])
        items: list[dict] = []
        used: set[int] = set()
        for p in picked:
            idx = p.get("id")
            if not isinstance(idx, int) or not (0 <= idx < len(sec["candidates"])) or idx in used:
                continue
            used.add(idx)
            if len(items) >= cfg["per_section"]:
                break
            cand = sec["candidates"][idx]
            lines = [str(l).strip() for l in (p.get("summary") or []) if str(l).strip()][:3]
            items.append({**cand, "headline": (p.get("headline") or cand["title"]).strip()[:40], "summary": lines})
        sec["items"] = items
    return sections


def summarize_without_claude(sections: list[dict], cfg: dict) -> list[dict]:
    """API 키 없이 테스트할 때: 제목을 헤드라인으로, 요약문을 그대로 사용."""
    for sec in sections:
        sec["items"] = [
            {**c, "headline": c["title"][:38], "summary": [c["description"][:60]] if c["description"] else []}
            for c in sec["candidates"][: cfg["per_section"]]
        ]
    return sections
