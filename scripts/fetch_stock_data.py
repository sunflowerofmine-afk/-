# scripts/fetch_stock_data.py
"""개별 종목 OHLCV 일별 히스토리 수집 (상위 후보 종목용)"""

import sys
import time
import logging
import re
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY

logger = logging.getLogger(__name__)

SISE_DAY_URL = "https://finance.naver.com/item/sise_day.naver"


def fetch_chart_data(code: str) -> pd.DataFrame:
    """
    최소 250일 일봉 데이터 확보용 함수.
    26페이지 × 10행 ≒ 260행 → MA60 + 52주 신고가 + 60일 최고값 계산 충분.
    """
    return fetch_daily_history(code, pages=26)


def fetch_daily_history(code: str, pages: int = 7) -> pd.DataFrame:
    """
    네이버 일별 시세에서 최근 pages*10행 데이터 수집.
    반환 컬럼: date(str), close, change, open, high, low, volume
    최신 데이터가 index 0.
    실패 시 빈 DataFrame 반환.
    """
    frames = []
    for page in range(1, pages + 1):
        url = f"{SISE_DAY_URL}?code={code}&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.select_one("table.type2")
            if table is None:
                break

            for tr in table.select("tr"):
                cols = tr.select("td")
                if len(cols) < 7:
                    continue
                date_text = cols[0].text.strip()
                if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_text):
                    continue

                def _n(idx):
                    return cols[idx].text.strip().replace(",", "").replace("+", "").replace("−", "-")

                frames.append({
                    "date":   date_text,
                    "close":  _n(1),
                    "change": _n(2),
                    "open":   _n(3),
                    "high":   _n(4),
                    "low":    _n(5),
                    "volume": _n(6),
                })

            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"[{code}] {page}페이지 히스토리 수집 실패: {e}")
            break

    if not frames:
        return pd.DataFrame()

    df = pd.DataFrame(frames)
    for col in ["close", "change", "open", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["close"], inplace=True)
    df.sort_values("date", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)

    # 네이버 sise_day에는 거래대금 컬럼이 없으므로 close×volume으로 근사
    df["trading_value"] = df["close"] * df["volume"]

    return df
