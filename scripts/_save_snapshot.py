"""성과 스냅샷 저장 스크립트 — python -m scripts._save_snapshot"""
import json
from pathlib import Path
from collections import defaultdict
import pandas as pd
import sys

SIGNALS_DIR = Path("data/signals")

# 복기 JSON 로드
reviews = []
for p in sorted(SIGNALS_DIR.glob("*_review.json")):
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            reviews.extend(data)
    except Exception as e:
        print(f"로드 실패: {p} — {e}")

measured = [r for r in reviews if r.get("result") in ("성공", "실패")]

def avg(lst):
    return round(sum(lst) / len(lst), 2) if lst else None

def wr(lst):
    return round(sum(1 for v in lst if v > 0) / len(lst) * 100, 1) if lst else None

def win_pct(grp):
    n = len(grp)
    w = sum(1 for r in grp if r["result"] == "성공")
    return w, n, round(w / n * 100, 1) if n else 0

# 수급 라벨 조인
sup_map = {}
sig_rows = []
for p in sorted(SIGNALS_DIR.glob("*_1750_signals.csv")):
    try:
        df = pd.read_csv(p, dtype={"종목코드": str})
        df["_date"] = p.stem[:10]
        sig_rows.append(df)
    except Exception:
        pass

if sig_rows:
    sdf = pd.concat(sig_rows, ignore_index=True)
    code_col = next((c for c in sdf.columns if "코드" in c or c == "code"), None)
    sup_col  = next((c for c in sdf.columns if "supply" in c.lower() or "수급" in c), None)
    if code_col and sup_col:
        for _, row in sdf.iterrows():
            key = (row["_date"], str(row[code_col]).zfill(6))
            sup_map[key] = row[sup_col]

# 날짜 범위
dates = sorted(r["signal_date"] for r in measured if r.get("signal_date"))
date_from = dates[0] if dates else "?"
date_to   = dates[-1] if dates else "?"

lines = []
lines.append("# 종베 시스템 성과 스냅샷")
lines.append("분석일: 2026-05-21")
lines.append(f"복기 기간: {date_from} ~ {date_to}")
lines.append(f"복기 완료: {len(measured)}건  (전체 {len(reviews)}건)")
lines.append("")

# 전체 요약
wins = sum(1 for r in measured if r["result"] == "성공")
d1o = [r["d1_open_pct"] for r in measured if r.get("d1_open_pct") is not None]
d1c = [r["d1_close_pct"] for r in measured if r.get("d1_close_pct") is not None]
d3c = [r["d3_close_pct"] for r in measured if r.get("d3_close_pct") is not None]
d5c = [r["d5_close_pct"] for r in measured if r.get("d5_close_pct") is not None]
mfe = [r["mfe"] for r in measured if r.get("mfe") is not None]
mae = [r["mae"] for r in measured if r.get("mae") is not None]
gap = [r["gap_pct"] for r in measured if r.get("gap_pct") is not None]

lines.append("## 전체 요약")
lines.append(f"전체 승률:      {wins}/{len(measured)} = {wins/len(measured)*100:.1f}%  (기준: 익일 시가 양갭)")
lines.append(f"갭 평균:        {avg(gap):+.2f}%  (양갭 비율: {wr(gap):.1f}%)")
lines.append(f"D+1 시가 평균:  {avg(d1o):+.2f}%  (승: {wr(d1o):.1f}%)")
lines.append(f"D+1 종가 평균:  {avg(d1c):+.2f}%  (승: {wr(d1c):.1f}%)")
lines.append(f"D+3 종가 평균:  {avg(d3c):+.2f}%  (승: {wr(d3c):.1f}%, n={len(d3c)})")
lines.append(f"D+5 종가 평균:  {avg(d5c):+.2f}%  (승: {wr(d5c):.1f}%, n={len(d5c)})")
lines.append(f"MFE 평균:       {avg(mfe):+.2f}%")
lines.append(f"MAE 평균:       {avg(mae):+.2f}%")
lines.append("")

# 패턴별
lines.append("## 패턴별 승률")
pat_groups = defaultdict(list)
for r in measured:
    pat_groups[r.get("pattern_type") or "없음"].append(r)
for pat, grp in sorted(pat_groups.items(), key=lambda x: -len(x[1])):
    w, n, pct = win_pct(grp)
    d1o_g = [r["d1_open_pct"] for r in grp if r.get("d1_open_pct") is not None]
    d1c_g = [r["d1_close_pct"] for r in grp if r.get("d1_close_pct") is not None]
    lines.append(f"{pat}: {w}/{n} = {pct}%  D+1시가 {avg(d1o_g):+.2f}%  D+1종가 {avg(d1c_g):+.2f}%")
lines.append("")

