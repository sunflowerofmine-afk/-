# scripts/fetch_market_data.py
"""네이버 증권 코스피/코스닥 전 종목 데이터 수집"""

import sys
import logging
import re
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, MARKETS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

# 2026-09-11까지 finance.naver.com/sise/sise_market_sum.naver HTML을 긁었다.
# 네이버가 그 페이지를 stock.naver.com으로 리다이렉트하면서 표가 사라져 JSON API로 전환.
# 행 구성(ETF·ETN 포함)과 컬럼 의미는 구 페이지와 동일하게 맞춘다 — 총 거래대금·집중도 연속성.
from scripts.naver_api import fetch_market_list, to_float


def _rows_to_df(stocks: list[dict]) -> pd.DataFrame:
    rows = []
    for s in stocks:
        code = str(s.get("itemCode") or "").strip()
        name = str(s.get("stockName") or "").strip()
        if not code or not name:
            continue
        price  = to_float(s.get("closePriceRaw"), 0.0)
        volume = to_float(s.get("accumulatedTradingVolumeRaw"), 0.0)
        # 거래대금: API가 실제 누적 거래대금(원)을 준다. 없으면 구 방식(현재가×거래량)으로 대체.
        tv_won = to_float(s.get("accumulatedTradingValueRaw"))
        if tv_won is None:
            tv_won = price * volume
        # 상장주식수: 시가총액(원) ÷ 현재가. 네이버 시총 정의가 상장주식수×현재가라 정확히 복원된다.
        mcap = to_float(s.get("marketValueRaw"), 0.0)
        shares = round(mcap / price) if price and mcap else 0.0

        rows.append({
            "종목명":   name,
            "종목코드": code,
            "현재가":   price,
            "전일비":   to_float(s.get("compareToPreviousClosePriceRaw"), 0.0),
            "등락률":   to_float(s.get("fluctuationsRatio"), 0.0),
            "거래량":   volume,
            "거래대금": float(tv_won),   # 원 단위
            "상장주식수": float(shares),  # 주 단위
        })
    return pd.DataFrame(rows)


def fetch_all_stocks(market_name: str, market_code: int) -> pd.DataFrame:
    """시장 전체 종목 수집 → DataFrame 반환 (raw 원본, 제외 필터 미적용)"""
    logger.info(f"[{market_name}] 전 종목 수집 시작 (JSON API)")
    try:
        stocks = fetch_market_list(market_name)
    except Exception as e:
        logger.error(f"[{market_name}] 수집 실패: {e}")
        return pd.DataFrame()

    result = _rows_to_df(stocks)
    if result.empty:
        logger.error(f"[{market_name}] 수집 데이터 없음")
        return pd.DataFrame()

    for col in ["현재가", "전일비", "등락률", "거래량"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result["거래대금"] = pd.to_numeric(result["거래대금"], errors="coerce").fillna(0)

    result.dropna(subset=["종목명", "현재가"], inplace=True)
    result["시장"] = market_name
    result.reset_index(drop=True, inplace=True)

    logger.info(f"[{market_name}] 총 {len(result)}개 종목 수집 완료")
    return result


_FCHART_INDEX_URL = "https://fchart.stock.naver.com/sise.nhn"


def fetch_index_levels() -> dict:
    """코스피/코스닥 현재 지수 레벨 + 등락률 수집 (fchart 일봉 기준).
    실패 시 None 반환 (파이프라인 중단 금지).
    포맷: <item data="YYYYMMDD|open|high|low|close|volume" />
    """
    from datetime import datetime, timezone, timedelta
    today8 = datetime.now(tz=timezone(timedelta(hours=9))).strftime("%Y%m%d")
    result = {"kospi_level": None, "kosdaq_level": None, "kospi_chg": None, "kosdaq_chg": None}
    for symbol, level_key, chg_key in [
        ("KOSPI",  "kospi_level",  "kospi_chg"),
        ("KOSDAQ", "kosdaq_level", "kosdaq_chg"),
    ]:
        try:
            items = re.findall(
                r'<item data="(\d{8})\|[^|]+\|[^|]+\|[^|]+\|([0-9.]+)\|',
                requests.get(
                    f"{_FCHART_INDEX_URL}?symbol={symbol}&timeframe=day&count=5&requestType=0",
                    headers=HEADERS, timeout=REQUEST_TIMEOUT,
                ).text,
            )
            for i, (d, c) in enumerate(items):
                if d == today8:
                    result[level_key] = float(c)
                    if i > 0:
                        prev = float(items[i - 1][1])
                        if prev > 0:
                            result[chg_key] = round((float(c) - prev) / prev * 100, 2)
                    break
            logger.debug(f"지수 [{symbol}]: level={result[level_key]} chg={result[chg_key]}")
        except Exception as e:
            logger.debug(f"지수 수집 실패 [{symbol}]: {e}")
    return result


def run() -> dict[str, pd.DataFrame]:
    """KOSPI + KOSDAQ 전 종목 수집"""
    result = {}
    for market_name, market_code in MARKETS.items():
        df = fetch_all_stocks(market_name, market_code)
        if not df.empty:
            result[market_name] = df
    return result
