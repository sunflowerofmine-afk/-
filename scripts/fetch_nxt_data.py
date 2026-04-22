# scripts/fetch_nxt_data.py
"""NXT(넥스트레이드) 거래상위 데이터 수집 — 2차/수동 실행 시 KRX 데이터에 합산"""

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

NXT_QUANT_URL = "https://finance.naver.com/sise/nxt_sise_quant.naver"
_MARKETS = {"KOSPI": 0, "KOSDAQ": 1}


def _parse_number(text: str) -> str:
    return text.strip().replace(",", "").replace("+", "").replace("%", "").replace("−", "-")


def _fetch_page(market_code: int) -> pd.DataFrame:
    url = f"{NXT_QUANT_URL}?sosok={market_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.encoding = "euc-kr"
    except Exception as e:
        logger.warning(f"[NXT sosok={market_code}] 요청 실패: {e}")
        return pd.DataFrame()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.select_one("table.type_2")
    if table is None:
        logger.warning(f"[NXT sosok={market_code}] 테이블(type_2) 없음")
        return pd.DataFrame()

    rows = []
    for tr in table.select("tr"):
        cols = tr.select("td")
        if len(cols) < 6:
            continue
        name_tag = cols[1].select_one("a")
        if name_tag is None:
            continue

        href = name_tag.get("href", "")
        code_match = re.search(r"code=(\w+)", href)
        code = code_match.group(1) if code_match else ""
        if not code:
            continue

        try:
            price  = float(_parse_number(cols[2].text) or "0")
            volume = float(_parse_number(cols[5].text) or "0")
            # 거래대금: NXT 거래상위 페이지는 거래대금 직접 제공 (백만원 단위)
            tv_raw = float(_parse_number(cols[6].text) or "0") if len(cols) > 6 else 0.0
            tv_won = tv_raw * 1_000_000  # 백만원 → 원
        except (ValueError, IndexError):
            price, volume, tv_won = 0.0, 0.0, 0.0

        rows.append({
            "종목코드": code,
            "nxt_price":  price,
            "nxt_volume": volume,
            "nxt_tv":     tv_won,
        })

    logger.debug(f"[NXT sosok={market_code}] {len(rows)}행 파싱")
    return pd.DataFrame(rows)


def fetch_nxt_quant() -> dict:
    """
    KOSPI + KOSDAQ NXT 거래상위(최대 100개) 수집.
    반환: {종목코드: {"nxt_price": float, "nxt_volume": float, "nxt_tv": float(원)}}
    NXT에 없는 종목은 포함되지 않음 → 합산 시 해당 종목은 KRX 값만 사용.
    """
    result: dict = {}
    for market_name, market_code in _MARKETS.items():
        df = _fetch_page(market_code)
        time.sleep(REQUEST_DELAY)
        if df.empty:
            logger.warning(f"[NXT {market_name}] 수집 데이터 없음")
            continue
        for _, row in df.iterrows():
            result[str(row["종목코드"])] = {
                "nxt_price":  float(row["nxt_price"]),
                "nxt_volume": float(row["nxt_volume"]),
                "nxt_tv":     float(row["nxt_tv"]),
            }
        logger.info(f"[NXT {market_name}] {len(df)}개 종목 수집 완료")
    return result


def merge_nxt_into_df(all_df: pd.DataFrame, nxt_dict: dict) -> pd.DataFrame:
    """
    KRX 전종목 DataFrame에 NXT 데이터 합산.
    - 거래대금: KRX + NXT 합산 (원 단위)
    - 거래량:   KRX + NXT 합산
    - 현재가:   NXT 가격이 있으면 NXT 우선 (더 최신), 없으면 KRX 유지
    - 등락률:   KRX 기준 유지 (종가베팅 기준은 KRX 15:30 종가)
    """
    if not nxt_dict:
        return all_df

    nxt_df = pd.DataFrame.from_dict(nxt_dict, orient="index").reset_index()
    nxt_df.rename(columns={"index": "종목코드"}, inplace=True)

    merged = all_df.merge(nxt_df, on="종목코드", how="left")
    merged["nxt_tv"]     = merged["nxt_tv"].fillna(0)
    merged["nxt_volume"] = merged["nxt_volume"].fillna(0)

    merged["거래대금"] = merged["거래대금"] + merged["nxt_tv"]
    merged["거래량"]   = merged["거래량"]   + merged["nxt_volume"]

    # 현재가: NXT 가격이 유효한 경우만 덮어쓰기
    mask = merged["nxt_price"].notna() & (merged["nxt_price"] > 0)
    merged.loc[mask, "현재가"] = merged.loc[mask, "nxt_price"]

    merged.drop(columns=["nxt_tv", "nxt_volume", "nxt_price"], inplace=True)

    nxt_hit = int(mask.sum())
    logger.info(f"NXT 합산 완료: {nxt_hit}개 종목 거래대금/거래량/현재가 업데이트")
    return merged
