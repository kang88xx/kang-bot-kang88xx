"""최초 1회 설정 마법사: 키 입력 → 봇 확인 → chat_id 찾기 → 테스트 발송 → GitHub 시크릿 등록.

    python setup.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import checkin
from common import ROOT, load_config, load_dotenv, page_url
from telegram import delete_webhook, get_me, get_updates, send_message

ENV_FILE = ROOT / ".env"
SECRET_NAMES = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"]


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    shown = f" [{'설정됨' if secret and default else default}]" if default else ""
    value = input(f"{prompt}{shown}: ").strip()
    return value or default


def write_env(values: dict) -> None:
    """기존 .env 의 다른 키(PAGE_URL 등)는 유지하고 값이 있는 키만 갱신."""
    existing: dict = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    existing.update({k: v for k, v in values.items() if v})
    ENV_FILE.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n", encoding="utf-8")
    os.environ.update({k: v for k, v in values.items() if v})


def find_chat_id(token: str, bot_username: str, wait_sec: int = 120) -> tuple[str, int | None]:
    """최근 업데이트에서 개인 채팅 id를 찾는다. 없으면 사용자가 봇에게 말을 걸 때까지 기다린다."""
    print(f"\n텔레그램에서 @{bot_username} 대화방을 열고 아무 메시지나 하나 보내주세요 (예: 안녕).")
    deadline = time.time() + wait_sec
    while True:
        updates = get_updates(token)
        chats = [(u["message"]["chat"], u["update_id"]) for u in updates
                 if u.get("message") and u["message"].get("chat", {}).get("type") == "private"]
        if chats:
            chat, last_update = chats[-1][0], max(uid for _, uid in chats)
            who = " ".join(x for x in [chat.get("first_name", ""), chat.get("last_name", "")] if x) or chat.get("username", "")
            print(f"채팅방 발견: {who} (@{chat.get('username', '-')}, chat_id={chat['id']})")
            if ask("이 사람이 본인 맞나요? (y/n)", "y").lower().startswith("y"):
                return str(chat["id"]), last_update + 1
            raise RuntimeError("다른 사람의 메시지가 먼저 들어왔습니다. 봇 username을 바꾸거나 잠시 뒤 다시 실행하세요.")
        if time.time() > deadline:
            raise RuntimeError("메시지를 받지 못했습니다. 봇에게 메시지를 보낸 뒤 setup.py를 다시 실행하세요.")
        time.sleep(3)


def push_to_github(values: dict) -> None:
    if not shutil.which("gh"):
        print("gh CLI가 없어 GitHub 시크릿 자동 등록을 건너뜁니다. README의 수동 등록 방법을 참고하세요.")
        return
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True)
    if remote.returncode != 0:
        print("git 원격 저장소(origin)가 없어 GitHub 등록을 건너뜁니다.")
        return
    print("\nGitHub 시크릿 등록 중...")
    for name in SECRET_NAMES:
        value = values.get(name) or ""
        if not value:
            continue
        r = subprocess.run(["gh", "secret", "set", name], cwd=ROOT, input=value, text=True, capture_output=True)
        if r.returncode != 0:
            print(f"  ! {name} 등록 실패: {r.stderr.strip()[:200]}  (수동: gh secret set {name})")
            continue
        print(f"  - {name} 등록")


def main() -> int:
    load_dotenv()
    print("=== 데일리 비서(텔레그램) 설정 ===\n(값을 그대로 두려면 Enter)\n")
    values = {
        "TELEGRAM_BOT_TOKEN": ask("1) 텔레그램 봇 토큰 (@BotFather)", os.environ.get("TELEGRAM_BOT_TOKEN", ""), secret=True),
        "ANTHROPIC_API_KEY": ask("2) Anthropic API 키 (sk-ant-...)", os.environ.get("ANTHROPIC_API_KEY", ""), secret=True),
        "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID", ""),
    }
    token = values["TELEGRAM_BOT_TOKEN"]
    if not token:
        print("봇 토큰은 필수입니다.")
        return 1

    me = get_me(token)
    print(f"✅ 봇 확인: @{me.get('username')} ({me.get('first_name')})")
    delete_webhook(token)  # webhook 이 걸려 있으면 getUpdates 가 409 로 실패하므로 해제

    offset = None
    if not values["TELEGRAM_CHAT_ID"]:
        values["TELEGRAM_CHAT_ID"], offset = find_chat_id(token, me.get("username", ""))
    write_env(values)
    print(f"✅ 로컬 설정 저장: {ENV_FILE} (git에는 올라가지 않음)")

    # 설정 중 받은 메시지는 체크인으로 읽지 않도록 offset을 그 뒤로 둔다
    state = checkin.load_state()
    if offset is not None and (state.get("offset") or 0) < offset:
        state["offset"] = offset
    checkin.save_state(state)

    url = page_url(load_config())
    text = ("✅ <b>데일리 비서 설정 완료</b>\n내일 아침 7시부터 이 채팅방으로 브리핑이 도착합니다.\n\n"
            "체크인은 이 채팅에 답장으로:\n<code>운동: O/X\n업무: 오늘 핵심 1~3개\n언어공부: O/X</code>")
    try:
        send_message(token, values["TELEGRAM_CHAT_ID"], text)
        print("✅ 테스트 메시지를 보냈습니다. 텔레그램을 확인하세요.")
    except Exception as exc:
        print(f"❌ 테스트 발송 실패: {exc}")
        return 1

    push_to_github(values)
    print(f"\n설정이 끝났습니다. 브리핑 페이지: {url or '(GitHub Actions 첫 실행 뒤 생성)'}")
    print("GitHub 저장소 Actions 탭에서 'Daily assistant briefing' → Run workflow 로 바로 한 번 보내볼 수 있습니다.")
    print("state/telegram_state.json 이 바뀌었으면 커밋해 두세요 (git add state && git commit).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
