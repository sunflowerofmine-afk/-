# scripts/pattern_detector.py
"""캔들 패턴 탐지 모듈"""

import sys
import logging
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT,
    HIGH_RANGE_HOLD_DAYS,
    PULLBACK_MAX_DROP_PCT,
    MIN_TRADING_VALUE_EOK,
)
from scripts.indicators import is_big_candle, is_first_big_candle, is_ma_cluster, calc_all_ma

logger = logging.getLogger(__name__)

MIN_TV_WON = MIN_TRADING_VALUE_EOK * 100_000_000


def detect_weak_candle(open_: float, close: float, change_pct: float) -> bool:
    """약음봉 여부: 음봉이고 등락률이 -2% 이내"""
    is_bearish = close < open_
    return is_bearish and change_pct >= PULLBACK_MAX_DROP_PCT


def _find_recent_big_candle(daily_df: pd.DataFrame, start_idx: int, lookback: int) -> int | None:
    """start_idx 이후 lookback 범위 내 장대양봉 행 인덱스 반환 (없으면 None)"""
    for i in range(start_idx, min(start_idx + lookback, len(daily_df))):
        row = daily_df.iloc[i]
        try:
            bc = is_big_candle(
                open_=float(row.get("open", 0) or 0),
                high=float(row.get("high", 0) or 0),
                low=float(row.get("low", 0) or 0),
                close=float(row.get("close", 0) or 0),
                change_pct=float(row.get("change", 0) or 0),
                trading_value=float(row.get("trading_value", 0) or MIN_TV_WON),
            )
            if bc["big_candle"] or bc["loose_big_candle"]:
                return i
        except Exception:
            continue
    return None


def detect_patterns(
    code: str,
    today_open: float,
    today_high: float,
    today_low: float,
    today_close: float,
    today_change_pct: float,
    today_tv: float,
    daily_df: pd.DataFrame,
) -> dict:
    """
    3가지 패턴 탐지.
    daily_df: index 0이 오늘(또는 최신), 오름차순 최신순.
    today_tv: 원 단위
    반환: {pattern1, pattern2, pattern3, pattern_summary, details}
    """
    result = {
        "pattern1": False,
        "pattern2": False,
        "pattern3": False,
        "pattern_summary": "없음",
        "details": {},
    }

    if daily_df.empty or len(daily_df) < 3:
        result["details"]["error"] = "데이터 부족"
        return result

    # ── 오늘 캔들 분석 ─────────────────────────────────────
    today_bc = is_big_candle(today_open, today_high, today_low, today_close,
                             today_change_pct, today_tv)
    first_bc  = is_first_big_candle(daily_df, today_idx=0)

    # MA 계산 (close 컬럼 필요)
    df_with_ma = calc_all_ma(daily_df)
    row0 = df_with_ma.iloc[0]
    ma_cluster = is_ma_cluster(
        ma5=row0.get("ma5", 0),
        ma10=row0.get("ma10", 0),
        ma20=row0.get("ma20", 0),
        ma60=row0.get("ma60"),
    )

    tv_ok = today_tv >= MIN_TV_WON

    # ── 패턴 1: 첫 장대양봉 돌파형 ───────────────────────────
    p1 = (
        today_bc["big_candle"] and
        first_bc["first_big_candle"] and
        ma_cluster["cluster"] and
        tv_ok
    )
    result["pattern1"] = p1
    result["details"]["pattern1"] = {
        "big_candle":       today_bc["big_candle"],
        "first_big_candle": first_bc["first_big_candle"],
        "ma_cluster":       ma_cluster["cluster"],
        "tv_ok":            tv_ok,
    }

    # ── 패턴 2: 장대양봉 후 1~2거래일 눌림형 ────────────────
    # 오늘은 양봉이 아님 (눌림), 1~2일 전에 기준봉 있음
    base_idx = _find_recent_big_candle(daily_df, start_idx=1, lookback=2)
    p2 = False
    if base_idx is not None:
        base_row  = daily_df.iloc[base_idx]
        base_tv   = float(base_row.get("trading_value", 0) or 0)
        today_tv_lower = (today_tv < base_tv) if base_tv > 0 else False
        weak = detect_weak_candle(today_open, today_close, today_change_pct)
        p2 = weak and today_tv_lower
    result["pattern2"] = p2
    result["details"]["pattern2"] = {
        "base_candle_found": base_idx is not None,
        "weak_candle":       detect_weak_candle(today_open, today_close, today_change_pct),
    }

    # ── 패턴 3: 장대양봉 후 고가권 3일 횡보형 ───────────────
    base_idx3 = _find_recent_big_candle(daily_df, start_idx=3, lookback=5)
    p3 = False
    if base_idx3 is not None:
        base_high = float(daily_df.iloc[base_idx3].get("high", 0) or 0)
        if base_high > 0:
            hold_days = min(base_idx3, HIGH_RANGE_HOLD_DAYS)
            in_range  = True
            for i in range(1, hold_days + 1):
                if i >= len(daily_df):
                    break
                c = float(daily_df.iloc[i].get("close", 0) or 0)
                gap = (base_high - c) / base_high * 100
                if gap > HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT:
                    in_range = False
                    break
            p3 = in_range
    result["pattern3"] = p3
    result["details"]["pattern3"] = {
        "base_candle_found": base_idx3 is not None,
        "hold_days":         HIGH_RANGE_HOLD_DAYS,
    }

    # ── 패턴 요약 ─────────────────────────────────────────
    names = [f"패턴{i+1}" for i, flag in enumerate([p1, p2, p3]) if flag]
    result["pattern_summary"] = "+".join(names) if names else "없음"

    return result
