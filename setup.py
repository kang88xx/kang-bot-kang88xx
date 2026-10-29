"""최초 1회 설정 마법사: 키 입력 → 카카오 로그인 → 토큰 암호화 저장 → 테스트 발송 → GitHub 시크릿 등록.

    python setup.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from cryptography.fernet import Fernet

from common import ROOT, load_config, load_dotenv, page_url

ENV_FILE = ROOT / ".env"
REDIRECT_URI = "http://localhost:8765/callback"
AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
SECRET_NAMES = ["KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "ANTHROPIC_API_KEY", "TOKEN_ENC_KEY"]


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    shown = f" [{'설정됨' if secret and default else default}]" if default else ""
    value = input(f"{prompt}{shown}: ").strip()
    return value or default


def write_env(values: dict) -> None:
    lines = [f"{k}={v}" for k, v in values.items() if v is not None]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.update({k: v for k, v in values.items() if v})


def open_browser(url: str) -> None:
    try:
        if webbrowser.open(url):
            return
    except Exception:
        pass
    if shutil.which("cmd.exe"):  # WSL → 윈도우 기본 브라우저
        try:
            subprocess.run(["cmd.exe", "/c", "start", "", url], check=False, timeout=10)
        except Exception:
            pass


def wait_for_code(timeout: int = 300) -> str | None:
    """localhost:8765 에서 ?code= 를 받을 때까지 대기."""
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = "✅ 카카오 로그인 완료. 이 창을 닫고 터미널로 돌아가세요."
            else:
                result["error"] = qs.get("error_description", qs.get("error", ["?"]))[0]
                body = f"❌ 로그인 실패: {result['error']}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<meta charset='utf-8'><h2>{body}</h2>".encode())

        def log_message(self, *_):
            pass

    try:
        server = HTTPServer(("127.0.0.1", 8765), Handler)
    except OSError as exc:
        print(f"(로컬 서버를 열 수 없음: {exc}. 수동 입력으로 진행)")
        return None
    server.timeout = 1
    deadline = __import__("time").time() + timeout
    while not result and __import__("time").time() < deadline:
        server.handle_request()
    server.server_close()
    if "error" in result:
        print(f"카카오 로그인 오류: {result['error']}")
    return result.get("code")


def kakao_login() -> None:
    from tokens import exchange_code, save_tokens

    url = AUTH_URL + "?" + urlencode({
        "client_id": os.environ["KAKAO_REST_API_KEY"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "talk_message",
    })
    print("\n브라우저가 열리면 카카오 계정으로 로그인하고 '카카오톡 메시지 전송'에 동의하세요.")
    print(f"자동으로 열리지 않으면 이 주소를 브라우저에 붙여넣으세요:\n\n  {url}\n")
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    code = wait_for_code()
    if not code:
        raw = input("로그인 후 이동한 주소(http://localhost:8765/callback?code=...)를 그대로 붙여넣으세요: ").strip()
        code = parse_qs(urlparse(raw).query).get("code", [raw])[0]
    tokens = exchange_code(code, REDIRECT_URI)
    save_tokens(tokens)
    print("✅ 카카오 토큰 저장 완료 (state/kakao_token.enc, 암호화됨)")


def test_send(url: str) -> bool:
    from kakao import send_text
    from tokens import get_access_token

    try:
        send_text(get_access_token(), "✅ 아침 뉴스 브리핑 설정이 완료됐습니다.\n내일 아침 7시부터 이 채팅방으로 뉴스가 도착합니다.", url, "페이지 열기")
        print("✅ 테스트 메시지를 보냈습니다. 카카오톡 '나와의 채팅'을 확인하세요.")
        return True
    except Exception as exc:
        print(f"❌ 테스트 발송 실패: {exc}")
        print("   → 카카오 개발자 콘솔에서 '카카오톡 메시지 전송' 동의항목과 Web 플랫폼 도메인 등록을 확인하세요.")
        return False


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
        subprocess.run(["gh", "secret", "set", name, "--body", value], cwd=ROOT, check=True)
        print(f"  - {name} 등록")
    subprocess.run(["git", "add", "state/kakao_token.enc"], cwd=ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if diff.returncode != 0:
        subprocess.run(["git", "commit", "-m", "chore: 카카오 토큰 등록"], cwd=ROOT, check=True)
    subprocess.run(["git", "push"], cwd=ROOT, check=True)
    print("✅ GitHub에 토큰 파일 푸시 완료")


def main() -> int:
    load_dotenv()
    print("=== 아침 뉴스 브리핑 설정 ===\n(값을 그대로 두려면 Enter)\n")
    values = {
        "KAKAO_REST_API_KEY": ask("1) 카카오 REST API 키", os.environ.get("KAKAO_REST_API_KEY", ""), secret=True),
        "KAKAO_CLIENT_SECRET": ask("2) 카카오 Client Secret (사용 안 하면 Enter)", os.environ.get("KAKAO_CLIENT_SECRET", ""), secret=True),
        "ANTHROPIC_API_KEY": ask("3) Anthropic API 키 (sk-ant-...)", os.environ.get("ANTHROPIC_API_KEY", ""), secret=True),
        "TOKEN_ENC_KEY": os.environ.get("TOKEN_ENC_KEY") or Fernet.generate_key().decode(),
    }
    if not values["KAKAO_REST_API_KEY"]:
        print("REST API 키는 필수입니다.")
        return 1
    write_env(values)
    print(f"✅ 로컬 설정 저장: {ENV_FILE} (git에는 올라가지 않음)")

    cfg = load_config()
    url = page_url(cfg)
    if not url:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        if "github.com" in remote:
            path = remote.split("github.com")[-1].lstrip(":/").removesuffix(".git")
            owner, _, name = path.partition("/")
            url = f"https://{owner.lower()}.github.io/{name}/"
    print(f"브리핑 페이지 주소: {url or '(알 수 없음 — config.yaml의 page_url을 채우세요)'}")
    if url:
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        print(f"※ 카카오 개발자 콘솔 > 앱 설정 > 플랫폼 > Web 사이트 도메인에 {host} 가 등록되어 있어야 링크 버튼이 동작합니다.")

    kakao_login()
    test_send(url or "https://github.com")
    push_to_github(values)
    print("\n설정이 끝났습니다. GitHub 저장소 Actions 탭에서 'Morning news briefing' → Run workflow 로 바로 한 번 보내볼 수 있습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
