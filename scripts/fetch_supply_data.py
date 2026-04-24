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

FRGN_URL = "https://finance.naver.com/item/frgn.naver"


def _parse_shares(text: str) -> float | None:
    """콤마/부호 처리 후 주(株) 단위 숫자 반환"""
    cleaned = text.strip().replace(",", "").replace("+", "").replace("−", "-").replace(" ", "")
    if cleaned in ("", "-", "N/A", "n/a"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_supply(code: str) -> SupplyData:
    """
    네이버 외국인/기관 매매 페이지에서 최신 1거래일 수급 데이터 반환.
    - table[1]: 일별 외국인/기관 매매동향
    - col[0]: 날짜, col[5]: 기관 순매수(주), col[6]: 외국인 순매수(주), col[7]: 외국인 보유주수, col[8]: 외국인 보유율
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

        for tr in tables[1].select("tr"):
            cols = tr.select("td")
            if len(cols) < 7:
                continue
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", cols[0].text.strip()):
                continue
            result.institution_net = _parse_shares(cols[5].text)
            result.foreign_net     = _parse_shares(cols[6].text)
            result.supply_date     = cols[0].text.strip()
            result.status = "ok"
            logger.info(f"[{code}] 수급 날짜: {result.supply_date} 기관={result.institution_net} 외국인={result.foreign_net}")
            break

    except Exception as e:
        logger.warning(f"[{code}] 수급 수집 실패: {e}")

    return result
