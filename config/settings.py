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
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_ID_DEV = os.getenv("TELEGRAM_CHAT_ID_DEV", "")  # --preview 모드 전용 (본인 DM)

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
FIRST_BIG_CANDLE_LOOKBACK_DAYS      = 30
BIG_CANDLE_MIN_PCT                  = 15.0
LOOSE_BIG_CANDLE_MIN_PCT            = 10.0
MA_CLUSTER_5_10_20_MAX_GAP_PCT      = 5.0
MA_CLUSTER_5_10_20_60_MAX_GAP_PCT   = 8.0
PULLBACK_MAX_DROP_PCT               = -4.0
HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT = 6.0   # 고가 유지 기준(하한): 기준봉 고가 대비 -6% 이내 (5%→6%: 진단 결과 근소 탈락 케이스 반영)
OVERHEATED_GAP_FROM_BASE_HIGH_PCT          = 8.0   # 과확장 기준(상한): 기준봉 고가 대비 +8% 초과 시 과확장 (5%→8%: 대세 상승장 완화)
HIGH_RANGE_HOLD_DAYS                = 3
STRUCTURE_BREAK_MAX_GAP_PCT         = 8.0   # 구조 붕괴 기준: 기준봉 고가 대비 -8% 초과
INTRADAY_CLOSE_FROM_HIGH_MIN_PCT    = -5.0  # 당일 고가 대비 종가 최소값 (이격 초과 시 탈락)
TV_RATIO_OK_MIN                     = 0.4   # 거래대금 ratio 정상 감소 기준 (기준봉 대비 40% 이상)
TV_RATIO_WATCH_MIN                  = 0.1   # 고가횡보형 거래대금 ratio 최소값 (0.3→0.1: 건강한 수축도 횡보로 인정)
TV_RATIO_P3_MAX                     = 0.7   # 고가횡보형 거래대금 ratio 최대값 (기준봉 대비 70% 초과 시 재상승 판정, 횡보 탈락)
TV_RATIO_P2P3_MIN                   = 0.05  # 고가횡보형/고가수축형 거래대금 ratio 최소값 (수축/횡보 = 적은 거래대금이 건강)
BIG_CANDLE_CLOSE_FROM_HIGH_MIN_PCT       = -5.0  # 장대양봉 고가 대비 종가 최소값 (%)
LOOSE_BIG_CANDLE_CLOSE_FROM_HIGH_MIN_PCT = -5.0  # 준장대양봉 고가 대비 종가 최소값 (%)
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

# ── DART 공시 수집 ──────────────────────────────────────────
DART_API_KEY                        = os.getenv("DART_API_KEY", "")
ENABLE_DART_FETCH                   = True   # 2차/수동 실행 시 후보 종목 당일 공시 조회

ENABLE_SHORT_BALANCE                = True   # 공매도 잔고 추적 (pykrx, T+2)
ENABLE_PENSION_FETCH                = True   # 연기금 순매수 추적 (pykrx, T-1)

# ── 장세별 핵심 후보 상한선 ──────────────────────────────────
CANDIDATES_MAX_BULL               = 5   # 강세
CANDIDATES_MAX_NEUTRAL            = 3   # 중립
CANDIDATES_MAX_BEAR               = 2   # 약세 (전체하락형·혼조형)
CANDIDATES_MAX_CONCENTRATED_BEAR  = 3   # 약세 + 자금집중형 (지수 강세 & ADL 약세)

