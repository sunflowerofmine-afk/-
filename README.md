# Korea Close Betting Bot

한국 주식 종가베팅용 데이터 수집 / 후보 선별 / 텔레그램 알림 시스템.
GitHub Actions로 로컬 PC 없이 자동 실행됩니다.

---

## 동작 흐름

```
[GitHub Actions 스케줄]
        ↓
[비거래일 체크] → 비거래일이면 텔레그램 알림 후 종료
        ↓
[전 종목 수집] 코스피 + 코스닥 (네이버 증권)
        ↓
[제외 필터] ETF, 스팩, 우선주 등 제거
        ↓
[랭킹 계산] 상승률 Top20 / 거래대금 Top20 / 교집합
        ↓
[1차 알림 - 14:50] 빠른 스캔 결과 전송
        ↓ (17:50 실행 시)
[후보 지표 계산] 이평선, 거래량 60일 최고, 패턴
[수급 / 뉴스 수집]
        ↓
[2차 알림 - 17:50] 상세 분석 결과 전송
        ↓
[데이터 저장] CSV + GitHub Artifact
```

---

## 폴더 구조

```
korea-close-betting-bot/
├── .github/workflows/run_bot.yml   # GitHub Actions 워크플로
├── config/settings.py              # 전체 설정값
├── data/
│   ├── raw/        ← 수집 원본 (YYYYMMDD_HHMM_KOSPI.csv)
│   ├── processed/  ← 가공 데이터 (Top20, 교집합)
│   ├── signals/    ← 텔레그램 발송 후보 기록
│   └── results/    ← 사후 성과 저장 (수동 입력)
├── logs/           ← 실행 로그
├── scripts/
│   ├── market_calendar.py   # 거래일 판단
│   ├── storage.py           # CSV 저장/로드
│   ├── fetch_market_data.py # 네이버 전 종목 수집
│   ├── fetch_stock_data.py  # 개별 종목 OHLCV 히스토리
│   ├── fetch_supply_data.py # 기관/외국인 수급
│   ├── fetch_news.py        # 종목 뉴스
│   ├── indicators.py        # 기술적 지표 계산
│   ├── pattern_detector.py  # 패턴 탐지
│   ├── ranking.py           # Top20 / 교집합
│   ├── notifier.py          # 텔레그램 전송
│   └── pipeline.py          # 전체 파이프라인
└── requirements.txt
```

---

## GitHub Secrets 설정

레포지토리 → Settings → Secrets and variables → Actions → **New repository secret**

| Secret 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급한 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 봇과 대화 후 getUpdates로 확인한 숫자 ID |

---

## GitHub Actions 활성화

1. 이 레포지토리를 GitHub에 push
2. Actions 탭 → 워크플로 활성화 확인
3. 수동 테스트: **Run workflow** 클릭

> **주의:** GitHub Actions의 schedule cron은 UTC 기준입니다.
> KST 14:50 = UTC 05:50, KST 17:50 = UTC 08:50으로 설정되어 있습니다.
> 서버 부하에 따라 실제 실행이 최대 10~15분 지연될 수 있습니다.

---

## 수동 실행 (로컬)

```bash
# 1. 패키지 설치
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env  # .env 파일에 토큰 입력

# 3. 실행
python -m scripts.pipeline
```

---

## 데이터 저장 위치

| 종류 | 경로 | 예시 파일명 |
|---|---|---|
| 원본 수집 | `data/raw/` | `2026-04-16_1450_KOSPI.csv` |
| Top20 | `data/processed/` | `2026-04-16_1450_top_gainers.csv` |
| 발송 후보 | `data/signals/` | `2026-04-16_1750_signals.csv` |
| 사후 성과 | `data/results/` | 수동 입력 |

GitHub Actions 실행 후 **Artifacts**에서 data/, logs/ 폴더 전체 다운로드 가능 (30일 보관).

---

## 텔레그램 메시지 예시

