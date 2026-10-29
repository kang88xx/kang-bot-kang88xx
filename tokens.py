"""카카오 토큰 저장(암호화)과 갱신."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from cryptography.fernet import Fernet

from common import ROOT

TOKEN_FILE = ROOT / "state" / "kakao_token.enc"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def _fernet() -> Fernet:
    key = os.environ.get("TOKEN_ENC_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENC_KEY 환경변수가 없습니다 (setup.py를 먼저 실행하세요).")
    return Fernet(key.encode())


def load_tokens() -> dict:
    if not TOKEN_FILE.exists():
        raise RuntimeError(f"{TOKEN_FILE} 가 없습니다. setup.py로 카카오 로그인을 먼저 하세요.")
    return json.loads(_fernet().decrypt(TOKEN_FILE.read_bytes()))


def save_tokens(tokens: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(_fernet().encrypt(json.dumps(tokens).encode()))


def _client_params() -> dict:
    params = {"client_id": os.environ["KAKAO_REST_API_KEY"]}
    secret = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()
    if secret:
        params["client_secret"] = secret
    return params


def _apply(tokens: dict, body: dict) -> dict:
    now = time.time()
    tokens["access_token"] = body["access_token"]
    tokens["access_expires_at"] = now + int(body.get("expires_in", 0))
    if body.get("refresh_token"):
        tokens["refresh_token"] = body["refresh_token"]
        tokens["refresh_expires_at"] = now + int(body.get("refresh_token_expires_in", 0))
    return tokens


def exchange_code(code: str, redirect_uri: str) -> dict:
    data = {"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code, **_client_params()}
    resp = requests.post(TOKEN_URL, data=data, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"토큰 발급 실패 {resp.status_code}: {resp.text}")
    return _apply({}, resp.json())


def refresh(tokens: dict) -> dict:
    data = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], **_client_params()}
    resp = requests.post(TOKEN_URL, data=data, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"토큰 갱신 실패 {resp.status_code}: {resp.text}\n"
                           "리프레시 토큰이 만료됐을 수 있습니다. setup.py를 다시 실행해 로그인하세요.")
    return _apply(tokens, resp.json())


def get_access_token() -> str:
    """유효한 액세스 토큰 반환. 만료(5분 전 포함)면 갱신 후 저장."""
    tokens = load_tokens()
    if time.time() > tokens.get("access_expires_at", 0) - 300:
        tokens = refresh(tokens)
        save_tokens(tokens)
        left_days = (tokens.get("refresh_expires_at", 0) - time.time()) / 86400
        print(f"[kakao] 토큰 갱신 완료 (리프레시 토큰 남은 기간: {left_days:.0f}일)")
    return tokens["access_token"]
