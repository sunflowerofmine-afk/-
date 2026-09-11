# scripts/fetch_nxt_data.py
"""NXT 거래상위 데이터 수집 — 2차/수동 실행 시 KRX 데이터에 합산"""

import sys
import logging
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

# 2026-09-11까지 finance.naver.com/sise/nxt_sise_quant.naver(거래량 상위, 시장별 100행)를 긁었다.
# 네이버가 그 페이지를 없애면서 stock.naver.com JSON API로 전환. 구 페이지와 같은 행 구성을
# 유지하기 위해 NXT 거래량 상위 100종목/시장으로 자른다 (is_nxt·NXT대장 의미 보존).
from scripts.naver_api import fetch_stock_default, to_float

_MARKETS = {"KOSPI": "0", "KOSDAQ": "1"}
_TOP_N_PER_MARKET = 100


def _fetch_all_nxt() -> pd.DataFrame:
    """NXT 거래 종목 전체 → DataFrame(종목코드, sosok, nxt_price, nxt_volume, nxt_tv)."""
    try:
        items = fetch_stock_default(trade_type="NXT", market_type="ALL", order_type="quantTop")
    except Exception as e:
        logger.warning(f"[NXT] 요청 실패: {e}")
        return pd.DataFrame()

    rows = []
    for it in items:
        code = str(it.get("itemcode") or "").strip()
        if not code:
            continue
        rows.append({
            "종목코드":   code,
            "sosok":      str(it.get("sosok") or ""),
            "nxt_price":  to_float(it.get("nowPrice"), 0.0),
            "nxt_volume": to_float(it.get("tradeVolume"), 0.0),
            "nxt_tv":     to_float(it.get("tradeAmount"), 0.0),   # 원 단위
        })
    logger.debug(f"[NXT] {len(rows)}행 파싱")
    return pd.DataFrame(rows)


def fetch_nxt_quant() -> dict:
    """
    KOSPI + KOSDAQ NXT 거래상위(최대 100개) 수집.
    반환: {종목코드: {"nxt_price": float, "nxt_volume": float, "nxt_tv": float(원)}}
    NXT에 없는 종목은 포함되지 않음 → 합산 시 해당 종목은 KRX 값만 사용.
    """
    result: dict = {}
    all_df = _fetch_all_nxt()
    for market_name, market_code in _MARKETS.items():
        df = all_df[all_df["sosok"] == market_code] if not all_df.empty else all_df
        if df.empty:
            logger.warning(f"[NXT {market_name}] 수집 데이터 없음")
            continue
        df = df.sort_values("nxt_volume", ascending=False).head(_TOP_N_PER_MARKET)
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