# 스코어 구간별
lines.append("## 스코어 구간별 승률")
score_bands = [("7~9점", 7, 9), ("10~12점", 10, 12), ("13+점", 13, 99)]
for label, lo, hi in score_bands:
    grp = [r for r in measured if lo <= (r.get("total_score") or 0) <= hi]
    if not grp:
        continue
    w, n, pct = win_pct(grp)
    d1o_g = [r["d1_open_pct"] for r in grp if r.get("d1_open_pct") is not None]
    d1c_g = [r["d1_close_pct"] for r in grp if r.get("d1_close_pct") is not None]
    mfe_g = [r["mfe"] for r in grp if r.get("mfe") is not None]
    mae_g = [r["mae"] for r in grp if r.get("mae") is not None]
    lines.append(
        f"{label}: {w}/{n} = {pct}%"
        f"  D+1시가 {avg(d1o_g):+.2f}%"
        f"  D+1종가 {avg(d1c_g):+.2f}%"
        f"  MFE {avg(mfe_g):+.1f}%"
        f"  MAE {avg(mae_g):+.1f}%"
    )
lines.append("")

# 당일 상승률 구간별
lines.append("## 당일 상승률 구간별 승률")
chg_bands = [("+10~15%", 10, 15), ("+15~20%", 15, 20), ("+20~25%", 20, 25), ("+25~30%", 25, 30)]
for label, lo, hi in chg_bands:
    grp = [r for r in measured if r.get("signal_change_pct") is not None and lo <= r["signal_change_pct"] < hi]
    if not grp:
        continue
    w, n, pct = win_pct(grp)
    d1o_g = [r["d1_open_pct"] for r in grp if r.get("d1_open_pct") is not None]
    mfe_g = [r["mfe"] for r in grp if r.get("mfe") is not None]
    mae_g = [r["mae"] for r in grp if r.get("mae") is not None]
    lines.append(
        f"{label}: {w}/{n} = {pct}%"
        f"  D+1시가 {avg(d1o_g):+.2f}%"
        f"  MFE {avg(mfe_g):+.1f}%"
        f"  MAE {avg(mae_g):+.1f}%"
    )
lines.append("")

# 교집합 여부별
lines.append("## 교집합 여부별 승률")
inter  = [r for r in measured if r.get("in_inter")]
ninter = [r for r in measured if not r.get("in_inter")]
w_i, n_i, p_i = win_pct(inter)
w_n, n_n, p_n = win_pct(ninter)
d1o_i = [r["d1_open_pct"] for r in inter  if r.get("d1_open_pct") is not None]
d1o_n = [r["d1_open_pct"] for r in ninter if r.get("d1_open_pct") is not None]
lines.append(f"교집합({n_i}건):   {w_i}/{n_i} = {p_i}%  D+1시가 {avg(d1o_i):+.2f}%")
lines.append(f"비교집합({n_n}건): {w_n}/{n_n} = {p_n}%  D+1시가 {avg(d1o_n):+.2f}%")
lines.append("")

# 교집합 x 스코어 교차
lines.append("## 교집합 x 스코어 교차")
for inter_flag, ilabel in [(True, "교집합"), (False, "비교집합")]:
    for band_label, lo, hi in [("고스코어(10+)", 10, 99), ("저스코어(~9)", 0, 9)]:
        grp = [
            r for r in measured
            if bool(r.get("in_inter")) == inter_flag
            and lo <= (r.get("total_score") or 0) <= hi
        ]
        if not grp:
            continue
        w, n, pct = win_pct(grp)
        d1o_g = [r["d1_open_pct"] for r in grp if r.get("d1_open_pct") is not None]
        lines.append(f"{ilabel} x {band_label}: {w}/{n} = {pct}%  D+1시가 {avg(d1o_g):+.2f}%")
lines.append("")

# 수급 라벨별
if sup_map:
    lines.append("## 수급 라벨별 승률")
    sup_groups = defaultdict(list)
    for r in measured:
        key = (r.get("signal_date", ""), str(r.get("code", "")).zfill(6))
        sup = sup_map.get(key)
        if sup and str(sup) != "nan":
            sup_groups[sup].append(r)
    for sup, grp in sorted(sup_groups.items(), key=lambda x: -len(x[1])):
        w, n, pct = win_pct(grp)
        d1o_g = [r["d1_open_pct"] for r in grp if r.get("d1_open_pct") is not None]
        lines.append(f"{sup}: {w}/{n} = {pct}%  D+1시가 {avg(d1o_g):+.2f}%")
    lines.append("")

# 날짜별
lines.append("## 날짜별 성과")
date_groups = defaultdict(list)
for r in measured:
    date_groups[r.get("signal_date", "?")].append(r)
for d, grp in sorted(date_groups.items()):
    w, n, pct = win_pct(grp)
    g_vals = [r["gap_pct"] for r in grp if r.get("gap_pct") is not None]
    lines.append(f"{d}: {w}/{n} = {pct:.0f}%  갭평균 {avg(g_vals):+.1f}%")
lines.append("")

# 결과 유형
lines.append("## 결과 유형 분포")
frt = defaultdict(int)
for r in measured:
    frt[r.get("final_result_type") or "?"] += 1
for typ, cnt in sorted(frt.items(), key=lambda x: -x[1]):
    lines.append(f"{typ}: {cnt}건 ({cnt/len(measured)*100:.1f}%)")

# 저장
out = "\n".join(lines)
out_path = SIGNALS_DIR / "snapshot_2026-05-21.txt"
out_path.write_text(out, encoding="utf-8")
print(f"저장 완료: {out_path}  ({len(lines)}줄)")