# ── 고가수축형(HTC) 패턴 파라미터 ─────────────────────────────
HTC_BASE_LOOKBACK_DAYS                   = 5      # 기준봉 탐색 범위 (일) — OBS pool 추적 5일과 일치
HTC_POST_AVG_TV_RATIO_MAX                = 0.6    # 기준봉 이후 평균 거래대금 ≤ 기준봉 × 0.6 (0.5→0.6: 대세 상승장 완화)
HTC_TODAY_TV_RATIO_MAX                   = 0.6    # 오늘 거래대금 ≤ 기준봉 × 0.6 (0.5→0.6: 대세 상승장 완화)
HTC_MIN_TODAY_TV_EOK                     = 300    # 오늘 거래대금 최소값 (억)
HTC_CLOSE_FROM_BASE_HIGH_MIN_PCT         = -6.0   # 오늘 종가 ≥ 기준봉 고가 × (1 - 6%) (5%→6%: 진단 결과 반영)
HTC_CLOSE_FROM_BASE_CLOSE_MIN_PCT        = -5.0   # 오늘 종가 ≥ 기준봉 종가 × (1 - 5%)
HTC_LOWEST_CLOSE_FROM_BASE_CLOSE_MIN_PCT = -8.0   # 기준봉 이후 최저 종가 ≥ 기준봉 종가 × (1 - 8%) (–5→–8: 중간일 단기 눌림 허용)
HTC_RANGE_MAX_PCT                        = 10.0   # 기준봉 이후 고가~저가 변동폭 ≤ 10%
HTC_CLOSE_RANGE_MAX_PCT                  = 10.0   # 기준봉 이후 종가 변동폭 ≤ 10% (7→10: 2~3일 수축에 현실적 기준)
HTC_STRUCTURE_BREAK_FROM_BASE_HIGH_PCT   = -8.0   # 중간 거래일 종가 기준봉 고가 대비 -8% 초과 시 구조 붕괴
HTC_BREAKDOWN_CANDLE_CHANGE_MIN_PCT      = -5.0   # 구조붕괴 장대음봉 등락률 기준
HTC_BREAKDOWN_CANDLE_TV_RATIO_MIN        = 0.5    # 구조붕괴 장대음봉 거래대금 기준 (기준봉 대비)

# ── 김형준 기법 (KH) 파라미터 ─────────────────────────────────
KH_BASE_TV_EXPLOSION_MULT       = 3.0    # 기준봉 거래대금 폭발 배수 (이전 20일 평균 대비). 표본 부족 시 2.5→2.0으로 완화 검토.
KH_BASE_TV_MIN_EOK              = 700    # 기준봉 거래대금 최소 (억원) — 강의 근거 없음, 3배 폭발 조건이 방어
KH_TODAY_TV_RATIO_MAX           = 0.65   # 오늘 TV ≤ 기준봉 × 65% (0.5→0.65: 현대오토에버 미스 사례 반영)
KH_CLOSE_FROM_BASE_HIGH_MIN_PCT = -8.0   # 오늘 종가 ≥ 기준봉 고가 × 92% (2~5일 눌림 허용)
KH_BASE_LOOKBACK_DAYS           = 10     # 기준봉 탐색 범위 (거래일) — HTC(5일)와 분리, KH 전용
KH_VOLUME_UP_BEARISH_RATIO      = 0.7    # 거래량 증가 음봉 판정 기준 (기준봉 대비 비율)
KH_SQUEEZE_CANDLE_BODY_MAX_RATIO = 0.5   # 거자름 캔들 몸통 비율 상한 (고저 범위 대비) — "짧은" 음봉/양봉 조건
KH_CRAWL_MIN_TV_EOK             = 300    # KH 전용 크롤링 최소 거래대금 (억원, B안)
OBS_CRAWL_MIN_TV_EOK            = 100    # 기준봉 관찰 풀(recent_base_pool) 최소 거래대금 (억원)

# ── 오버수급 (상장주식수 대비 5일 누적 순매수 비율) ──────────────
# 돌팬티 "기관 오버수급 → 종가고가 패턴" 근사. 유통주식수가 아닌 상장주식수 기준이라 보수적.
OVERSUPPLY_RATIO_PCT                     = 1.0    # 5일 누적 순매수가 상장주식수의 1%↑이면 오버수급 강조

# ── 재료 신선도 (과거 signals 등장 횟수 기반 근사) ───────────────
# 종목이 최근 N거래일 signals에 여러 번 잡혔으면 "이미 진행된 재료"로 간주. 0회=신규 등장(신선).
FRESHNESS_LOOKBACK_DAYS                  = 10     # 신선도 판정 탐색 거래일 수
FRESHNESS_STALE_MIN_COUNT                = 3      # 이 횟수 이상 등장 시 "식상 가능" 표시

