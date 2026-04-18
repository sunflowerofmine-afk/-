# scripts/regenerate_dashboard.py
"""
기존 CSV 데이터에서 새 레이아웃 대시보드 재생성
Usage: python -m scripts.regenerate_dashboard 2026-04-17
"""

import sys
import re
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import RAW_DIR, PROCESSED_DIR, SIGNALS_DIR, REPORTS_DIR, MIN_TRADING_VALUE_EOK
from scripts.dashboard import generate_dashboard_html, generate_index_html


def _load_csv(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    return pd.DataFrame()


def run(date_str: str):
    MIN_TV_WON = MIN_TRADING_VALUE_EOK * 100_000_000

    # 해당 날짜의 타임스탬프 목록 수집
    raw_files = list(RAW_DIR.glob(f"{date_str}_*.csv"))
    timestamps = sorted(set(
        m.group(1)
        for f in raw_files
        if (m := re.match(r"(\d{4}-\d{2}-\d{2}_\d{4})", f.name))
    ))

    if not timestamps:
        print(f"[오류] {date_str} 날짜의 데이터 파일을 찾을 수 없습니다.")
        print(f"  확인 경로: {RAW_DIR}")
        return

    for ts in timestamps:
        print(f"\n[처리 중] {ts}")
        snapshot_time = ts.split("_")[1]
        run_type = {"1450": "1차", "1750": "2차"}.get(snapshot_time, "수동")

        # 시장 요약
        raw_kospi  = _load_csv(RAW_DIR / f"{ts}_KOSPI.csv")
        raw_kosdaq = _load_csv(RAW_DIR / f"{ts}_KOSDAQ.csv")
        kospi_tv  = raw_kospi["거래대금"].sum()  / 1e8 if not raw_kospi.empty  and "거래대금" in raw_kospi.columns  else 0
        kosdaq_tv = raw_kosdaq["거래대금"].sum() / 1e8 if not raw_kosdaq.empty and "거래대금" in raw_kosdaq.columns else 0
        all_raw   = pd.concat([raw_kospi, raw_kosdaq], ignore_index=True)
        tv_1500_count = int((all_raw["거래대금"] >= MIN_TV_WON).sum()) if not all_raw.empty and "거래대금" in all_raw.columns else 0

        # processed CSV
        gainers_df = _load_csv(PROCESSED_DIR / f"{ts}_top_gainers.csv")
        tv_df      = _load_csv(PROCESSED_DIR / f"{ts}_top_tv.csv")
        inter_df   = _load_csv(PROCESSED_DIR / f"{ts}_intersection.csv")

        gainers_tv_1500 = int((gainers_df["거래대금"] >= MIN_TV_WON).sum()) if not gainers_df.empty and "거래대금" in gainers_df.columns else 0
        inter_codes     = set(inter_df["종목코드"].astype(str)) if not inter_df.empty and "종목코드" in inter_df.columns else set()

        # signals CSV → 핵심 후보
        sig_files = sorted(SIGNALS_DIR.glob(f"{ts}*.csv"))
        core_candidates = []
        if sig_files:
            sig_df = _load_csv(sig_files[0])
            for _, row in sig_df.iterrows():
                code    = str(row.get("종목코드", ""))
                pattern = str(row.get("패턴", "없음")) or "없음"

                class _Score:
                    total_score = row.get("total_score", 0)

                core_candidates.append({
                    "name":          row.get("종목명", ""),
                    "code":          code,
                    "market":        row.get("시장", ""),
                    "change_pct":    float(row.get("등락률", 0)),
                    "trading_value": float(row.get("거래대금", 0)),
                    "indicators":    {},
                    "patterns":      {"pattern_summary": pattern},
                    "supply":        None,
                    "news":          None,
                    "score":         _Score(),
                    "checklist":     None,
                    "in_inter":      code in inter_codes,
                    "has_pattern":   pattern not in ("없음", "", "nan"),
                    "supply_ok":     False,
                })

        report_data = {
            "metadata": {
                "date":          date_str,
                "snapshot_time": snapshot_time,
                "run_time":      f"{date_str} {snapshot_time[:2]}:{snapshot_time[2:]}",
                "run_type":      run_type,
            },
            "market_summary": {
                "kospi_tv_eok":          kospi_tv,
                "kosdaq_tv_eok":         kosdaq_tv,
                "tv_1500_count":         tv_1500_count,
                "gainers_tv_1500_count": gainers_tv_1500,
                "gainers_count":         len(gainers_df),
                "tv_count":              len(tv_df),
                "intersection_count":    len(inter_df),
                "core_count":            len(core_candidates),
            },
            "gainers_top20":           gainers_df.to_dict("records") if not gainers_df.empty else [],
            "trading_value_top20":     tv_df.to_dict("records")      if not tv_df.empty      else [],
            "intersection_candidates": inter_df.to_dict("records")   if not inter_df.empty   else [],
            "core_candidates":         core_candidates,
            "rejected_candidates":     [],
        }

        out_path    = REPORTS_DIR / f"{ts}.html"
        latest_name = f"latest_{snapshot_time}.html" if run_type in ("1차", "2차") else "latest.html"
        ok = generate_dashboard_html(report_data, out_path, REPORTS_DIR / latest_name)
        print(f"  → {'성공' if ok else '실패'}: {out_path.name}")

    generate_index_html(REPORTS_DIR)
    print("\n인덱스 갱신 완료")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m scripts.regenerate_dashboard 2026-04-17")
        sys.exit(1)
    run(sys.argv[1])
