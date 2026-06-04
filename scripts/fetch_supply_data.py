# scripts/fetch_supply_data.py
"""기관/외국인 수급 데이터 수집"""

import sys
import logging
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT
from scripts.models import SupplyData

logger = logging.getLogger(__name__)

FRGN_URL     = "https://finance.naver.com/item/frgn.naver"
INVESTOR_URL = "https://finance.naver.com/item/sise_investor.naver"


def _parse_shares(text: str) -> float | None:
    """콤마/부호 처리 후 주(株) 단위 숫자 반환"""
    cleaned = text.strip().replace(",", "").replace("+", "").replace("−", "-").replace(" ", "")
    if cleaned in ("", "-", "N/A", "n/a"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


_SUPPLY_LOOKBACK = 5  # 누적 집계 거래일 수


def _count_consecutive(values: list[float]) -> int:
    """
    최신일(index 0)부터 같은 부호가 연속되는 일수 반환.
    양수(순매수)면 양수, 음수(순매도)면 음수로 반환.
    예: [+100, +200, +50, -10, +30] → 3
        [-100, -200, +50]           → -2
        []                          → 0
    """
    if not values:
        return 0
    sign = 1 if values[0] > 0 else (-1 if values[0] < 0 else 0)
    if sign == 0:
        return 0
    count = 0
    for v in values:
        if (sign > 0 and v > 0) or (sign < 0 and v < 0):
            count += 1
        else:
            break
    return sign * count


def fetch_supply(code: str) -> SupplyData:
    """
    네이버 외국인/기관 매매 페이지에서 최근 5거래일 수급 데이터 반환.
    - institution_net / foreign_net : 최신 1거래일 (주 단위 — pipeline에서 종가 곱해 원 변환)
    - institution_net_5d / foreign_net_5d : 5거래일 누적 (동일 단위)
    실패해도 예외 발생 금지 — status="failed" SupplyData 반환.
    """
    result = SupplyData(code=code)

    try:
        resp = requests.get(f"{FRGN_URL}?code={code}", headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "lxml")

        tables = soup.select("table.type2")
        if len(tables) < 2:
            logger.debug(f"[{code}] 수급 테이블 없음 (tables={len(tables)})")
            return result

        inst_acc  = 0.0
        frgn_acc  = 0.0
        rows_ok   = 0
        inst_rows: list[float] = []
        frgn_rows: list[float] = []

        for tr in tables[1].select("tr"):
            cols = tr.select("td")
            if len(cols) < 7:
                continue
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", cols[0].text.strip()):
                continue

            inst = _parse_shares(cols[5].text)
            frgn = _parse_shares(cols[6].text)

            if rows_ok == 0:
                result.institution_net = inst
                result.foreign_net     = frgn
                result.supply_date     = cols[0].text.strip()
                result.status          = "ok"

            inst_acc += (inst or 0.0)
            frgn_acc += (frgn or 0.0)
            inst_rows.append(inst or 0.0)
            frgn_rows.append(frgn or 0.0)
            rows_ok  += 1

            if rows_ok >= _SUPPLY_LOOKBACK:
                break

        if result.status == "ok":
            result.institution_net_5d = inst_acc
            result.foreign_net_5d     = frgn_acc
            result.institution_consecutive_days = _count_consecutive(inst_rows)
            result.foreign_consecutive_days     = _count_consecutive(frgn_rows)
            logger.info(
                f"[{code}] 수급({rows_ok}d) 날짜={result.supply_date} "
                f"기관1d={result.institution_net} 5d={inst_acc:.0f} "
                f"외국인1d={result.foreign_net} 5d={frgn_acc:.0f}"
            )

    except Exception as e:
        logger.warning(f"[{code}] 수급 수집 실패: {e}")

    return result
