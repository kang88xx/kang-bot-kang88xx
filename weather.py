"""날씨 알림: Open-Meteo 예보 수집 → 텔레그램 발송 (Claude 호출 없음, API 키 불필요, 무료).

사용법:
  python weather.py             # 실제 발송 (GitHub Actions에서 매일 아침 호출)
  python weather.py --dry-run   # 텔레그램은 보내지 않고 메시지 내용만 출력
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from common import load_config, load_dotenv
from page import WEEKDAYS
from telegram import send_message

API = "https://api.open-meteo.com/v1/forecast"
CURRENT = "temperature_2m,apparent_temperature,weather_code"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"

# WMO 날씨 코드 → (이모지, 설명). Open-Meteo의 weather_code 가 쓰는 값만 담았다.
WMO = {
    0: ("☀️", "맑음"), 1: ("🌤", "대체로 맑음"), 2: ("⛅", "구름 조금"), 3: ("☁️", "흐림"),
    45: ("🌫", "안개"), 48: ("🌫", "안개"),
    51: ("🌦", "약한 이슬비"), 53: ("🌦", "이슬비"), 55: ("🌦", "강한 이슬비"),
    56: ("🌧", "어는 이슬비"), 57: ("🌧", "어는 이슬비"),
    61: ("🌧", "약한 비"), 63: ("🌧", "비"), 65: ("🌧", "강한 비"),
    66: ("🌧", "어는 비"), 67: ("🌧", "어는 비"),
    71: ("🌨", "약한 눈"), 73: ("🌨", "눈"), 75: ("❄️", "강한 눈"), 77: ("🌨", "싸락눈"),
    80: ("🌦", "약한 소나기"), 81: ("🌦", "소나기"), 82: ("⛈", "강한 소나기"),
    85: ("🌨", "약한 소낙눈"), 86: ("❄️", "강한 소낙눈"),
    95: ("⛈", "뇌우"), 96: ("⛈", "우박 동반 뇌우"), 99: ("⛈", "우박 동반 뇌우"),
}


def describe(code) -> tuple[str, str]:
    return WMO.get(code, ("🌡", "날씨 정보 없음"))


def fetch_forecast(locations: list[dict], tz_name: str, days: int, retries: int = 3) -> list[dict]:
    """여러 지역을 요청 한 번으로 조회해 locations 와 같은 순서의 응답 목록을 반환.

    429/5xx/네트워크 오류는 지수 백오프로 재시도하고, 끝내 실패하면 예외를 올린다.
    """
    params = {
        "latitude": ",".join(str(loc["lat"]) for loc in locations),
        "longitude": ",".join(str(loc["lon"]) for loc in locations),
        "current": CURRENT,
        "daily": DAILY,
        "timezone": tz_name,
        "forecast_days": days,
    }
    delay = 3.0
    for attempt in range(retries + 1):
        try:
            resp = requests.get(API, params=params, timeout=20)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text[:120]}")
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if attempt == retries or (status is not None and status < 500 and status != 429):
                raise RuntimeError(f"날씨 조회 실패: {exc}") from exc
            print(f"  ! 날씨 조회 실패, {delay:.0f}초 뒤 재시도: {exc}")
            time.sleep(delay)
            delay *= 2
    results = data if isinstance(data, list) else [data]  # 지역이 하나면 객체 하나로 온다
    if len(results) != len(locations):
        raise RuntimeError(f"날씨 응답 지역 수 불일치: 요청 {len(locations)}곳, 응답 {len(results)}곳")
    return results


def _temp(value) -> str:
    return "?" if value is None else f"{round(value)}°"


def _day_line(day: date, today: date, code, tmin, tmax, prob, rain) -> str:
    emoji, desc = describe(code)
    weekday = WEEKDAYS[day.weekday()]
    label = f"오늘({weekday})" if day == today else f"{day.day}일({weekday})"
    line = f"• {label} {emoji} {desc} {_temp(tmin)}~{_temp(tmax)}"
    if prob is not None:
        line += f" · 강수 {round(prob)}%"
    if rain is not None and rain >= 0.5:
        line += f" ({max(1, round(rain))}mm)"
    return line


def build_message(locations: list[dict], results: list[dict], now: datetime) -> str:
    """텔레그램 HTML 본문: 지역별로 현재 기온 + 오늘부터 며칠간의 일별 예보."""
    today = now.date()
    parts = [f"🌤 <b>{now.month}월 {now.day}일 ({WEEKDAYS[today.weekday()]}) 날씨</b>"]
    for loc, res in zip(locations, results):
        cur = res.get("current") or {}
        head = f"📍 <b>{html.escape(str(loc['name']), quote=False)}</b>"
        if cur.get("temperature_2m") is not None:
            head += f" — 지금 {_temp(cur['temperature_2m'])}"
            if cur.get("apparent_temperature") is not None:
                head += f" (체감 {_temp(cur['apparent_temperature'])})"
        lines = [head]
        daily = res.get("daily") or {}
        days = daily.get("time") or []
        if not days:
            lines.append("• 예보를 가져오지 못했습니다")

        def col(name: str) -> list:
            values = daily.get(name) or []
            return list(values) + [None] * (len(days) - len(values))

        for row in zip(days, col("weather_code"), col("temperature_2m_min"), col("temperature_2m_max"),
                       col("precipitation_probability_max"), col("precipitation_sum")):
            lines.append(_day_line(date.fromisoformat(row[0]), today, *row[1:]))
        parts.append("\n".join(lines))
    parts.append('자료: <a href="https://open-meteo.com/">Open-Meteo</a>')
    return "\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 생략")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()
    weather = cfg.get("weather") or {}
    locations = [loc for loc in (weather.get("locations") or [])
                 if isinstance(loc, dict) and loc.get("name") and loc.get("lat") is not None
                 and loc.get("lon") is not None]
    if not locations:
        print("config.yaml 의 weather.locations 에 지역(name, lat, lon)이 없습니다. 종료.")
        return 1
    ahead = weather.get("days_ahead")
    try:
        ahead = 3 if ahead is None else int(ahead)
    except (TypeError, ValueError):
        ahead = 3
    days = 1 + min(15, max(0, ahead))  # 오늘 + 앞으로 N일 (Open-Meteo 예보는 최대 16일)
    now = datetime.now(ZoneInfo(cfg["timezone"]))
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not args.dry_run and not (token and chat_id):
        print("  ! TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다 (setup.py를 먼저 실행하세요).")
        return 1

    print(f"[1/2] 날씨 조회 ({len(locations)}곳, {days}일치)")
    try:
        results = fetch_forecast(locations, cfg["timezone"], days)
    except Exception as exc:
        print(f"  ! {exc}")
        if not args.dry_run:  # 조용히 빠지지 않도록 실패 사실은 알린다
            send_message(token, chat_id, "⚠️ 오늘 날씨 정보를 가져오지 못했습니다.")
        return 1

    print("[2/2] 텔레그램 발송")
    text = build_message(locations, results, now)
    if args.dry_run:
        print(f"-----\n{text}\n(dry-run: 전송 생략)")
        return 0
    count = send_message(token, chat_id, text)
    print(f"  - {count}개 메시지 전송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
