# scripts/fetch_market_data.py
"""네이버 증권 코스피/코스닥 전 종목 데이터 수집"""

import sys
import time
import logging
import re
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, MARKETS, REQUEST_TIMEOUT, REQUEST_DELAY

logger = logging.getLogger(__name__)

BASE_URL = "https://finance.naver.com/sise/sise_market_sum.naver"


def _get_last_page(market_code: int) -> int:
    url = f"{BASE_URL}?sosok={market_code}&page=1"
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.encoding = "euc-kr"
    soup = BeautifulSoup(resp.text, "lxml")
    pager = soup.select_one("td.pgRR > a")
    if pager is None:
        return 1
    href = pager.get("href", "")
    match = re.search(r"page=(\d+)", href)
    return int(match.group(1)) if match else 1


def _parse_number(text: str) -> str:
    return text.strip().replace(",", "").replace("+", "").replace("%", "").replace("−", "-")


def _fetch_page(market_code: int, page: int) -> pd.DataFrame:
    url = f"{BASE_URL}?sosok={market_code}&page={page}"
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.encoding = "euc-kr"
    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.select_one("table.type_2")
    if table is None:
        return pd.DataFrame()

    rows = []
    for tr in table.select("tr"):
        cols = tr.select("td")
        # 현재 페이지 구조(13컬럼): N/종목명/현재가/전일비/등락률/액면가/시가총액/상장주식수/외국인비율/거래량/PER/ROE/게시판
        if len(cols) < 10:
            continue
        name_tag = cols[1].select_one("a")
        if name_tag is None:
            continue

        href = name_tag.get("href", "")
        code_match = re.search(r"code=(\w+)", href)
        code = code_match.group(1) if code_match else ""

        # 거래량(col[9]) × 현재가(col[2])로 거래대금 계산 (sise_market_sum에 거래대금 컬럼 없음)
        try:
            price  = float(_parse_number(cols[2].text) or "0")
            volume = float(_parse_number(cols[9].text) or "0")
            tv_won = price * volume  # 원 단위
        except ValueError:
            tv_won = 0.0

        rows.append({
            "종목명":   name_tag.text.strip(),
            "종목코드": code,
            "현재가":   _parse_number(cols[2].text),
            "전일비":   _parse_number(cols[3].text),
            "등락률":   _parse_number(cols[4].text),
            "거래량":   _parse_number(cols[9].text),
            "거래대금": tv_won,   # 원 단위
        })

    return pd.DataFrame(rows)


def fetch_all_stocks(market_name: str, market_code: int) -> pd.DataFrame:
    """시장 전체 종목 수집 → DataFrame 반환 (raw 원본, 제외 필터 미적용)"""
    logger.info(f"[{market_name}] 마지막 페이지 확인 중...")
    try:
        last_page = _get_last_page(market_code)
    except Exception as e:
        logger.error(f"[{market_name}] 페이지 수 확인 실패: {e}")
        return pd.DataFrame()

    logger.info(f"[{market_name}] 총 {last_page}페이지 수집 시작")
    frames = []
    for page in range(1, last_page + 1):
        try:
            df = _fetch_page(market_code, page)
            if not df.empty:
                frames.append(df)
            if page % 10 == 0:
                logger.info(f"[{market_name}] {page}/{last_page} 완료")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"[{market_name}] {page}페이지 실패: {e}")
            time.sleep(1)

    if not frames:
        logger.error(f"[{market_name}] 수집 데이터 없음")
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)

    for col in ["현재가", "전일비", "등락률", "거래량"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result["거래대금"] = pd.to_numeric(result["거래대금"], errors="coerce").fillna(0)

    result.dropna(subset=["종목명", "현재가"], inplace=True)
    result["시장"] = market_name
    result.reset_index(drop=True, inplace=True)

    logger.info(f"[{market_name}] 총 {len(result)}개 종목 수집 완료")
    return result


_FCHART_INDEX_URL = "https://fchart.stock.naver.com/sise.nhn"


def _fetch_index_chg(symbol: str, today8: str) -> float | None:
    """KOSPI/KOSDAQ 당일 등락률 (fchart 일봉 기준). 장중이면 현재가 기준."""
    try:
        items = re.findall(
            r'<item data="(\d{8})\|([0-9.]+)\|[^|]+\|[^|]+\|([0-9.]+)\|',
            requests.get(
                f"{_FCHART_INDEX_URL}?symbol={symbol}&timeframe=day&count=5&requestType=0",
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
            ).text,
        )
        for i, (d, _, c) in enumerate(items):
            if d == today8 and i > 0:
                prev = float(items[i - 1][2])
                if prev > 0:
                    return round((float(c) - prev) / prev * 100, 2)
    except Exception:
        pass
    return None


def fetch_index_levels() -> dict:
    """코스피/코스닥 현재 지수 레벨 + 등락률 수집. 실패 시 None 반환 (파이프라인 중단 금지)."""
    from datetime import datetime, timezone, timedelta
    today8 = datetime.now(tz=timezone(timedelta(hours=9))).strftime("%Y%m%d")
    result = {"kospi_level": None, "kosdaq_level": None, "kospi_chg": None, "kosdaq_chg": None}
    for code, key in [("KOSPI", "kospi_level"), ("KOSDAQ", "kosdaq_level")]:
        try:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "lxml")
            for selector in ["#nowVal", ".num_now em", "strong.num", "span.num"]:
                el = soup.select_one(selector)
                if el:
                    val = _parse_number(el.text)
                    if val:
                        result[key] = float(val)
                        break
            logger.debug(f"지수 레벨 [{code}]: {result[key]}")
        except Exception as e:
            logger.debug(f"지수 레벨 수집 실패 [{code}]: {e}")
    for symbol, key in [("KOSPI", "kospi_chg"), ("KOSDAQ", "kosdaq_chg")]:
        result[key] = _fetch_index_chg(symbol, today8)
    return result


def run() -> dict[str, pd.DataFrame]:
    """KOSPI + KOSDAQ 전 종목 수집"""
    result = {}
    for market_name, market_code in MARKETS.items():
        df = fetch_all_stocks(market_name, market_code)
        if not df.empty:
            result[market_name] = df
    return result
