# scripts/pullback_observer.py
"""
일반 눌림 관찰 — 기존 종가베팅 체계와 완전 분리.
매수 신호 아님. 데이터 축적 목적.
수정 금지: 기존 종가베팅 후보/점수/알림/패턴/김형준 기법 일체.
"""

import json
import logging
import re as _re
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _load_base_pool(signals_dir: Path, run_date: str, lookback_days: int) -> dict[str, str]:
    """과거 signals.csv에서 당일돌파형 종목 수집. 반환: {code: base_date}"""
    code_to_date: dict[str, str] = {}
    seen_dates: set[str] = set()

    for f in sorted(signals_dir.glob("*_signals.csv"), reverse=True):
        m = _re.match(r"^(\d{4}-\d{2}-\d{2})_\d{4}_signals\.csv$", f.name)
        if not m:
            continue
        file_date = m.group(1)
        if file_date >= run_date:
            continue
        if file_date not in seen_dates and len(seen_dates) >= lookback_days:
            break
        seen_dates.add(file_date)
        try:
            df = pd.read_csv(f, dtype={"종목코드": str})
            if "pattern_type_label" not in df.columns or "종목코드" not in df.columns:
                continue
            mask = df["pattern_type_label"] == "당일돌파형"
            for code in df.loc[mask, "종목코드"].dropna().astype(str):
                if code not in code_to_date:
                    code_to_date[code] = file_date
        except Exception:
            continue

    return code_to_date


def _find_base_candle(
    daily_df: pd.DataFrame,
    lookback_days: int,
    min_chg_pct: float,
    min_tv_eok: float,
    tv_mult: float,
) -> dict | None:
    """
    최근 lookback_days 내 기준봉 탐색 (index 1 ~ lookback_days).
    조건: change_pct >= min_chg_pct AND (tv >= min_tv_eok억 OR tv >= 20일평균 * tv_mult)
    반환: 기준봉 정보 dict, 없으면 None.
    """
    if len(daily_df) < 3:
        return None

    search_end = min(lookback_days, len(daily_df) - 1)

    for i in range(1, search_end + 1):
        row = daily_df.iloc[i]
        chg = float(row.get("change_pct", 0) or 0)
        if chg < min_chg_pct:
            continue

        tv = float(row.get("trading_value", 0) or 0)
        tv_eok = tv / 1e8

        prior = daily_df.iloc[i + 1: i + 21]
        avg_tv = float(prior["trading_value"].mean()) if not prior.empty else 0.0
        tv_ratio_to_avg = (tv / avg_tv) if avg_tv > 0 else 0.0

        if tv_eok >= min_tv_eok or (avg_tv > 0 and tv_ratio_to_avg >= tv_mult):
            return {
                "idx":               i,
                "date":              str(row.get("date", "")),
                "change_pct":        round(chg, 2),
                "trading_value":     tv,
                "trading_value_eok": round(tv_eok, 1),
                "high":              float(row.get("high", 0) or 0),
                "low":               float(row.get("low",  0) or 0),
                "close":             float(row.get("close", 0) or 0),
                "open":              float(row.get("open",  0) or 0),
                "tv_ratio_to_avg":   round(tv_ratio_to_avg, 2),
            }

    return None


