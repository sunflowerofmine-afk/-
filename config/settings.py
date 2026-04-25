# config/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── 경로 ──────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
DATA_DIR      = BASE_DIR / "data"
RAW_DIR       = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SIGNALS_DIR   = DATA_DIR / "signals"
RESULTS_DIR   = DATA_DIR / "results"
LOG_DIR       = BASE_DIR / "logs"
REPORTS_DIR   = BASE_DIR / "reports"

for _d in [RAW_DIR, PROCESSED_DIR, SIGNALS_DIR, RESULTS_DIR, LOG_DIR, REPORTS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── 텔레그램 ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── 수집 설정 ──────────────────────────────────────────────
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)
NAVER_REQUEST_TIMEOUT = int(os.getenv("NAVER_REQUEST_TIMEOUT", "10"))
REQUEST_TIMEOUT = NAVER_REQUEST_TIMEOUT
REQUEST_DELAY   = float(os.getenv("REQUEST_DELAY", "0.3"))
TZ = os.getenv("TZ", "Asia/Seoul")

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}

MARKETS = {"KOSPI": 0, "KOSDAQ": 1}

# ── 제외 키워드 (종목명 포함 기준) ────────────────────────
EXCLUDE_KEYWORDS = [
    "스팩", "SPAC", "ETF", "ETN", "리츠", "REITs",
    "인버스", "레버리지", "선물", "채권", "TRF", "TDF",
    # ETF 브랜드명 (ETF 키워드 없이 브랜드명만 표시되는 경우 대비)
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "KOSEF",
    "TIMEFOLIO", "ACE ", "SOL ", "RISE ",
]

# ── 우선주 제외 (종목명 끝 기준) ───────────────────────────
PREFERRED_STOCK_SUFFIXES = ["우", "1우", "2우", "우B", "우C"]

# ── 기술적 조건 설정값 ─────────────────────────────────────
FIRST_BIG_CANDLE_LOOKBACK_DAYS      = 60
BIG_CANDLE_MIN_PCT                  = 15.0
LOOSE_BIG_CANDLE_MIN_PCT            = 10.0
MA_CLUSTER_5_10_20_MAX_GAP_PCT      = 5.0
MA_CLUSTER_5_10_20_60_MAX_GAP_PCT   = 8.0
PULLBACK_MAX_DROP_PCT               = -2.0
HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT = 5.0   # 고가 유지 기준: 기준봉 고가 대비 -5% 이내
HIGH_RANGE_HOLD_DAYS                = 3
STRUCTURE_BREAK_MAX_GAP_PCT         = 8.0   # 구조 붕괴 기준: 기준봉 고가 대비 -8% 초과
TV_RATIO_OK_MIN                     = 0.4   # 거래대금 ratio 정상 감소 기준 (기준봉 대비 40% 이상)
TV_RATIO_WATCH_MIN                  = 0.2   # 당일돌파형 거래대금 ratio 최소값
TV_RATIO_P2P3_MIN                   = 0.05  # 고가횡보형/눌림관찰형 거래대금 ratio 최소값 (눌림 = 적은 거래대금이 건강)
BIG_CANDLE_UPPER_TAIL_MAX           = 0.25  # 장대양봉 윗꼬리 최대 비율 (전체 캔들 범위 대비)
LOOSE_BIG_CANDLE_UPPER_TAIL_MAX     = 0.35  # 준장대양봉 윗꼬리 최대 비율
BASE_TV_EXPLOSION_MULT              = 3.0   # 기준봉 거래대금 폭발 기준: 이전 20일 평균 대비 배수
CONSOLIDATION_LOOKBACK_DAYS         = 20    # 기간조정 패턴: 횡보 기준 일수
CONSOLIDATION_MAX_RANGE_PCT         = 15.0  # 기간조정 패턴: 최대 변동폭 (%)
PULLBACK_RESISTANCE_LOOKBACK_DAYS   = 25    # 되돌림 지지: 저항선 탐색 범위 (일)
PULLBACK_RESISTANCE_RECENT_DAYS     = 5     # 되돌림 지지: 저항선 돌파 확인 구간 (일)
PULLBACK_RETEST_MARGIN_PCT          = 5.0   # 되돌림 지지: 저항선 근처 허용 오차 (%)
MARKET_REGIME_BULL_ADL              = 0.55  # 강세 판단: 상승 종목 비율 (상승+하락 종목 수 대비)
MARKET_REGIME_BEAR_ADL              = 0.40  # 약세 판단: 상승 종목 비율
MARKET_REGIME_BULL_TV1500           = 3     # 강세 판단: 1500억↑ 종목 최소 수
VOLUME_PEAK_LOOKBACK_DAYS           = 60
TRADING_VALUE_PEAK_LOOKBACK_DAYS    = 60
MIN_TRADING_VALUE_EOK               = 1500   # 억원 단위
TV_SCORE_3_MIN_EOK                  = 10000  # 거래대금 3점 기준: 1조↑
TV_SCORE_2_MIN_EOK                  = 5000   # 거래대금 2점 기준: 5천억↑
TV_SCORE_1_MIN_EOK                  = 1000   # 거래대금 1점 기준: 1천억↑
TOP_GAINERS_COUNT                   = 20
TOP_TRADING_VALUE_COUNT             = 20
ENABLE_NEWS_FETCH                   = True
ENABLE_SUPPLY_FETCH                 = True
GEMINI_API_KEY                      = os.getenv("GEMINI_API_KEY", "")
# ── 1차 필터 조건 ──────────────────────────────────────────
MIN_PRICE                           = 1000
MIN_CHANGE_PCT                      = -5.0
MAX_CHANGE_PCT                      = 30.0

# ── 뉴스 점수화 ────────────────────────────────────────────
NEWS_SCORE_ENABLED                  = True
USE_LLM_NEWS                        = True
GEMINI_MODEL                        = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── 대시보드 / GitHub Pages ─────────────────────────────────
GITHUB_PAGES_BASE_URL               = os.getenv("PAGES_BASE_URL", "")
ENABLE_DASHBOARD                    = True
ENABLE_GITHUB_PAGES_LINK            = True

# ── 섹터 수집 ──────────────────────────────────────────────
ENABLE_SECTOR_FETCH                 = True
SECTOR_TOP_N                        = 5   # 거래대금 상위 N개 섹터

# ── NXT 수집 (2차/수동 실행 시 KRX 데이터에 합산) ─────────────
ENABLE_NXT_FETCH                    = True
