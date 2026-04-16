from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv()

# ── 경로 ──────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent.parent
DATA_DIR       = BASE_DIR / "data"
RAW_DIR        = DATA_DIR / "raw"
PROCESSED_DIR  = DATA_DIR / "processed"
LOG_DIR        = BASE_DIR / "logs"

# 디렉터리 자동 생성
for _dir in (RAW_DIR, PROCESSED_DIR, LOG_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ── 텔레그램 ───────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── 수집 설정 ──────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}

# 시장 코드: 0=코스피, 1=코스닥
MARKETS = {
    "KOSPI":  0,
    "KOSDAQ": 1,
}

# ── 필터 조건 (filter_stocks.py에서 사용) ─────────────────
FILTER = {
    "min_change_rate":    3.0,           # 등락률 최소 (%)
    "min_volume":         500_000,       # 최소 거래량
    "min_trade_amount":   5_000_000_000, # 최소 거래대금 (원)
}

# ── 스케줄 시간 ────────────────────────────────────────────
SCHEDULE_TIMES = ["15:00", "18:00"]