# ── 대형주 주도주 관찰 레이어 (2026-06-30 백테스트로 필터 재정렬) ──
# 신규 검증(시총상위50, 4~6월 2950표본): 신고가근접+거래대금+양봉 → D+1 시가67%/종가58%
#   (봇 중소형 53%/39% 대비 우위). 외인·기관 동시매수는 단독효과 미미 → 보조 태그로 강등.
# 게이트는 '코스피 강세 국면'에서 '신고가근접+거래대금' 질적 필터로 교체(혼조장 삼성전기 포착).
# 단 약세장 분리검증 미완 → 관찰정보(매수신호 아님), 대시보드 주석으로 과최적화 경계.
ENABLE_LARGECAP_OBSERVER                = True
LARGECAP_TOP_N                          = 50     # 코스피 시총 상위 N
LARGECAP_NEAR_HIGH_PCT                  = 5.0    # 252일 신고가 대비 -N% 이내 (신고가 근접)
LARGECAP_MIN_TV_EOK                     = 3000   # 최소 거래대금 (억) — "시장 자금 중심"

# ── 투탑(삼성전자·SK하이닉스) 과매도 반등 관찰 (2026-07-05, backtest_twotop_oversold) ──
# 기존 대형주 관찰(신고가+양봉)이 못 잡는 '급락일 과매도 반등' 자리 보완.
# 고수(돌팬티·준돌)가 7/2~7/3 실제 수익낸 자리 = 삼전·SKH 급락 후 다음날 반등.
# 백테스트: 당일 -8%↓ 다음날 67~71% / 2일누적 -12%↓ 86%. 손절 필수(최악 다음날저가 -12.5%).
# 관찰정보(매수신호 아님). KRX 일봉 기준 — NXT 야간·동시호가 정보 미반영.
ENABLE_TWOTOP_OVERSOLD                  = True
TWOTOP_CODES                            = {"005930": "삼성전자", "000660": "SK하이닉스"}
TWOTOP_OVERSOLD_1D_PCT                  = -8.0   # 당일 등락률 이 값 이하 = 과매도 관찰
TWOTOP_OVERSOLD_2D_PCT                  = -12.0  # 2일 누적 등락률 이 값 이하 = 강한 과매도

# ── 매매 분석 (trade_analyzer) ──────────────────────────────
TRADE_ANALYZER_BASE_CAPITAL             = 0      # 포지션 비중 산정 기준 자본 (0=해당 기간 총 매수대금)

# ── 일반 눌림 관찰 (Pullback Observer) — 기존 종가베팅 체계와 완전 분리 ───
# 평일 파이프라인에선 OFF (수집 비용 큼·소급 가능). 금요일 weekly_research에서
# 그 주 5일치를 소급 생성. weekly_research는 pullback_observer.run을 직접 호출하므로
# 이 플래그와 무관하게 동작.
ENABLE_PULLBACK_OBS                 = False
PULLBACK_OBS_DIR                    = DATA_DIR / "pullback_observation"
PULLBACK_OBS_SIGNALS_LOOKBACK_DAYS  = 20     # signals.csv 탐색 범위 (거래일)
PULLBACK_OBS_BASE_CANDLE_MIN_PCT    = 10.0   # 기준봉 최소 상승률 (%)
PULLBACK_OBS_BASE_TV_MIN_EOK        = 1000   # 기준봉 최소 거래대금 (억)
PULLBACK_OBS_BASE_TV_MULT           = 3.0    # 기준봉 20일 평균 대비 최소 배수 (OR 조건)
PULLBACK_OBS_DRAWDOWN_NORMAL_MAX    = -4.0   # 일반 눌림 상한: 이보다 작게 눌려야 함
PULLBACK_OBS_DRAWDOWN_NORMAL_MIN    = -12.0  # 일반 눌림 하한
PULLBACK_OBS_DRAWDOWN_DEEP_MIN      = -18.0  # 깊은 눌림 하한 (미만 제외)
PULLBACK_OBS_TODAY_TV_MIN_EOK       = 300    # 오늘 최소 거래대금 (억)
PULLBACK_OBS_NEAR_MA_THRESHOLD_PCT  = 3.0    # MA 근접 판단 기준 (%)
PULLBACK_OBS_TV_DRY_RATIO           = 0.5    # 거래대금 건조 기준 (기준봉 대비)
