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
    STRUCTURE_BREAK_MAX_GAP_PCT,
    TV_RATIO_OK_MIN,
    TV_RATIO_WATCH_MIN,
    BASE_TV_EXPLOSION_MULT,
    CONSOLIDATION_LOOKBACK_DAYS,
    CONSOLIDATION_MAX_RANGE_PCT,
    PULLBACK_RESISTANCE_LOOKBACK_DAYS,
    PULLBACK_RESISTANCE_RECENT_DAYS,
    PULLBACK_RETEST_MARGIN_PCT,
)
from scripts.indicators import is_big_candle, is_first_big_candle, is_ma_cluster, calc_all_ma

logger = logging.getLogger(__name__)

MIN_TV_WON = MIN_TRADING_VALUE_EOK * 100_000_000


def detect_weak_candle(open_: float, close: float, change_pct: float) -> bool:
    """약음봉 여부: 음봉이고 등락률이 -2% 이내"""
    is_bearish = close < open_
    return is_bearish and change_pct >= PULLBACK_MAX_DROP_PCT


def _find_recent_big_candle(daily_df: pd.DataFrame, start_idx: int, lookback: int) -> int | None:
    """start_idx 이후 lookback 범위 내 기준봉 행 인덱스 반환 (없으면 None).
    기준봉 조건: 장대양봉(or 준장대양봉) AND 이전 20일 평균 거래대금 대비 BASE_TV_EXPLOSION_MULT배 이상.
    """
    for i in range(start_idx, min(start_idx + lookback, len(daily_df))):
        row = daily_df.iloc[i]
        try:
            base_tv = float(row.get("trading_value", 0) or 0)
            bc = is_big_candle(
                open_=float(row.get("open", 0) or 0),
                high=float(row.get("high", 0) or 0),
                low=float(row.get("low", 0) or 0),
                close=float(row.get("close", 0) or 0),
                change_pct=float(row.get("change", 0) or 0),
                trading_value=base_tv if base_tv > 0 else MIN_TV_WON,
            )
            if bc["big_candle"] or bc["loose_big_candle"]:
                past = daily_df.iloc[i + 1 : i + 21]["trading_value"].replace(0, float("nan"))
                avg_tv = past.mean()
                if pd.isna(avg_tv) or avg_tv <= 0 or base_tv >= avg_tv * BASE_TV_EXPLOSION_MULT:
                    return i
        except Exception:
            continue
    return None


def detect_consolidation_breakout(
    daily_df: pd.DataFrame,
    today_close: float,
    today_high: float,
) -> dict:
    """
    #14 기간조정 패턴: 최근 CONSOLIDATION_LOOKBACK_DAYS일 횡보 후 고가 돌파.
    조건: 과거 N일 변동폭(최고가 기준) ≤ CONSOLIDATION_MAX_RANGE_PCT AND 오늘 고가가 N일 고가 돌파.
    """
    n = CONSOLIDATION_LOOKBACK_DAYS
    if len(daily_df) < n + 1:
        return {"consolidation_flag": False}
    past = daily_df.iloc[1 : n + 1]
    highs  = past["high"].replace(0, float("nan")).dropna()
    lows   = past["low"].replace(0, float("nan")).dropna()
    if highs.empty or lows.empty:
        return {"consolidation_flag": False}
    max_high = float(highs.max())
    min_low  = float(lows.min())
    if max_high <= 0:
        return {"consolidation_flag": False}
    range_pct = (max_high - min_low) / max_high * 100
    breakout  = today_high >= max_high
    return {
        "consolidation_flag":       range_pct <= CONSOLIDATION_MAX_RANGE_PCT and breakout,
        "consolidation_range_pct":  round(range_pct, 2),
        "consolidation_high":       max_high,
    }


