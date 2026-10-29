# ☀️ 아침 뉴스 브리핑 → 카카오톡

매일 아침 7시(한국시간), 종합·경제/증시·IT/테크(+관심 키워드) 뉴스를 Claude가 선별·요약해
**카카오톡 '나와의 채팅'** 으로 보내고, 전체 요약은 GitHub Pages 웹페이지로 볼 수 있습니다.

- 실행: GitHub Actions (PC가 꺼져 있어도 동작, 무료)
- 발송: 카카오 '나에게 보내기' API (무료, 본인 카톡으로만 발송)
- 요약: Claude API (유료, 하루 1회 호출)
- 카톡 메시지: 섹션당 1개(헤드라인 3개 + '전체 보기' 버튼). 카톡 텍스트 200자 제한 때문에 상세 요약은 웹페이지에 실립니다.

## 최초 설정 (한 번만)

### 1. 카카오 개발자 앱 만들기 (약 5분)

1. https://developers.kakao.com → 로그인 → **내 애플리케이션 → 애플리케이션 추가하기** (앱 이름/회사명은 아무거나)
2. **앱 설정 → 앱 키**에서 **REST API 키** 복사
3. **앱 설정 → 플랫폼 → Web** 에 사이트 도메인 등록: `https://kang88xx.github.io`
   (메시지의 링크 버튼이 이 도메인을 가리키므로 꼭 필요)
4. **제품 설정 → 카카오 로그인** → 활성화 **ON**
   → **Redirect URI** 에 `http://localhost:8765/callback` 등록
5. **제품 설정 → 카카오 로그인 → 동의항목** → **카카오톡 메시지 전송(talk_message)** → **선택 동의** 로 설정
   - 이 항목이 비활성화돼 있으면 **앱 설정 → 일반 → 비즈 앱 전환**(개인 개발자, 무료)을 먼저 하세요.
6. (선택) **제품 설정 → 카카오 로그인 → 보안 → Client Secret** 을 켰다면 그 값도 복사. 끄고 쓰는 게 간단합니다.

### 2. Anthropic API 키

https://platform.claude.com → API Keys → 키 생성(`sk-ant-...`). 결제 수단 등록 후 최소 $5 충전.
(Opus 5.5 기준 하루 약 50~100원 수준. 비용은 콘솔 Usage에서 확인)

### 3. 설정 마법사 실행 (WSL 터미널)

```bash
cd /mnt/c/Users/kslee/Desktop/bise
uv venv .venv --python 3.12 && source .venv/bin/activate   # 또는 python3 -m venv .venv
uv pip install -r requirements.txt                          # 또는 pip install -r requirements.txt
python setup.py
```

마법사가 하는 일: 키 입력 → 브라우저로 카카오 로그인 → 토큰을 암호화해 `state/kakao_token.enc`에 저장
→ 테스트 메시지 발송 → GitHub 시크릿 4개 등록(`gh` CLI 사용) → 토큰 파일 푸시.

### 4. 바로 한 번 보내보기

GitHub 저장소 → **Actions → Morning news briefing → Run workflow**. 1~2분 뒤 카톡이 옵니다.
이후에는 매일 아침 7시에 자동 실행됩니다.

## 설정 바꾸기 (`config.yaml`)

| 항목 | 설명 |
|---|---|
| `keywords` | 관심 키워드 목록. 키워드마다 구글뉴스 검색 섹션이 추가됨 (예: `- 반도체`) |
| `sections[].feeds` | 섹션별 RSS 피드. 추가/삭제 가능 |
| `per_section` | 섹션당 웹페이지에 실을 기사 수 (기본 5) |
| `kakao_top` | 카톡 메시지에 넣을 헤드라인 수 (기본 3, 200자 제한) |
| `model`, `effort` | 요약 모델과 노력도. `claude-haiku-5-5`로 바꾸면 비용이 1/40 수준 |
| `max_candidates` | Claude에게 넘기는 섹션당 후보 수. 줄이면 비용 감소 |

발송 시각은 `.github/workflows/morning.yml`의 `cron` (UTC 기준, 한국시간 −9시간)을 고치면 됩니다.
예: 오전 6시 → `"0 21 * * *"`, 오전 8시 → `"0 23 * * *"`.

## 로컬에서 테스트

```bash
python main.py --skip-claude --dry-run   # 수집 + 페이지 생성만 (API 키 불필요)
python main.py --dry-run                 # Claude 요약까지 (카톡 발송은 생략)
python main.py                           # 실제 발송
```

## 동작 원리

```
RSS 수집(news_fetch.py) → Claude 선별·요약(summarize.py) → docs/index.html 생성(page.py)
→ 카카오 토큰 갱신(tokens.py) → 섹션별 카톡 발송(kakao.py) → 토큰·페이지 커밋(워크플로)
```

- 카카오 리프레시 토큰은 약 2개월 유효하고, 만료 1개월 전부터 새 토큰이 발급됩니다.
  매일 실행하면서 갱신된 토큰을 암호화해 저장소에 다시 커밋하므로 **재로그인 없이 계속 동작**합니다.
- 토큰 파일은 `TOKEN_ENC_KEY`(GitHub 시크릿)로 암호화돼 있어 저장소가 공개여도 안전합니다.
- 한 달 넘게 워크플로가 멈춰 있었다면 토큰이 만료될 수 있습니다. 그때는 `python setup.py`를 다시 실행하세요.

## 문제 해결

- **카톡이 안 와요**: Actions 탭에서 실패한 실행의 로그를 보세요. `토큰 갱신 실패`면 `setup.py` 재실행, `카카오 전송 실패`면 동의항목/도메인 등록 확인.
- **링크 버튼이 안 눌려요**: 카카오 앱 플랫폼(Web)에 `https://kang88xx.github.io` 가 등록되어 있는지 확인.
- **GitHub Actions가 60일간 활동이 없으면 예약 실행을 멈춥니다**: 저장소에 아무 커밋이나 하면 다시 켜집니다
  (매일 페이지를 커밋하므로 보통은 해당 없음).