```
[종가베팅 스캔 - 2026-04-16 17:50 KST (2차)]

[시장 요약]
코스피 거래대금: 12,345억
코스닥 거래대금: 8,234억

[상승률 Top20]
1) 삼성전자(005930) [KOSPI] +5.23% | 5,200억 | 거래량60최고:O | 뉴스:O
2) SK하이닉스(000660) [KOSPI] +4.11% | 3,100억 | ...

[거래대금 Top20]
1) 에코프로(086520) [KOSDAQ] 8,500억 | +12.3% | ...

[상승률 Top20 ∩ 거래대금 Top20]
1) 에코프로(086520) | +12.3% | 8,500억 | 패턴:패턴1 | 수급:기관 +320억

[핵심 후보]
1) 에코프로(086520) [KOSDAQ]
- 패턴: 패턴1(첫 장대양봉 돌파형)
- 상승률: +12.3%
- 거래대금: 8,500억
- 장대양봉: O / 준장대양봉: O / 첫장대양봉: O
- 이평밀집: O
- 수급: 기관 +320억 / 외국인 +150억
- 거래량60최고: O / 거래대금60최고: O
- 뉴스: [수주]공급계약 체결 발표
```

---

## 비거래일/휴장일 처리

- 토요일, 일요일은 자동으로 수집 건너뜀
- `scripts/market_calendar.py`의 `KRX_HOLIDAYS` 셋에 매년 KRX 공식 휴장일 추가 필요
- 비거래일 실행 시 텔레그램으로 "비거래일" 알림 전송

---

## 설정값 수정 (`config/settings.py`)

| 설정 | 기본값 | 설명 |
|---|---|---|
| `MIN_TRADING_VALUE_EOK` | 1500 | 핵심 후보 최소 거래대금 (억) |
| `BIG_CANDLE_MIN_PCT` | 15.0 | 장대양봉 최소 상승률 (%) |
| `TOP_GAINERS_COUNT` | 20 | 상승률 상위 추출 수 |
| `ENABLE_NEWS_FETCH` | True | 뉴스 수집 on/off |
| `ENABLE_SUPPLY_FETCH` | True | 수급 수집 on/off |

---

## GitHub Pages 대시보드

매 실행마다 HTML 리포트를 생성해 GitHub Pages로 확인할 수 있습니다.

### 1. GitHub Pages 활성화

레포지토리 → **Settings** → **Pages**
- Source: `Deploy from a branch`
- Branch: `main` / `(root)` 선택 → **Save**

수 분 후 아래 주소에서 Pages가 활성화됩니다:
```
https://{USERNAME}.github.io/{REPOSITORY}/
```

### 2. GITHUB_PAGES_BASE_URL 설정

레포 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름 | 값 예시 |
|---|---|
| `GITHUB_PAGES_BASE_URL` | `https://sunflowerofmine-afk.github.io/-` |

### 3. 대시보드 링크 구조

```
reports/
├── 2026-04-17_1450.html   ← 날짜별 1차 리포트
├── 2026-04-17_1750.html   ← 날짜별 2차 리포트
├── latest_1450.html       ← 항상 최신 1차 리포트
└── latest_1750.html       ← 항상 최신 2차 리포트
```

텔레그램 메시지 하단에 링크가 자동 포함됩니다:
```
[상세 대시보드]
- 최신: https://USERNAME.github.io/REPOSITORY/reports/latest_1750.html
- 날짜별: https://USERNAME.github.io/REPOSITORY/reports/2026-04-17_1750.html
```

### 4. 대시보드가 안 열릴 때

1. GitHub Pages가 활성화됐는지 확인 (Settings → Pages)
2. Actions 실행이 완료됐는지 확인 (커밋이 있어야 Pages 반영)
3. Actions 탭에서 `Commit dashboard reports` 스텝 성공 여부 확인
4. `GITHUB_PAGES_BASE_URL` Secret이 올바르게 설정됐는지 확인

---

## 주의사항

- 네이버 증권 페이지 구조가 변경되면 파서 수정 필요 (`fetch_market_data.py`)
- 14:50 데이터는 장중 잠정치, 17:50은 확정치에 가까움
- GitHub Actions 무료 계정: 월 2,000분 제공 (1회 실행 약 5분 → 충분)
- `.env` 파일은 절대 GitHub에 push하지 말 것

---

## 추후 확장 포인트

- `data/` CSV → SQLite 전환 (`storage.py`만 수정)
- `data/results/` 활용한 백테스트 스크립트 추가
- 조건 변경 시 `config/settings.py`만 수정
- 뉴스 API (Naver News API) 연동으로 정확도 향상
