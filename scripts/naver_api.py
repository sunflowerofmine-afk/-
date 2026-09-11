# scripts/naver_api.py
"""네이버 증권 JSON API 공용 헬퍼.

2026-09-11 네이버가 finance.naver.com의 HTML 시세 페이지(sise_market_sum, nxt_sise_quant,
item/frgn, sise_group, marketindex)를 stock.naver.com으로 302 리다이렉트하기 시작해
HTML 파서가 전부 빈 결과를 냈다. 수집기는 아래 JSON API로 옮긴다.

  전 종목 시세      m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ}?page=&pageSize=100
                    (ETF·ETN 포함 — 구 페이지와 같은 행 구성. 총 거래대금·집중도 연속성 유지)
  NXT 거래상위      stock.naver.com/api/domestic/market/stock/default?tradeType=NXT&orderType=quantTop
  외국인·기관 수급   m.stock.naver.com/api/stock/{code}/trend?pageSize=5
  업종·테마         stock.naver.com/api/domestic/market/{upjong|theme}/list, /{no}/stocklist
  환율·유가         m.stock.naver.com/front-api/marketIndex/productDetail?category=&reutersCode=

startIdx는 페이지 번호(0부터)이고 pageSize 상한은 list 계열 200, marketValue 100이다.
"""

import logging
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

JSON_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/",
}

MARKET_LIST_URL   = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
STOCK_DEFAULT_URL = "https://stock.naver.com/api/domestic/market/stock/default"
STOCK_TREND_URL   = "https://m.stock.naver.com/api/stock/{code}/trend"
GROUP_LIST_URL    = "https://stock.naver.com/api/domestic/market/{group}/list"
GROUP_STOCKS_URL  = "https://stock.naver.com/api/domestic/market/{group}/{no}/stocklist"
MARKET_INDEX_URL  = "https://m.stock.naver.com/front-api/marketIndex/productDetail"

MARKET_LIST_PAGE_SIZE = 100   # marketValue API 상한 (101 이상은 400)
GROUP_PAGE_SIZE       = 200   # list/stocklist 상한 (201 이상은 400)


def get_json(url: str, params: dict | None = None, timeout: int = REQUEST_TIMEOUT):
    """JSON GET. HTTP 오류·비JSON 응답은 예외로 올린다 (호출부가 수집 실패로 처리)."""
    resp = requests.get(url, params=params, headers=JSON_HEADERS, timeout=timeout)
    resp.raise_for_status()
    body = resp.text.lstrip()
    if not body.startswith(("{", "[")):
        raise ValueError(f"JSON 아님 ({resp.status_code}, {len(body)}B): {url}")
    return resp.json()


def to_float(v, default: float | None = None) -> float | None:
    """'1,234', '+5,769', '-3.53', '46.71%' → float. 빈값·'N/A'는 default."""
    if v is None:
        return default
    s = str(v).strip().replace(",", "").replace("%", "").replace("+", "").replace("−", "-")
    if s in ("", "-", "N/A", "n/a", "null", "None"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def fetch_market_list(market: str) -> list[dict]:
    """시가총액순 전 종목 (ETF·ETN 포함). market = 'KOSPI' | 'KOSDAQ'. 페이지 끝까지 순회."""
    rows: list[dict] = []
    page = 1
    while True:
        data = get_json(MARKET_LIST_URL.format(market=market),
                        {"page": page, "pageSize": MARKET_LIST_PAGE_SIZE})
        stocks = data.get("stocks") or []
        rows.extend(stocks)
        if len(stocks) < MARKET_LIST_PAGE_SIZE:
            break
        page += 1
        if page > 100:  # 안전장치: 1만 종목 초과는 비정상
            logger.warning(f"[{market}] 페이지 100 초과 — 중단")
            break
    return rows


def fetch_stock_default(trade_type: str = "KRX", market_type: str = "ALL",
                        order_type: str = "marketSum", page_size: int = 5000) -> list[dict]:
    """종목 리스트 (KRX/NXT). page_size는 상한이 없어 한 번에 받는다."""
    data = get_json(STOCK_DEFAULT_URL, {
        "tradeType": trade_type, "marketType": market_type,
        "orderType": order_type, "startIdx": 0, "pageSize": page_size,
    })
    return data if isinstance(data, list) else []


def fetch_group_list(group: str) -> list[dict]:
    """업종('upjong') / 테마('theme') 목록. 등락률순, 페이지 끝까지."""
    rows: list[dict] = []
    for page in range(0, 20):
        data = get_json(GROUP_LIST_URL.format(group=group),
                        {"startIdx": page, "pageSize": GROUP_PAGE_SIZE, "sortType": "changeRate"})
        if not isinstance(data, list) or not data:
            break
        rows.extend(data)
        if len(data) < GROUP_PAGE_SIZE:
            break
    return rows


def fetch_group_stocks(group: str, no: int | str) -> list[dict]:
    """업종/테마 구성 종목 전체 (200개 단위 페이지 순회)."""
    rows: list[dict] = []
    for page in range(0, 20):
        data = get_json(GROUP_STOCKS_URL.format(group=group, no=no), {
            "marketType": "ALL", "orderType": "quantTop",
            "startIdx": page, "pageSize": GROUP_PAGE_SIZE,
        })
        if not isinstance(data, list) or not data:
            break
        rows.extend(data)
        if len(data) < GROUP_PAGE_SIZE:
            break
    return rows


def fetch_market_index(category: str, reuters_code: str) -> dict:
    """환율·원자재 현재값. 예: ('exchange','FX_USDKRW'), ('energy','CLcv1'=WTI)."""
    data = get_json(MARKET_INDEX_URL, {"category": category, "reutersCode": reuters_code})
    if not data.get("isSuccess"):
        raise ValueError(f"marketIndex 실패 {category}/{reuters_code}: {data.get('message')}")
    return data.get("result") or {}
