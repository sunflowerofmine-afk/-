# scripts/fetch_sector_data.py
"""네이버 증권 업종 섹터 데이터 수집"""

import re
import sys
import time
import logging
from pathlib import Path
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY

logger = logging.getLogger(__name__)

_BASE = "https://finance.naver.com/sise"


def _get(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.encoding = "euc-kr"
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.warning(f"섹터 페이지 요청 실패 {url}: {e}")
        return None


def _parse_float(text: str) -> float:
    cleaned = (
        text.strip()
        .replace(",", "")
        .replace("+", "")
        .replace("%", "")
        .replace("▲", "")
        .replace("▼", "-")
        .replace("−", "-")
    )
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def fetch_sector_overview() -> pd.DataFrame:
    """
    네이버 업종 현황 → 섹터명, sector_no, 등락률 DataFrame 반환.
    컬럼 순서: 업종명 | 전일대비% | 상승수 | 보합수 | 하락수 | 미등록 | 비율
    """
    soup = _get(f"{_BASE}/sise_group.naver?type=upjong")
    if not soup:
        return pd.DataFrame()

    rows = []
    for tr in soup.select("table.type_1 tr"):
        tds = tr.select("td")
        a_tag = None
        for td in tds:
            a = td.select_one("a[href]")
            if a and re.search(r"no=\d+", a.get("href", "")):
                a_tag = a
                break
        if a_tag is None:
            continue

        m = re.search(r"no=(\d+)", a_tag.get("href", ""))
        if not m:
            continue

        try:
            change_pct = _parse_float(tds[1].text)
        except IndexError:
            continue

        rows.append({
            "sector_name": a_tag.text.strip(),
            "sector_no":   int(m.group(1)),
            "change_pct":  change_pct,
        })

    if not rows:
        logger.warning("업종 overview 파싱 결과 없음 (HTML 구조 변경 가능성)")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    logger.info(f"업종 overview 수집: {len(df)}개 섹터")
    return df


def fetch_sector_stock_codes(sector_no: int) -> list:
    """업종 상세 페이지에서 구성 종목 코드 목록 추출"""
    url = f"{_BASE}/sise_group_detail.naver?type=upjong&no={sector_no}"
    soup = _get(url)
    if not soup:
        return []
    codes = []
    for a in soup.find_all("a", href=re.compile(r"code=\d{6}")):
        m = re.search(r"code=(\d{6})", a["href"])
        if m:
            codes.append(m.group(1))
    return list(dict.fromkeys(codes))  # deduplicate, preserve order


def run(top_n: int = 5) -> dict:
    """
    거래대금 상위 top_n개 섹터 수집.
    Returns:
        overview        : pd.DataFrame  전체 업종 개요
        top_sectors     : list[dict]    상위 섹터 (sector_name, change_pct, tv_eok, stock_codes)
        code_to_sector  : dict[str,str] 종목코드 → 섹터명
    """
    result: dict = {
        "overview":       pd.DataFrame(),
        "top_sectors":    [],
        "code_to_sector": {},
    }

    overview = fetch_sector_overview()
    if overview.empty:
        return result
    result["overview"] = overview

    top_df = overview.nlargest(top_n, "change_pct")
    code_to_sector: dict[str, str] = {}

    for _, row in top_df.iterrows():
        no   = int(row["sector_no"])
        name = str(row["sector_name"])
        chg  = float(row["change_pct"])
        tv   = 0.0  # pipeline에서 filtered_df 기반으로 재계산

        codes: list = []
        try:
            codes = fetch_sector_stock_codes(no)
            for c in codes:
                code_to_sector.setdefault(c, name)
            logger.info(f"[{name}] 구성종목 {len(codes)}개")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"[{name}] 구성종목 수집 실패: {e}")

        result["top_sectors"].append({
            "sector_name": name,
            "change_pct":  chg,
            "tv_eok":      tv,
            "stock_codes": codes,
        })

    result["code_to_sector"] = code_to_sector
    logger.info(f"섹터 수집 완료: {len(result['top_sectors'])}섹터, {len(code_to_sector)}종목 매핑")
    return result
