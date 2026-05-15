"""scripts/_preview_dashboard.py
기존 signals CSV + daily_summary.json으로 대시보드 HTML만 재렌더링 (UI 시안용).
데이터 재수집 없음. 수급/뉴스/지표 세부 데이터는 비어있을 수 있음.

Usage:
    python -m scripts._preview_dashboard
    python -m scripts._preview_dashboard --date 2026-05-15 --snap 1750
"""
import argparse, json, subprocess, sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import SIGNALS_DIR, REPORTS_DIR
from scripts.dashboard import generate_dashboard_html


def _load_signals(date_str: str, snap: str) -> pd.DataFrame:
    p = Path(SIGNALS_DIR) / f"{date_str}_{snap}_signals.csv"
    if not p.exists():
        # fallback: 해당 날짜 최신 1750/1450
        for s in ["1750", "1450"]:
            p = Path(SIGNALS_DIR) / f"{date_str}_{s}_signals.csv"
            if p.exists():
                break
    if not p.exists():
        sys.exit(f"시그널 파일 없음: {date_str}_{snap}_signals.csv")
    return pd.read_csv(p, dtype={"종목코드": str}, encoding="utf-8-sig")


def _load_summary(date_str: str) -> dict:
    p = Path(SIGNALS_DIR) / f"daily_summary_{date_str}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _to_candidate(row: pd.Series) -> dict:
    tv_ratio = row.get("tv_ratio")
    try:
        tv_ratio = float(tv_ratio) if tv_ratio and str(tv_ratio) != "nan" else None
    except Exception:
        tv_ratio = None
    gap_pct = row.get("base_high_gap_pct")
    try:
        gap_pct = float(gap_pct) if gap_pct and str(gap_pct) != "nan" else None
    except Exception:
        gap_pct = None
    offset = row.get("base_candle_offset")
    try:
        offset = int(offset) if offset and str(offset) != "nan" else None
    except Exception:
        offset = None

    entry_ref = row.get("entry_reference_price") or row.get("signal_price") or 0
    try:
        entry_ref = float(entry_ref)
    except Exception:
        entry_ref = 0.0

    return {
        "name":             str(row.get("종목명", "")),
        "code":             str(row.get("종목코드", "")),
        "market":           str(row.get("시장", "")),
        "change_pct":       float(row.get("등락률", 0) or 0),
        "trading_value":    float(row.get("거래대금", 0) or 0),
        "signal_price":     float(row.get("signal_price", 0) or 0),
        "entry_reference_price": entry_ref,
        "price_source":     str(row.get("price_source", "") or ""),
        "in_inter":         bool(row.get("in_inter", False)),
        "sector":           str(row.get("sector", "") or ""),
        "patterns": {
            "pattern_type_label":    str(row.get("pattern_type_label", "없음") or "없음"),
            "pattern_summary":       str(row.get("패턴", "") or ""),
            "tv_ratio":              tv_ratio,
            "base_high_gap_pct":     gap_pct,
            "base_candle_day_offset": offset,
            "structure_broken_flag": False,
            "kim_hyungjun_flag":     bool(row.get("kim_hyungjun_flag", False)),
        },
        "indicators":    {},
        "supply":        None,
        "news":          None,
        "score":         None,
        "checklist":     None,
        "has_pattern":   str(row.get("pattern_type_label", "없음")) != "없음",
        "supply_ok":     False,
        "near_high_52w": False,
        "is_leading_sector": False,
        "prog_net_eok":  None,
        "regular_close_price": None,
        "regular_close_price_available": False,
        "kim_hyungjun_supply_ok": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-05-15")
    parser.add_argument("--snap", default="1750")
    args = parser.parse_args()

    sig_df  = _load_signals(args.date, args.snap)
    summary = _load_summary(args.date)

    candidates = [_to_candidate(row) for _, row in sig_df.iterrows()]

    market_regime = summary.get("market_regime", "중립")
    _max_map = {"강세": 5, "중립": 3, "약세": 2}
    max_n = _max_map.get(market_regime, 3)

    # signals CSV는 정렬 순서가 보존되어 있으므로 그대로 분리
    core_candidates  = candidates[:max_n]
    watch_candidates = candidates[max_n:]

    report_data = {
        "metadata": {
            "date":          args.date,
            "snapshot_time": args.snap,
            "run_time":      summary.get("run_time", args.date),
            "run_type":      summary.get("run_type", "2차"),
        },
        "market_summary": {
            "kospi_tv_eok":          summary.get("kospi_tv_eok", 0),
            "kosdaq_tv_eok":         summary.get("kosdaq_tv_eok", 0),
            "tv_1500_count":         0,
            "gainers_tv_1500_count": 0,
            "gainers_count":         0,
            "tv_count":              0,
            "intersection_count":    sum(1 for c in candidates if c["in_inter"]),
            "core_count":            len(core_candidates),
            "market_regime":         market_regime,
            "market_adl":            None,
            "market_subtype":        summary.get("market_subtype", ""),
            "market_type":           summary.get("market_type", ""),
            "kospi_level":           summary.get("kospi_level"),
            "kosdaq_level":          summary.get("kosdaq_level"),
            "kospi_chg":             summary.get("kospi_chg"),
            "kosdaq_chg":            summary.get("kosdaq_chg"),
            "limit_up_count":        summary.get("limit_up_count", 0),
            "limit_up_list":         [],
        },
        "core_candidates":         core_candidates,
        "watch_candidates":        watch_candidates,
        "rejected_candidates":     [],
        "kh_only_candidates":      [],
        "obs_candidates":          [],
        "gainers_top20":           [],
        "trading_value_top20":     [],
        "intersection_candidates": [],
        "leading_sectors":         [],
        "sector_calendar":         {},
        "review_results":          [],
        "cumulative_stats":        {},
        "kh_candidates_scope":     "top40_only",
    }

    out = REPORTS_DIR / f"preview_{args.date}_{args.snap}.html"
    generate_dashboard_html(report_data, out)
    print(f"생성: {out}")
    try:
        subprocess.Popen(["start", "", str(out)], shell=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