def detect_pullback_support(
    daily_df: pd.DataFrame,
    today_close: float,
    today_low: float,
) -> dict:
    """
    #15 되돌림 지지 패턴: 25일 저항선 돌파 후 되돌림 → 저항선 위 마감.
    저항선 R = days [RECENT+1 : LOOKBACK+1] 최고 종가.
    조건: ①최근 RECENT일 내 R 돌파 이력 ②오늘 저가가 R±MARGIN% ③오늘 종가≥R.
    """
    lookback = PULLBACK_RESISTANCE_LOOKBACK_DAYS
    recent   = PULLBACK_RESISTANCE_RECENT_DAYS
    margin   = PULLBACK_RETEST_MARGIN_PCT / 100.0

    if len(daily_df) < lookback + 1:
        return {"pullback_support_flag": False}

    # 저항선: recent+1~lookback일 전 최고 종가
    pivot_range = daily_df.iloc[recent + 1 : lookback + 1]
    if pivot_range.empty:
        return {"pullback_support_flag": False}
    closes = pivot_range["close"].replace(0, float("nan")).dropna()
    if closes.empty:
        return {"pullback_support_flag": False}
    resistance = float(closes.max())
    if resistance <= 0:
        return {"pullback_support_flag": False}

    # 조건①: 최근 recent일 내 종가가 저항선 위로 돌파한 적 있는가
    broke_above = any(
        float(daily_df.iloc[i].get("close", 0) or 0) >= resistance
        for i in range(1, recent + 1)
    )
    if not broke_above:
        return {"pullback_support_flag": False}

    # 조건②: 오늘 저가가 저항선 ±margin 이내 (되돌림 확인)
    retested = resistance * (1 - margin) <= today_low <= resistance * (1 + margin)

    # 조건③: 오늘 종가가 저항선 이상
    closed_above = today_close >= resistance

    return {
        "pullback_support_flag":    retested and closed_above,
        "pullback_resistance":      round(resistance, 0),
        "pullback_gap_pct":         round((today_close - resistance) / resistance * 100, 2)
                                    if resistance > 0 else None,
    }


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
    3가지 패턴 탐지 (당일돌파형 / 고가횡보형 / 눌림관찰형).

    daily_df: index 0이 오늘(최신), 이후 과거 순서.
    today_tv: 원 단위

    반환 필드:
      pattern1            : 당일돌파형 여부
      pattern2            : 눌림관찰형 여부
      pattern3            : 고가횡보형 여부
      pattern_summary     : 활성 패턴 이름 조합 (예: "당일돌파형", "고가횡보형+눌림관찰형")
      pattern_type_label  : 대표 타입 문자열 (우선순위: 당일돌파형 > 고가횡보형 > 눌림관찰형 > 없음)
      base_candle_day_offset : 기준봉 시점 (0=당일, 1=1일전, 2=2일전, 3=3일전, None=없음)
      base_high_gap_pct   : (오늘종가 - 기준봉고가) / 기준봉고가 * 100 (None=기준봉 없음)
      high_range_hold_flag: 오늘 종가가 기준봉 고가 대비 3% 이내
      post_base_volume_decline_flag: 기준봉 이후 거래대금 감소 여부
      pullback_watch_flag : 소폭 눌림 상태 (고가 대비 3~10% 이탈, 구조 유지)
      structure_broken_flag: 기준봉 이후 STRUCTURE_BREAK_MAX_GAP_PCT 초과 밀림 발생
      overheated_3d_flag  : 과열 여부 (3일 누적 급등 / 기준봉 위로 이격 과다 / 윗꼬리 과다)
      details             : 세부 계산 정보
    """
    result = {
        "pattern1": False,
        "pattern2": False,
        "pattern3": False,
        "pattern_summary": "없음",
        "pattern_type_label": "없음",
        "base_candle_day_offset": None,
        "base_high_gap_pct": None,
        "high_range_hold_flag": False,
        "tv_ratio": None,
        "tv_3d_flow": [],
        "status_summary": "약화",
        "post_base_volume_decline_flag": False,
        "pullback_watch_flag": False,
        "structure_broken_flag": False,
        "overheated_3d_flag": False,
        "new_high_60d": False,
        "near_high_60d": False,
        "consolidation_flag": False,
        "pullback_support_flag": False,
        "details": {},
    }

    if daily_df.empty or len(daily_df) < 2:
        result["details"]["error"] = "데이터 부족"
        return result

    tv_ok = today_tv >= MIN_TV_WON

    # ── 오늘 캔들 분석 ─────────────────────────────────────
    today_bc = is_big_candle(today_open, today_high, today_low, today_close,
                             today_change_pct, today_tv)
    is_base_today = today_bc["big_candle"] or today_bc["loose_big_candle"]

    # ── 패턴1: 당일돌파형 ──────────────────────────────────
    p1 = is_base_today and tv_ok

    # ── 최근 1~3일 내 기준봉 탐지 ──────────────────────────
    base_idx = _find_recent_big_candle(daily_df, start_idx=1, lookback=3)

    # ── 최근 3일 거래대금 흐름 (1일전 ~ 3일전) — 대시보드 표시용 ─
    tv_3d_flow = [
        float(daily_df.iloc[i].get("trading_value", 0) or 0)
        for i in range(1, min(4, len(daily_df)))
    ]

    # ── 기준봉 파생 지표 계산 ───────────────────────────────
    base_high_gap_pct: float | None = None
    high_range_hold_flag = False
    post_base_volume_decline_flag = False
    pullback_watch_flag = False
    structure_broken_flag = False
    tv_ratio: float | None = None

    if base_idx is not None:
        base_row  = daily_df.iloc[base_idx]
        base_high = float(base_row.get("high", 0) or 0)
        base_tv   = float(base_row.get("trading_value", 0) or 0)

        if base_high > 0:
            # 기준봉 고가 대비 오늘 종가 괴리율
            base_high_gap_pct = (today_close - base_high) / base_high * 100

            # 고가권 유지: 기준봉 고가 대비 -5% 이내
            high_range_hold_flag = base_high_gap_pct >= -HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT

            # 구조 붕괴: 기준봉~오늘 사이 중간일이 -8% 초과 밀린 날 존재
            for i in range(1, base_idx):
                day_close = float(daily_df.iloc[i].get("close", 0) or 0)
                if day_close > 0:
                    gap = (base_high - day_close) / base_high * 100
                    if gap > STRUCTURE_BREAK_MAX_GAP_PCT:
                        structure_broken_flag = True
                        break

            # 거래대금 ratio: 오늘 / 기준봉
            if base_tv > 0:
                tv_ratio = today_tv / base_tv
                between_tvs = [
                    float(daily_df.iloc[i].get("trading_value", 0) or 0)
                    for i in range(1, base_idx)
                ]
                all_between_ok = all(v <= base_tv for v in between_tvs if v > 0) if between_tvs else True
                post_base_volume_decline_flag = all_between_ok and (today_tv <= base_tv)

            # 눌림 관찰형: 기준봉 고가 대비 -5%~-8% 구간, 구조 미붕괴
            pullback_watch_flag = (
                base_high_gap_pct < -HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT
                and base_high_gap_pct >= -STRUCTURE_BREAK_MAX_GAP_PCT
                and not structure_broken_flag
            )

    # ── 상태 요약 ─────────────────────────────────────────
    if base_high_gap_pct is not None:
        if base_high_gap_pct >= -3.0:
            status_summary = "고가 유지"
        elif base_high_gap_pct >= -5.0:
            status_summary = "횡보"
        elif base_high_gap_pct >= -8.0:
            status_summary = "눌림"
        else:
            status_summary = "약화"
    elif p1:
        status_summary = "고가 유지"  # 당일돌파형은 오늘이 기준봉
    else:
        status_summary = "약화"

    # ── 60일 신고가 ───────────────────────────────────────────
    past_highs = [
        float(daily_df.iloc[i].get("high", 0) or 0)
        for i in range(1, len(daily_df))
    ]
    high_60d = max(past_highs) if past_highs else 0
    new_high_60d  = high_60d > 0 and today_high >= high_60d
    near_high_60d = high_60d > 0 and today_close >= high_60d * 0.97

    # ── 과확장 판정: 오늘 종가가 기준봉 고가 위 5% 초과 → 진입 위험 ─
    # today_high = today_close로 설정되어 윗꼬리 계산 불가 → 기준봉 고가 대비 이격으로 대체
    overheated_3d_flag = (
        base_idx is not None and
        base_high_gap_pct is not None and
        base_high_gap_pct > HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT
    )

    # ── 패턴3: 고가횡보형 ─────────────────────────────────
    p3 = (
        base_idx is not None
        and high_range_hold_flag
        and not structure_broken_flag
        and (tv_ratio is None or tv_ratio >= TV_RATIO_WATCH_MIN)
    )

    # ── 패턴2: 눌림관찰형 ─────────────────────────────────
    p2 = (
        base_idx is not None
        and pullback_watch_flag
        and not structure_broken_flag
        and (tv_ratio is None or tv_ratio >= TV_RATIO_WATCH_MIN)
    )

    # ── 대표 타입 ─────────────────────────────────────────
    if p1:
        pattern_type_label = "당일돌파형"
        base_candle_day_offset = 0
    elif p3:
        pattern_type_label = "고가횡보형"
        base_candle_day_offset = base_idx
    elif p2:
        pattern_type_label = "눌림관찰형"
        base_candle_day_offset = base_idx
    else:
        pattern_type_label = "없음"
        base_candle_day_offset = base_idx

    active_labels = (
        (["당일돌파형"] if p1 else [])
        + (["고가횡보형"] if p3 else [])
        + (["눌림관찰형"] if p2 else [])
    )
    pattern_summary = "+".join(active_labels) if active_labels else "없음"

    # ── 기준봉 이후 1~3일 상세 (base_idx > 1인 경우만) ──────────
    post_base_days: list[dict] = []
    if base_idx is not None and base_idx > 1:
        base_row  = daily_df.iloc[base_idx]
        base_high = float(base_row.get("high", 0) or 0)
        for i in range(1, base_idx):  # base_idx-1일전 ~ 1일전
            d = daily_df.iloc[i]
            d_close  = float(d.get("close", 0) or 0)
            d_high   = float(d.get("high", 0) or 0)
            d_tv     = float(d.get("trading_value", 0) or 0)
            d_change = float(d.get("change", 0) or 0)
            close_vs_base = (d_close - base_high) / base_high * 100 if base_high > 0 else None
            high_vs_base  = (d_high  - base_high) / base_high * 100 if base_high > 0 else None
            post_base_days.append({
                "offset":               i,
                "change_pct":           round(d_change, 2),
                "tv":                   d_tv,
                "close_vs_base_high":   round(close_vs_base, 2) if close_vs_base is not None else None,
                "high_vs_base_high":    round(high_vs_base,  2) if high_vs_base  is not None else None,
            })
        # 시간 순으로 정렬 (오래된 날 먼저)
        post_base_days.sort(key=lambda x: -x["offset"])

    # ── #14 기간조정 패턴 / #15 되돌림 지지 패턴 ───────────────
    consol = detect_consolidation_breakout(daily_df, today_close, today_high)
    pbs    = detect_pullback_support(daily_df, today_close, today_low)

    result.update({
        "pattern1": p1,
        "pattern2": p2,
        "pattern3": p3,
        "pattern_summary": pattern_summary,
        "pattern_type_label": pattern_type_label,
        "base_candle_day_offset": base_candle_day_offset,
        "base_high_gap_pct": base_high_gap_pct,
        "high_range_hold_flag": high_range_hold_flag,
        "tv_ratio": tv_ratio,
        "tv_3d_flow": tv_3d_flow,
        "post_base_days": post_base_days,
        "status_summary": status_summary,
        "post_base_volume_decline_flag": post_base_volume_decline_flag,
        "pullback_watch_flag": pullback_watch_flag,
        "structure_broken_flag": structure_broken_flag,
        "overheated_3d_flag": overheated_3d_flag,
        "new_high_60d": new_high_60d,
        "near_high_60d": near_high_60d,
        "consolidation_flag":    consol.get("consolidation_flag", False),
        "pullback_support_flag": pbs.get("pullback_support_flag", False),
        "details": {
            "today_big_candle":        today_bc.get("big_candle", False),
            "today_loose_bc":          today_bc.get("loose_big_candle", False),
            "base_idx":                base_idx,
            "consolidation_range_pct": consol.get("consolidation_range_pct"),
            "pullback_resistance":     pbs.get("pullback_resistance"),
            "pullback_gap_pct":        pbs.get("pullback_gap_pct"),
        },
    })
    return result
