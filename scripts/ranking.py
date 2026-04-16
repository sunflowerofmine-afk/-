# scripts/ranking.py
"""상승률/거래대금 Top20 추출 및 교집합 계산"""

import sys
import logging
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    EXCLUDE_KEYWORDS,
    PREFERRED_STOCK_SUFFIXES,
    TOP_GAINERS_COUNT,
    TOP_TRADING_VALUE_COUNT,
    MIN_PRICE,
    MIN_CHANGE_PCT,
    MAX_CHANGE_PCT,
)

logger = logging.getLogger(__name__)


def filter_excluded_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """
    제외 필터 공통 함수.
    - ETF/스팩/리츠 등: 종목명 포함 기준 (EXCLUDE_KEYWORDS)
    - 우선주: 종목명 끝 기준 (PREFERRED_STOCK_SUFFIXES)
    raw 저장에는 적용하지 않고, processed/ranking 단계에서만 호출.
    """
    if df.empty or "종목명" not in df.columns:
        return df
    mask = pd.Series([True] * len(df), index=df.index)

    # 포함 기준 제외
    for kw in EXCLUDE_KEYWORDS:
        mask &= ~df["종목명"].str.contains(kw, na=False)

    # 끝 기준 우선주 제외
    for suffix in PREFERRED_STOCK_SUFFIXES:
        mask &= ~df["종목명"].str.endswith(suffix, na=False)

    filtered = df[mask].copy()
    removed = len(df) - len(filtered)
    if removed:
        logger.info(f"제외 필터: {removed}개 종목 제거")
    return filtered.reset_index(drop=True)


def apply_exclusion_filter(df: pd.DataFrame) -> pd.DataFrame:
    """하위 호환 alias → filter_excluded_stocks 호출"""
    return filter_excluded_stocks(df)


def apply_price_filter(df: pd.DataFrame) -> pd.DataFrame:
    """
    1차 필터: 동전주 / 비정상 급등 제거.
    raw 데이터에는 적용하지 않고 processed/ranking 단계에서만 호출.
    조건: 종가 >= MIN_PRICE, MIN_CHANGE_PCT <= 등락률 <= MAX_CHANGE_PCT
    """
    if df.empty:
        return df
    before = len(df)
    mask = (
        (df["현재가"] >= MIN_PRICE) &
        (df["등락률"] >= MIN_CHANGE_PCT) &
        (df["등락률"] <= MAX_CHANGE_PCT)
    )
    filtered = df[mask].copy().reset_index(drop=True)
    removed = before - len(filtered)
    if removed:
        logger.info(f"1차 가격 필터: {removed}개 제거 (동전주/비정상 제외)")
    return filtered


def get_top_gainers(df: pd.DataFrame, n: int = TOP_GAINERS_COUNT) -> pd.DataFrame:
    """등락률 상위 N개 (양수만)"""
    if df.empty or "등락률" not in df.columns:
        return pd.DataFrame()
    filtered = df[df["등락률"] > 0].copy()
    return filtered.nlargest(n, "등락률").reset_index(drop=True)


def get_top_trading_value(df: pd.DataFrame, n: int = TOP_TRADING_VALUE_COUNT) -> pd.DataFrame:
    """거래대금 상위 N개"""
    if df.empty or "거래대금" not in df.columns:
        return pd.DataFrame()
    return df.nlargest(n, "거래대금").reset_index(drop=True)


def get_intersection(gainers_df: pd.DataFrame, tv_df: pd.DataFrame) -> pd.DataFrame:
    """상승률 Top20 ∩ 거래대금 Top20 교집합"""
    if gainers_df.empty or tv_df.empty or "종목코드" not in gainers_df.columns:
        return pd.DataFrame()
    codes_g = set(gainers_df["종목코드"].dropna())
    codes_t = set(tv_df["종목코드"].dropna())
    common  = codes_g & codes_t
    if not common:
        return pd.DataFrame()
    result = gainers_df[gainers_df["종목코드"].isin(common)].copy()
    result["in_top_gainers"] = True
    result["in_top_tv"]      = True
    return result.reset_index(drop=True)


def calc_market_total(kospi_df: pd.DataFrame, kosdaq_df: pd.DataFrame) -> dict:
    """코스피/코스닥 총 거래대금 계산 (원 단위)"""
    def total_tv(df):
        if df.empty or "거래대금" not in df.columns:
            return 0.0
        return float(df["거래대금"].sum())

    kospi_tv  = total_tv(kospi_df)
    kosdaq_tv = total_tv(kosdaq_df)

    return {
        "kospi_total_tv":     kospi_tv,
        "kosdaq_total_tv":    kosdaq_tv,
        "kospi_total_tv_eok": round(kospi_tv  / 100_000_000, 0),
        "kosdaq_total_tv_eok": round(kosdaq_tv / 100_000_000, 0),
    }
