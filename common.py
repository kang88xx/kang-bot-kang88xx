"""공통 유틸: .env 로딩, 설정 읽기, 페이지 URL 계산."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """간단한 .env 로더 (이미 설정된 환경변수는 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("timezone", "Asia/Seoul")
    cfg.setdefault("model", "claude-opus-5-5")
    cfg.setdefault("effort", "low")
    cfg.setdefault("max_age_hours", 24)
    cfg.setdefault("max_candidates", 25)
    cfg.setdefault("per_section", 5)
    cfg.setdefault("kakao_top", 3)
    cfg.setdefault("sections", [])
    cfg.setdefault("keywords", []) 
    cfg["keywords"] = [k for k in (cfg.get("keywords") or []) if k]
    return cfg


def page_url(cfg: dict) -> str:
    """브리핑 페이지 주소. config > PAGE_URL 환경변수 > GitHub Pages 기본 주소 순."""
    url = (cfg.get("page_url") or "").strip() or os.environ.get("PAGE_URL", "").strip()
    if not url:
        repo = os.environ.get("GITHUB_REPOSITORY", "")  # owner/repo
        if "/" in repo:
            owner, name = repo.split("/", 1)
            url = f"https://{owner.lower()}.github.io/{name}/"
    if url and not url.endswith("/"):
        url += "/"
    return url
