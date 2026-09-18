# scripts/backtest_evening_entry.py
"""정규장 종가 진입 vs 저녁(NXT 20:00 마지막가) 진입 — D+1 시가 기준 비교.

돌팬티 4강 1.6절이 시간외 매매 재개 조건으로 말한 "시간외에서 오른 종목이 다음 날 시초가에서
몇 % 떴는지 지속적으로 체크"를 봇이 쌓아 둔 데이터로 한다(2026-09-18 첫 실행, 지피티 제안 검토용).

자료(전부 비공개 백업 레포 data/):
  signals/{날짜}_1750_signals.csv   그날 최종 후보 · regular_close_price(정규장 종가)
  nxt/{날짜}_nxt.csv                NXT 20:00 마지막가(거래량 상위 100/시장). 자정 뒤 저장돼 하루 밀렸던 파일은
                                    2026-09-18에 실제 날짜로 정정했다(백업 레포 eb8933a)
  signals/{날짜}_review.json        D+1 시가(t1_open)

⚠ 표본은 봇 후보(당일 +10% 안팎 강한 마감형이 대부분)이고 개편 전(KRX 애프터마켓 없음) NXT 값이다.
사용자가 고른 종목이 아니다. 사용:  python -m scripts.backtest_evening_entry [--backup ../jongbe-data-backup]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

def load(backup: Path) -> pd.DataFrame:
    data = backup / "data"
    rows = []
    for d in sorted(p.name[:10] for p in (data / "nxt").glob("*_nxt.csv")):
        sp, rp, xp = data / "signals" / f"{d}_1750_signals.csv", data / "signals" / f"{d}_review.json", data / "nxt" / f"{d}_nxt.csv"
        if not (sp.exists() and rp.exists() and xp.exists()):
            continue
        s = pd.read_csv(sp, dtype={"종목코드": str}, encoding="utf-8-sig")
        x = pd.read_csv(xp, dtype={"종목코드": str}, encoding="utf-8-sig").set_index("종목코드")
        rv = {str(r["code"]).zfill(6): r for r in json.loads(rp.read_text(encoding="utf-8"))}
        for _, r in s.iterrows():
            c = str(r["종목코드"]).zfill(6)
            v = rv.get(c)
            if v is None or not v.get("t1_open") or c not in x.index:
                continue
            pc = r.get("regular_close_price")
            pc = float(pc) if pd.notna(pc) and pc > 0 else float(r["signal_price"])
            pn = x.at[c, "nxt_price"]
            if not (pd.notna(pn) and pn > 0):
                continue
            rows.append({"date": d, "name": r["종목명"], "tv": float(r["거래대금"]), "chg": float(r["등락률"]),
                         "p_close": pc, "p_nxt": float(pn), "t1_open": float(v["t1_open"])})
    m = pd.DataFrame(rows)
    m["gap"] = (m.t1_open / m.p_close - 1) * 100        # 종가 진입 → D+1 시가
    m["prem"] = (m.p_nxt / m.p_close - 1) * 100         # 저녁 마지막가의 종가 대비
    m["gap_nxt"] = (m.t1_open / m.p_nxt - 1) * 100      # 20:00 진입 → D+1 시가
    return m


def _line(x: pd.DataFrame, label: str) -> str:
    if x.empty:
        return f"{label}: 표본 없음"
    return (f"{label:20s} n={len(x):3d} | 종가진입→D+1시가 평균 {x.gap.mean():+.2f}% 중앙 {x.gap.median():+.2f}% 승률 {(x.gap > 0).mean() * 100:.0f}%"
            f" | 20:00진입→D+1시가 평균 {x.gap_nxt.mean():+.2f}% 중앙 {x.gap_nxt.median():+.2f}% 승률 {(x.gap_nxt > 0).mean() * 100:.0f}%"
            f" | 저녁 프리미엄 평균 {x.prem.mean():+.2f}%")


def report(m: pd.DataFrame) -> str:
    out = [f"표본 {len(m)} 종목-일 · {m.date.nunique()}일 · {m.date.min()}부터 {m.date.max()}",
           _line(m, "전체"), _line(m[m.tv >= 1.5e11], "거래대금 1,500억+"),
           _line(m.sort_values("tv", ascending=False).groupby("date").head(1), "날짜별 거래대금 1위"),
           _line(m[m.prem > 1], "저녁 +1% 초과"), _line(m[(m.prem <= 1) & (m.prem >= -1)], "저녁 ±1% 이내"), _line(m[m.prem < -1], "저녁 -1% 미만"),
           f"상관 — 저녁 프리미엄 vs 종가진입 D+1시가 r={m.prem.corr(m.gap):+.2f} / vs 20:00진입 D+1시가 r={m.prem.corr(m.gap_nxt):+.2f}"]
    for lab, s in (("저녁 +1% 초과", m[m.prem > 1]), ("저녁 -1% 미만", m[m.prem < -1])):
        if not s.empty:
            out.append(f"{lab} {len(s)}건 중 D+1 시가가 저녁가 위 {(s.gap_nxt > 0).mean() * 100:.0f}% · 종가 위 {(s.gap > 0).mean() * 100:.0f}%")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", default=str(Path(__file__).resolve().parents[2] / "jongbe-data-backup"))
    ap.add_argument("--csv", help="종목-일 표를 저장할 경로")
    a = ap.parse_args()
    df = load(Path(a.backup))
    print(report(df))
    if a.csv:
        df.to_csv(a.csv, index=False, encoding="utf-8-sig")
