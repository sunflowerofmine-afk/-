# scripts/indicators.py
"""기술적 지표 계산 모듈"""

import sys
import logging
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    BIG_CANDLE_MIN_PCT, LOOSE_BIG_CANDLE_MIN_PCT,
    BIG_CANDLE_UPPER_TAIL_MAX, LOOSE_BIG_CANDLE_UPPER_TAIL_MAX,
    MIN_TRADING_VALUE_EOK,
    MA_CLUSTER_5_10_20_MAX_GAP_PCT, MA_CLUSTER_5_10_20_60_MAX_GAP_PCT,
    FIRST_BIG_CANDLE_LOOKBACK_DAYS,
    VOLUME_PEAK_LOOKBACK_DAYS, TRADING_VALUE_PEAK_LOOKBACK_DAYS,
)

logger = logging.getLogger(__name__)

MIN_TRADING_VALUE_WON = MIN_TRADING_VALUE_EOK * 100_000_000  # 억→원


def calc_ma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=1).mean()


def calc_all_ma(df: pd.DataFrame) -> pd.DataFrame:
    """df에 ma5, ma10, ma20, ma60 컬럼 추가 (close 컬럼 필요)"""
    df = df.copy()
    close = df["close"]
    df["ma5"]  = calc_ma(close, 5)
    df["ma10"] = calc_ma(close, 10)
    df["ma20"] = calc_ma(close, 20)
    df["ma60"] = calc_ma(close, 60)
    return df


def is_ma_cluster(ma5: float, ma10: float, ma20: float, ma60: float | None = None) -> dict:
    """
    이평선 밀집 조건 판단.
    갭 계산: (max - min) / min * 100
    """
    values_3 = [v for v in [ma5, ma10, ma20] if v and not np.isnan(v)]
    values_4 = [v for v in [ma5, ma10, ma20, ma60] if v and not np.isnan(v)]

    def gap_pct(vals):
        if len(vals) < 2:
            return 0.0
        mn, mx = min(vals), max(vals)
        if mn == 0:
            return 999.0
        return (mx - mn) / mn * 100

    gap_3 = gap_pct(values_3)
    gap_4 = gap_pct(values_4)

    cluster_5_10_20 = gap_3 <= MA_CLUSTER_5_10_20_MAX_GAP_PCT
    cluster_all     = (len(values_4) == 4) and (gap_4 <= MA_CLUSTER_5_10_20_60_MAX_GAP_PCT)

    return {
        "cluster_5_10_20": cluster_5_10_20,
        "cluster_all":     cluster_all,
        "cluster":         cluster_5_10_20 or cluster_all,
        "gap_3ma_pct":     round(gap_3, 2),
        "gap_4ma_pct":     round(gap_4, 2),
    }


def is_big_candle(
    open_: float, high: float, low: float, close: float,
    change_pct: float, trading_value: float
) -> dict:
    """
    장대양봉/준장대양봉 판단.
    trading_value: 원 단위
    """
    # 전일 대비 상승 여부 (양봉/음봉 무관 — 상승 음봉도 기준봉으로 인정)
    is_bullish = change_pct > 0
    tv_ok = trading_value >= MIN_TRADING_VALUE_WON

    candle_range = high - low if (high - low) > 0 else 1
    upper_tail   = (high - close) / candle_range if candle_range else 0
    body         = (close - open_) / candle_range if candle_range else 0

    close_near_high_2 = ((high - close) / high * 100) <= 2.0 if high > 0 else False

    big_candle = (
        is_bullish and
        change_pct >= BIG_CANDLE_MIN_PCT and
        tv_ok and
        upper_tail <= BIG_CANDLE_UPPER_TAIL_MAX
    )
    loose_big_candle = (
        is_bullish and
        change_pct >= LOOSE_BIG_CANDLE_MIN_PCT and
        tv_ok and
        upper_tail <= LOOSE_BIG_CANDLE_UPPER_TAIL_MAX
    )

    return {
        "big_candle":        big_candle,
        "loose_big_candle":  loose_big_candle,
        "upper_tail_ratio":  round(upper_tail, 3),
        "body_ratio":        round(body, 3),
        "close_near_high":   close_near_high_2,
        "is_bullish":        is_bullish,
    }


def is_first_big_candle(daily_df: pd.DataFrame, today_idx: int = 0) -> dict:
    """
    최근 60거래일 내 첫 장대양봉(15%+) 여부 판단.
    daily_df: fetch_daily_history 반환값 (index 0이 최신)
    """
    if daily_df.empty or len(daily_df) <= today_idx + 1:
        return {"first_big_candle": False, "has_strong_candle_60d": False, "data_ok": False}

    lookback = daily_df.iloc[today_idx + 1 : today_idx + 1 + FIRST_BIG_CANDLE_LOOKBACK_DAYS]

    has_big_15 = False
    has_big_10 = False

    for _, row in lookback.iterrows():
        try:
            close_  = float(row.get("close",  0) or 0)
            change  = float(row.get("change", 0) or 0)   # 전일비(원)
            tv      = float(row.get("trading_value", 0) or 0)
            prev_close = close_ - change
            if prev_close > 0:
                pct = (close_ - prev_close) / prev_close * 100
            else:
                pct = 0.0
            # is_big_candle과 동일 기준: 전일 대비 상승 + 거래대금 충족
            rising = pct > 0
            tv_ok  = tv >= MIN_TRADING_VALUE_WON
            if rising and tv_ok and pct >= 15.0:
                has_big_15 = True
            if rising and tv_ok and pct >= 10.0:
                has_big_10 = True
        except Exception:
            continue

    return {
        "first_big_candle":    not has_big_15,
        "has_strong_candle_60d": has_big_10,
        "data_ok":             True,
    }


def is_volume_peak(daily_df: pd.DataFrame, today_idx: int = 0) -> bool:
    """오늘 거래량이 최근 60거래일 중 최고인지 판단"""
    if daily_df.empty or len(daily_df) < 2:
        return False
    today_vol = daily_df.iloc[today_idx]["volume"]
    lookback  = daily_df.iloc[today_idx + 1 : today_idx + 1 + VOLUME_PEAK_LOOKBACK_DAYS]["volume"]
    if lookback.empty:
        return False
    return float(today_vol) >= float(lookback.max())


def is_trading_value_peak(
    daily_df: pd.DataFrame,
    today_idx: int = 0,
    today_tv: float | None = None
) -> bool:
    """오늘 거래대금이 최근 60거래일 중 최고인지 판단. today_tv: 원 단위"""
    if today_tv is None:
        return False
    if daily_df.empty or "trading_value" not in daily_df.columns:
        return False
    lookback = daily_df.iloc[today_idx + 1 : today_idx + 1 + TRADING_VALUE_PEAK_LOOKBACK_DAYS]
    if "trading_value" not in lookback.columns or lookback.empty:
        return False
    return today_tv >= float(lookback["trading_value"].max())
