# scripts/fetch_stock_data.py
"""개별 종목 OHLCV 일별 히스토리 수집 (상위 후보 종목용)

2026-09-18 네이버가 `item/sise_day.naver`(HTML 일별시세)를 410으로 닫았다. fchart(XML, 한 번에
수백 일)를 주 소스로, 모바일 JSON `api/stock/{code}/price`(pageSize 60 상한)를 예비로 쓴다.
반환 구조는 예전과 같다: date(YYYY.MM.DD), close, change, open, high, low, volume — 최신이 index 0.

⚠ 두 소스 모두 **일봉 종가가 KRX 애프터마켓(16:00부터 20:00) 마지막 가격**이다(2026-09-14 개장 뒤,
sise_day도 같았다). 공식 정규장 종가(다음 날 기준가)와 다를 수 있다. 시가는 정규장 시가다.
"""

import sys
import logging
import re
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"
PRICE_API_URL = "https://m.stock.naver.com/api/stock/{code}/price"
_PRICE_API_PAGE = 60   # 61 이상은 400


def fetch_chart_data(code: str) -> pd.DataFrame:
    """
    최소 250일 일봉 데이터 확보용 함수.
    26페이지 × 10행 ≒ 260행 → MA60 + 52주 신고가 + 60일 최고값 계산 충분.
    """
    return fetch_daily_history(code, pages=26)


def _from_fchart(code: str, n: int) -> list[dict]:
    r = requests.get(FCHART_URL, params={"symbol": code, "timeframe": "day", "count": n, "requestType": 0},
                     headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    rows = []
    for item in re.findall(r'<item data="([^"]+)"', r.text):
        p = item.split("|")
        if len(p) < 6 or not re.match(r"\d{8}$", p[0]):
            continue
        rows.append({"date": f"{p[0][:4]}.{p[0][4:6]}.{p[0][6:]}", "open": p[1], "high": p[2],
                     "low": p[3], "close": p[4], "volume": p[5]})
    return rows


def _from_price_api(code: str, n: int) -> list[dict]:
    from scripts.naver_api import get_json
    rows = []
    page = 1
    while len(rows) < n:
        data = get_json(PRICE_API_URL.format(code=code), {"pageSize": _PRICE_API_PAGE, "page": page})
        if not isinstance(data, list) or not data:
            break
        for x in data:
            rows.append({"date": str(x.get("localTradedAt", "")).replace("-", "."),
                         "open": x.get("openPrice"), "high": x.get("highPrice"), "low": x.get("lowPrice"),
                         "close": x.get("closePrice"), "volume": x.get("accumulatedTradingVolume")})
        if len(data) < _PRICE_API_PAGE:
            break
        page += 1
    return rows[:n]


def fetch_daily_history(code: str, pages: int = 7) -> pd.DataFrame:
    """
    최근 pages*10행 일봉. 반환 컬럼: date(str), close, change, open, high, low, volume — 최신이 index 0.
    실패 시 빈 DataFrame 반환.
    """
    n = pages * 10
    rows: list[dict] = []
    try:
        rows = _from_fchart(code, n + 2)
    except Exception as e:
        logger.warning(f"[{code}] fchart 히스토리 실패, JSON으로 재시도: {e}")
    if not rows:
        try:
            rows = _from_price_api(code, n)
        except Exception as e:
            logger.warning(f"[{code}] 히스토리 수집 실패: {e}")
            return pd.DataFrame()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for col in ["close", "open", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")

    df.dropna(subset=["close"], inplace=True)
    df.sort_values("date", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)
    df = df.head(n).copy()

    # 전일비 = 종가 - 전일 종가 (내림차순이라 다음 행이 전일)
    df["change"] = df["close"] - df["close"].shift(-1)

    # 거래대금 컬럼이 없으므로 close×volume으로 근사 (예전과 동일)
    df["trading_value"] = df["close"] * df["volume"]

    # 내림차순 정렬이므로 pct_change(-1) = (today - yesterday) / yesterday
    df["change_pct"] = df["close"].pct_change(-1) * 100

    return df[["date", "close", "change", "open", "high", "low", "volume", "trading_value", "change_pct"]]
