# scripts/validate_volatility_turnover.py
"""준돌 관점 검증: 당일 변동폭(고저 13%+) / 거래대금 회전율(시총 대비) (일회성).

가설:
  Q1. 신호일 당일 고가-저가 변동폭이 클수록(13%+) 승률이 높은가?
  Q2. 거래대금 ÷ 시가총액(회전율)이 높을수록 승률이 높은가?
데이터: review.json(승률·signal_tv·signal_price) + 신호종목 일봉(고저폭) + 상장주식수(현재값 근사).
회전율 = signal_tv / (signal_price × 상장주식수). 상장주식수는 과거 미저장 → 현재값 근사(거의 불변).
"""
import glob
import json
import time
from statistics import mean

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import REQUEST_DELAY
from scripts.fetch_stock_data import fetch_daily_history
from scripts.fetch_market_data import fetch_all_stocks


def load_reviews():
    rows, seen = [], set()
    for f in sorted(glob.glob("data/signals/2026-0[56]-*_review.json")):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for r in data:
            k = (r.get("code"), r.get("signal_date"))
            if k in seen or r.get("result") not in ("성공", "실패"):
                continue
            seen.add(k)
            rows.append(r)
    return rows


def winrate(rows):
    if not rows:
        return None, 0
    w = sum(1 for r in rows if r["result"] == "성공")
    return w / len(rows) * 100, len(rows)


def _avg(rows, key):
    v = [r.get(key) for r in rows if r.get(key) is not None]
    return mean(v) if v else None


def line(rows, label):
    wr, n = winrate(rows)
    if wr is None:
        return f"  {label:20s} | 표본 0"
    d1o = _avg(rows, "d1_open_pct")
    return f"  {label:20s} | 승률 {wr:5.1f}% (n={n:3d}) | D+1시가 {d1o:+5.2f}%"


def main():
    rows = load_reviews()
    print(f"판정 신호 {len(rows)}개 로드")

    # 상장주식수 맵 (현재값 근사)
    print("전종목 상장주식수 수집...")
    shares = {}
    for mname, mcode in [("KOSPI", 0), ("KOSDAQ", 1)]:
        df = fetch_all_stocks(mname, mcode)
        if not df.empty and "상장주식수" in df.columns:
            for _, r in df.iterrows():
                shares[str(r["종목코드"])] = float(r.get("상장주식수", 0) or 0)
    print(f"상장주식수 맵 {len(shares)}종목")

    # 종목별 신호일 일봉 → 고저폭 + 회전율
    print("신호종목 일봉 수집...")
    for i, r in enumerate(rows):
        code = r.get("code")
        sdate = (r.get("signal_date") or "").replace("-", ".")  # 2026.05.04
        try:
            df = fetch_daily_history(code, pages=4)
            row = df[df["date"] == sdate]
            if not row.empty:
                hi = float(row.iloc[0]["high"]); lo = float(row.iloc[0]["low"])
                if lo > 0:
                    r["_volatility"] = (hi - lo) / lo * 100
            sh = shares.get(str(code), 0)
            sp = float(r.get("signal_price") or 0)
            tv = float(r.get("signal_tv") or 0)
            if sh > 0 and sp > 0:
                r["_turnover"] = tv / (sp * sh) * 100  # 시총 대비 거래대금 %
        except Exception:
            pass
        time.sleep(REQUEST_DELAY)
        if (i + 1) % 30 == 0:
            print(f"  {i+1}/{len(rows)} ...")

    out = []
    out.append("=" * 86)
    out.append("준돌 관점 검증 — 당일 변동폭 / 거래대금 회전율 (5-6월 판정 신호)")
    out.append("변동폭=(고가-저가)/저가. 회전율=거래대금/시총(상장주식수 현재값 근사).")
    out.append("=" * 86)

    vol_rows = [r for r in rows if "_volatility" in r]
    out.append(f"\n[Q1] 당일 변동폭 구간별 승률 (변동폭 보유 {len(vol_rows)}개)")
    out.append(line([r for r in vol_rows if r["_volatility"] < 8],                  "8% 미만 (저변동)"))
    out.append(line([r for r in vol_rows if 8 <= r["_volatility"] < 13],            "8-13%"))
    out.append(line([r for r in vol_rows if 13 <= r["_volatility"] < 20],           "13-20% (준돌 기준+)"))
    out.append(line([r for r in vol_rows if r["_volatility"] >= 20],                "20% 이상 (고변동)"))
    out.append(line([r for r in vol_rows if r["_volatility"] >= 13],                "── 13% 이상 합계"))
    out.append(line([r for r in vol_rows if r["_volatility"] < 13],                 "── 13% 미만 합계"))

    to_rows = [r for r in rows if "_turnover" in r]
    out.append(f"\n[Q2] 거래대금 회전율(시총 대비) 구간별 승률 (회전율 보유 {len(to_rows)}개)")
    out.append(line([r for r in to_rows if r["_turnover"] < 3],                     "3% 미만 (저회전)"))
    out.append(line([r for r in to_rows if 3 <= r["_turnover"] < 10],               "3-10%"))
    out.append(line([r for r in to_rows if 10 <= r["_turnover"] < 25],              "10-25%"))
    out.append(line([r for r in to_rows if r["_turnover"] >= 25],                   "25% 이상 (고회전)"))

    # 교차: 변동폭 13%+ AND 회전율 10%+ (준돌 이상적 자리)
    both = [r for r in rows if r.get("_volatility", 0) >= 13 and r.get("_turnover", 0) >= 10]
    out.append(f"\n[교차] 변동폭 13%+ AND 회전율 10%+ (준돌 이상적 조건)")
    out.append(line(both, "둘 다 충족"))

    text = "\n".join(out)
    open("data/validate_voltno_result.txt", "w", encoding="utf-8").write(text)
    print("저장: data/validate_voltno_result.txt")


if __name__ == "__main__":
    main()
