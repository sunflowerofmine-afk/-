# scripts/review.py
"""T+1 자동 복기 — 어제 신호 성과 측정 및 실패 원인 분류"""

import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scripts.fetch_stock_data import fetch_daily_history

try:
    from config.settings import REQUEST_DELAY
except Exception:
    REQUEST_DELAY = 0.3

logger = logging.getLogger(__name__)

_SIGNALS_DIR = Path("data/signals")
_MAX_REVIEW   = 15  # 최대 복기 종목 수 (요청 시간 제한)


def _find_yesterday_signals(today: date) -> pd.DataFrame | None:
    """최근 거래일(최대 7일 이내) 2차(1750) → 1차(1450) 순으로 signals CSV 탐색."""
    for days_back in range(1, 8):
        d     = today - timedelta(days=days_back)
        d_str = d.isoformat()
        for suffix in ("1750", "1450"):
            path = _SIGNALS_DIR / f"{d_str}_{suffix}_signals.csv"
            if path.exists():
                try:
                    df = pd.read_csv(path, dtype={"종목코드": str})
                    logger.info(f"복기 시그널 로드: {path} ({len(df)}개)")
                    return df
                except Exception as e:
                    logger.warning(f"시그널 로드 실패 {path}: {e}")
    return None


def _classify_fail_reason(gap_pct: float, kospi_chg: float | None) -> str | None:
    """갭 기반 실패 원인 분류. 성공이면 None."""
    if gap_pct >= 0:
        return None
    if kospi_chg is None:
        return "혼조"
    if kospi_chg <= -1.0:
        return "시황하락"
    if kospi_chg >= 0.5:
        return "개별약세"
    return "혼조"


def run(today: date, kospi_chg_today: float | None) -> list[dict]:
    """
    어제 신호 → 오늘 시가 갭 측정 → 실패 원인 분류.
    반환: list[dict] — 각 dict에 gap_pct, result, fail_reason 포함.
    """
    signals_df = _find_yesterday_signals(today)
    if signals_df is None or signals_df.empty:
        logger.info("복기: 어제 시그널 없음")
        return []

    today_str = today.strftime("%Y.%m.%d")
    rows      = signals_df.head(_MAX_REVIEW).to_dict("records")
    results   = []

    for row in rows:
        code = str(row.get("종목코드", ""))
        name = str(row.get("종목명", ""))
        sp   = row.get("signal_price")
        entry = {
            "code":         code,
            "name":         name,
            "signal_price": sp,
            "t1_open":      None,
            "t1_close":     None,
            "gap_pct":      None,
            "hold_pct":     None,
            "result":       "pending",
            "fail_reason":  None,
            "pattern_type": str(row.get("pattern_type_label", "")),
            "sector":       str(row.get("sector", "")),
        }

        try:
            hist = fetch_daily_history(code, pages=2)
            time.sleep(REQUEST_DELAY)

            if hist.empty:
                results.append(entry)
                continue

            today_rows = hist[hist["date"] == today_str]
            if today_rows.empty:
                results.append(entry)
                continue

            t1     = today_rows.iloc[0]
            t1_open  = float(t1.get("open")  or 0)
            t1_close = float(t1.get("close") or 0)

            # 진입가: CSV의 signal_price 우선, 없으면 어제 종가(hist에서 T-1 close)
            entry_price = float(sp) if sp and float(sp) > 0 else 0.0
            if entry_price <= 0:
                prev_rows = hist[hist["date"] != today_str]
                if prev_rows.empty:
                    results.append(entry)
                    continue
                entry_price = float(prev_rows.iloc[0]["close"] or 0)

            if entry_price <= 0 or t1_open <= 0:
                results.append(entry)
                continue

            gap_pct  = (t1_open  / entry_price - 1) * 100
            hold_pct = (t1_close / entry_price - 1) * 100 if t1_close > 0 else None

            entry.update({
                "t1_open":     t1_open,
                "t1_close":    t1_close if t1_close > 0 else None,
                "gap_pct":     round(gap_pct, 2),
                "hold_pct":    round(hold_pct, 2) if hold_pct is not None else None,
                "result":      "성공" if gap_pct >= 0 else "실패",
                "fail_reason": _classify_fail_reason(gap_pct, kospi_chg_today),
            })

        except Exception as e:
            logger.warning(f"[{code}] 복기 수집 실패: {e}")

        results.append(entry)

    success_n = sum(1 for r in results if r.get("result") == "성공")
    total_n   = sum(1 for r in results if r.get("result") in ("성공", "실패"))
    logger.info(f"복기 완료: {success_n}/{total_n} 성공")
    return results
