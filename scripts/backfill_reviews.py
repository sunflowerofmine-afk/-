# scripts/backfill_reviews.py
"""
과거 signal CSV → review.json 생성 (review.json 없는 날짜만 처리).
실행: python -m scripts.backfill_reviews
"""
import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.fetch_stock_data import fetch_daily_history
from scripts.review import _enrich_entry_with_returns

try:
    from config.settings import REQUEST_DELAY
except Exception:
    REQUEST_DELAY = 0.3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SIGNALS_DIR = Path("data/signals")
_DATE_RE     = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})_signals\.csv$")
_START_DATE  = "2026-04-27"  # 이전은 시스템 구조가 달라 제외


def _best_signal_file(signal_date: str) -> Path | None:
    """해당 날짜 signal CSV 중 1700~1959 타임스탬프 파일 우선, 없으면 최신."""
    candidates = []
    for f in _SIGNALS_DIR.glob(f"{signal_date}_*_signals.csv"):
        m = _DATE_RE.match(f.name)
        if m:
            candidates.append((int(m.group(2)), f))
    if not candidates:
        return None
    preferred = [(t, f) for t, f in candidates if 1700 <= t <= 1959]
    pool = preferred if preferred else candidates
    # 1750에 가장 가까운 것
    return min(pool, key=lambda x: abs(x[0] - 1750))[1]


def _signal_price_from_hist(hist: pd.DataFrame, signal_date: str) -> float:
    sig_dot = signal_date.replace("-", ".")
    rows = hist[hist["date"] == sig_dot]
    if not rows.empty:
        try:
            return float(rows.iloc[0]["close"])
        except (TypeError, ValueError):
            pass
    return 0.0


def run() -> None:
    today = date.today().isoformat()

    # 백필 대상: signal 파일 있고 review.json 없는 날짜
    all_dates: set[str] = set()
    for f in _SIGNALS_DIR.glob("*_signals.csv"):
        m = _DATE_RE.match(f.name)
        if m:
            all_dates.add(m.group(1))

    targets = sorted([
        d for d in all_dates
        if d >= _START_DATE
        and d < today
        and not (_SIGNALS_DIR / f"{d}_review.json").exists()
    ])

    if not targets:
        logger.info("백필 대상 없음 (모든 날짜에 review.json 존재).")
        return

    logger.info(f"백필 대상 {len(targets)}개 날짜: {targets}")

    for signal_date in targets:
        sig_path = _best_signal_file(signal_date)
        if sig_path is None:
            continue

        logger.info(f"처리: {sig_path.name}")
        try:
            df = pd.read_csv(sig_path, dtype={"종목코드": str})
        except Exception as e:
            logger.warning(f"CSV 로드 실패: {e}")
            continue

        if df.empty:
            continue

        results = []
        for _, row in df.iterrows():
            code = str(row.get("종목코드", "")).strip()
            name = str(row.get("종목명", "")).strip()
            if not code:
                continue

            sp_raw = row.get("signal_price")
            try:
                sp = float(sp_raw) if sp_raw and not pd.isna(sp_raw) else None
            except (TypeError, ValueError):
                sp = None

            in_inter_raw = row.get("in_inter")
            try:
                in_inter = bool(in_inter_raw) if in_inter_raw and not pd.isna(in_inter_raw) else False
            except (TypeError, ValueError):
                in_inter = False

            bgp_raw = row.get("base_high_gap_pct")
            try:
                base_high_gap_pct = float(bgp_raw) if bgp_raw is not None and not pd.isna(bgp_raw) else None
            except (TypeError, ValueError):
                base_high_gap_pct = None

            entry: dict = {
                "code":              code,
                "name":              name,
                "signal_price":      sp,
                "t1_open":           None,
                "t1_close":          None,
                "gap_pct":           None,
                "hold_pct":          None,
                "result":            "pending",
                "fail_reason":       None,
                "pattern_type":      str(row.get("pattern_type_label") or "없음"),
                "sector":            str(row.get("sector") or ""),
                "total_score":       int(row.get("total_score") or 0),
                "signal_date":       signal_date,
                "signal_tv":         float(row.get("거래대금") or 0),
                "signal_change_pct": float(row.get("등락률") or 0) or None,
                "in_inter":          in_inter,
                "base_high_gap_pct": base_high_gap_pct,
                "d1_open_pct":       None, "d1_high_pct":  None, "d1_close_pct": None,
                "d2_high_pct":       None, "d2_close_pct": None,
                "d3_high_pct":       None, "d3_close_pct": None,
                "d5_high_pct":       None, "d5_close_pct": None,
                "mfe":               None, "mae": None, "mfe_day": None, "mae_day": None,
                "interim_result_type": "pending",
                "final_result_type":   None,
                "alive_pullback":      None,
                "failed_structure":    None,
                "sector_still_active": None,
            }

            try:
                hist = fetch_daily_history(code, pages=2)
                time.sleep(REQUEST_DELAY)
                if hist.empty:
                    results.append(entry)
                    continue

                # signal_price 없으면 당일 종가로 대체
                entry_price = sp if sp and sp > 0 else _signal_price_from_hist(hist, signal_date)
                if entry_price and entry_price > 0:
                    entry["signal_price"] = entry_price

                    # D+1 시가/종가 → result 판정
                    sig_dot = signal_date.replace("-", ".")
                    post = hist[hist["date"] > sig_dot].sort_values("date").reset_index(drop=True)
                    if not post.empty:
                        r1 = post.iloc[0]
                        t1_open  = float(r1.get("open")  or 0)
                        t1_close = float(r1.get("close") or 0)
                        if t1_open > 0:
                            gap  = round((t1_open  / entry_price - 1) * 100, 2)
                            hold = round((t1_close / entry_price - 1) * 100, 2) if t1_close > 0 else None
                            entry.update({
                                "t1_open":  t1_open,
                                "t1_close": t1_close or None,
                                "gap_pct":  gap,
                                "hold_pct": hold,
                                "result":   "성공" if gap >= 0 else "실패",
                            })

                _enrich_entry_with_returns(entry, hist)

            except Exception as e:
                logger.warning(f"[{code}] 수집 실패: {e}")

            results.append(entry)

        if results:
            out = _SIGNALS_DIR / f"{signal_date}_review.json"
            out.write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            success = sum(1 for r in results if r.get("result") == "성공")
            logger.info(f"저장: {out.name} ({len(results)}건, 성공 {success}건)")


if __name__ == "__main__":
    run()