def run(
    date: str,
    filtered_df: pd.DataFrame,
    code_to_sector: dict,
    market_regime: str,
    adl: float,
    index_return_1d: float | None,
    signals_dir: Path,
    asof_date: str | None = None,
) -> list[dict]:
    """
    일반 눌림 관찰 후보 탐색 + JSONL 저장.
    반환: 대시보드 표시용 관찰 후보 리스트.

    asof_date (YYYY-MM-DD): 소급 모드. 주어지면 그 날짜를 '오늘'로 간주해
      각 종목 일봉을 asof_date 이하로 잘라 그날 시점 종가·거래대금·등락률을
      일봉에서 직접 산출(미래 데이터 누수 제거). filtered_df는 종목명·시장만
      제공하면 됨(거래대금/등락률은 일봉에서 취함). weekly_research가 호출.
    """
    from config.settings import (
        PULLBACK_OBS_DIR,
        PULLBACK_OBS_SIGNALS_LOOKBACK_DAYS,
        PULLBACK_OBS_BASE_CANDLE_MIN_PCT,
        PULLBACK_OBS_BASE_TV_MIN_EOK,
        PULLBACK_OBS_BASE_TV_MULT,
        PULLBACK_OBS_DRAWDOWN_NORMAL_MAX,
        PULLBACK_OBS_DRAWDOWN_NORMAL_MIN,
        PULLBACK_OBS_DRAWDOWN_DEEP_MIN,
        PULLBACK_OBS_TODAY_TV_MIN_EOK,
        PULLBACK_OBS_NEAR_MA_THRESHOLD_PCT,
        PULLBACK_OBS_TV_DRY_RATIO,
        REQUEST_DELAY,
    )
    from scripts.fetch_stock_data import fetch_chart_data
    from scripts.indicators import calc_all_ma

    PULLBACK_OBS_DIR.mkdir(parents=True, exist_ok=True)
    obs_log = PULLBACK_OBS_DIR / "low_position_watch_log.jsonl"

    min_tv_won = PULLBACK_OBS_TODAY_TV_MIN_EOK * 1e8

    # 과거 신호에서 당일돌파형 기준봉 pool 수집 (소급 시 asof_date 이전 신호만)
    base_pool = _load_base_pool(signals_dir, asof_date or date, PULLBACK_OBS_SIGNALS_LOOKBACK_DAYS)
    if not base_pool:
        logger.info("pullback_observer: 과거 신호 없음")
        return []

    if asof_date:
        # 소급 모드: 그날 거래대금/등락률은 루프 내 일봉에서 산출 → 후보는 pool 전체
        candidate_codes = list(base_pool.keys())
    else:
        # 오늘 거래대금/등락률 맵
        tv_map  = filtered_df.set_index("종목코드")["거래대금"].to_dict()
        chg_map = filtered_df.set_index("종목코드")["등락률"].to_dict()
        # TV >= 설정값, 상한가 아닌, filtered_df에 존재하는 종목
        candidate_codes = [
            code for code in base_pool
            if float(tv_map.get(code, 0)) >= min_tv_won
            and float(chg_map.get(code, 0)) < 29.5
            and not filtered_df[filtered_df["종목코드"] == code].empty
        ]
    logger.info(
        f"pullback_observer: pool {len(base_pool)}개 → TV필터 후 {len(candidate_codes)}개"
    )

    results: list[dict] = []
    thr = PULLBACK_OBS_NEAR_MA_THRESHOLD_PCT

    asof_dot = asof_date.replace("-", ".") if asof_date else None

    for code in candidate_codes:
        try:
            row_df = filtered_df[filtered_df["종목코드"] == code]
            # 소급 모드: 종목명/시장만 참조(없어도 진행). 평시: 없으면 skip.
            if row_df.empty and not asof_date:
                continue
            row = row_df.iloc[0] if not row_df.empty else None

            name   = str(row.get("종목명", "")) if row is not None else ""
            market = str(row.get("시장", ""))   if row is not None else ""
            sector = code_to_sector.get(code, "")

            daily_df = fetch_chart_data(code)
            time.sleep(REQUEST_DELAY)
            if daily_df.empty:
                continue

            if asof_date:
                # asof_date 이하로 잘라 그날을 '오늘'로 (미래 봉 제거)
                daily_df = daily_df[daily_df["date"] <= asof_dot].reset_index(drop=True)
                if len(daily_df) < 5:
                    continue
                _r0         = daily_df.iloc[0]
                today_close = float(_r0.get("close", 0) or 0)
                today_tv    = float(_r0.get("trading_value", 0) or 0)
                _prev_c     = float(daily_df.iloc[1].get("close", 0) or 0) if len(daily_df) > 1 else 0.0
                today_chg   = (today_close / _prev_c - 1) * 100 if _prev_c > 0 else 0.0
                # 소급 TV/상한가 필터 (평시 candidate_codes 필터와 동일 기준)
                if today_tv < min_tv_won or today_chg >= 29.5:
                    continue
            else:
                if len(daily_df) < 5:
                    continue
                today_close = float(row.get("현재가", 0) or 0)
                today_chg   = float(row.get("등락률", 0) or 0)
                today_tv    = float(row.get("거래대금", 0) or 0)

            if today_close <= 0:
                continue

            daily_df = calc_all_ma(daily_df)

            # 기준봉 탐색
            base = _find_base_candle(
                daily_df,
                lookback_days=PULLBACK_OBS_SIGNALS_LOOKBACK_DAYS,
                min_chg_pct=PULLBACK_OBS_BASE_CANDLE_MIN_PCT,
                min_tv_eok=PULLBACK_OBS_BASE_TV_MIN_EOK,
                tv_mult=PULLBACK_OBS_BASE_TV_MULT,
            )
            if base is None:
                continue

            base_idx   = base["idx"]
            base_tv    = base["trading_value"]
            base_high  = base["high"]
            base_close = base["close"]
            base_low   = base["low"]
            base_mid   = (base_high + base_low) / 2 if base_high > 0 else base_close

            # 최근 고점: 오늘부터 기준봉 당일까지 고가(high) 최대값
            window = daily_df.iloc[: base_idx + 1]
            if "high" in window.columns and not window["high"].isna().all():
                peak_loc      = int(window["high"].idxmax())
                recent_peak   = float(window["high"].max())
                recent_peak_d = str(daily_df.iloc[peak_loc].get("date", ""))
            else:
                recent_peak   = today_close
                recent_peak_d = date

            if recent_peak <= 0:
                continue

            # 눌림폭 (오늘 종가 / 최근 고점 고가)
            drawdown = (today_close - recent_peak) / recent_peak * 100

            if drawdown > PULLBACK_OBS_DRAWDOWN_NORMAL_MAX:   # > -4% → 아직 안 눌림
                continue
            if drawdown < PULLBACK_OBS_DRAWDOWN_DEEP_MIN:     # < -18% → 과도한 하락
                continue

            # MA 거리
            row0 = daily_df.iloc[0]
            ma5  = float(row0.get("ma5",  0) or 0)
            ma10 = float(row0.get("ma10", 0) or 0)
            ma20 = float(row0.get("ma20", 0) or 0)

            def _dist(ma: float) -> float | None:
                return (today_close - ma) / ma * 100 if ma > 0 else None

            dist_ma5      = _dist(ma5)
            dist_ma10     = _dist(ma10)
            dist_ma20     = _dist(ma20)
            dist_base_mid = _dist(base_mid) if base_mid > 0 else None

            near_ma5      = dist_ma5      is not None and abs(dist_ma5)      <= thr
            near_ma10     = dist_ma10     is not None and abs(dist_ma10)     <= thr
            near_ma20     = dist_ma20     is not None and abs(dist_ma20)     <= thr
            near_base_mid = dist_base_mid is not None and abs(dist_base_mid) <= thr

            # 지지선 근접 조건 미충족 시 저장 제외
            if not any([near_ma5, near_ma10, near_ma20, near_base_mid]):
                continue

            # 구조 유지 계산
            broken_from_base_close = (
                (today_close / base_close - 1) * 100 <= -8.0
                if base_close > 0 else False
            )
            below_base_mid = today_close < base_mid if base_mid > 0 else False
            below_ma20     = dist_ma20 is not None and dist_ma20 < 0
            structure_broken = bool(broken_from_base_close) or (below_base_mid and below_ma20)

            # 최근 3일 평균 거래대금 (어제~3일전, chart 기준)
            recent_3d_df    = daily_df.iloc[1:4]
            recent_3d_avg_tv = float(recent_3d_df["trading_value"].mean()) if not recent_3d_df.empty else 0.0
            recent_3d_ratio  = (recent_3d_avg_tv / base_tv) if base_tv > 0 else None

            # 상대강도 3일 (종목 3일 수익률 - 시장 3일 근사)
            relative_strength_3d = None
            if index_return_1d is not None and len(daily_df) >= 4:
                close_3d_ago = float(daily_df.iloc[3].get("close", 0) or 0)
                if close_3d_ago > 0:
                    stock_3d_ret     = (today_close / close_3d_ago - 1) * 100
                    market_3d_approx = index_return_1d * 3
                    relative_strength_3d = round(stock_3d_ret - market_3d_approx, 2)

            # 태그 생성
            tags: list[str] = []

            if PULLBACK_OBS_DRAWDOWN_NORMAL_MIN <= drawdown <= PULLBACK_OBS_DRAWDOWN_NORMAL_MAX:
                obs_type = "일반 눌림 관찰"
            else:
                obs_type = "깊은 눌림 관찰"
                tags.append("깊은눌림")

            if structure_broken:
                tags.append("구조훼손")

            if today_chg < 0 and recent_3d_avg_tv > 0:
                if today_tv / recent_3d_avg_tv > 1.5:
                    tags.append("거래대금 실린 음봉 위험")

            if recent_3d_ratio is not None and recent_3d_ratio <= PULLBACK_OBS_TV_DRY_RATIO:
                tags.append("거래대금건조")

            if relative_strength_3d is not None and relative_strength_3d > 0:
                tags.append("상대강도 양호")

            if near_ma5:      tags.append("5일선근접")
            if near_ma10:     tags.append("10일선근접")
            if near_ma20:     tags.append("20일선근접")
            if near_base_mid: tags.append("기준봉중심근접")

            results.append({
                "date":                              asof_date or date,
                "code":                              code,
                "name":                              name,
                "market":                            market,
                "sector":                            sector,
                "market_regime":                     market_regime,
                "adl":                               round(adl, 4),
                "index_return_1d":                   index_return_1d,
                "close":                             today_close,
                "change_pct":                        today_chg,
                "trading_value":                     today_tv,
                "base_date":                         base["date"],
                "base_change_pct":                   base["change_pct"],
                "base_trading_value":                base_tv,
                "base_trading_value_ratio":          base["tv_ratio_to_avg"],
                "recent_peak_date":                  recent_peak_d,
                "recent_peak_price":                 recent_peak,
                "drawdown_from_peak_pct":            round(drawdown, 2),
                "distance_to_ma5_pct":               round(dist_ma5, 2)      if dist_ma5      is not None else None,
                "distance_to_ma10_pct":              round(dist_ma10, 2)     if dist_ma10     is not None else None,
                "distance_to_ma20_pct":              round(dist_ma20, 2)     if dist_ma20     is not None else None,
                "distance_to_base_mid_pct":          round(dist_base_mid, 2) if dist_base_mid is not None else None,
                "near_ma5":                          near_ma5,
                "near_ma10":                         near_ma10,
                "near_ma20":                         near_ma20,
                "near_base_mid":                     near_base_mid,
                "recent_3d_avg_trading_value_ratio": round(recent_3d_ratio, 3) if recent_3d_ratio is not None else None,
                "today_trading_value_eok":           round(today_tv / 1e8, 1),
                "relative_strength_3d":              relative_strength_3d,
                "foreign_flow":                      None,
                "institution_flow":                  None,
                "program_flow":                      None,
                "observation_tags":                  tags,
                "is_buy_signal":                     False,
                "observation_type":                  obs_type,
            })

        except Exception as e:
            logger.warning(f"pullback_observer [{code}]: {e}")

    if results:
        with open(obs_log, "a", encoding="utf-8") as fh:
            for rec in results:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info(f"pullback_observer: {len(results)}개 저장 → {obs_log}")
    else:
        logger.info("pullback_observer: 조건 충족 종목 없음")

    return results
