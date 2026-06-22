# scripts/validate_dolchim_5_6.py
"""돌팬티·침착해 관점으로 5-6월 실제 신호 종목 재검증 (일회성 분석).

review.json(결과) + signals.csv(수급·점수·교집합)를 조인해
관점별 승률 / 진입 타점별 평균 수익을 집계한다.

승률 정의: result == "성공" (pending 제외)
진입 타점 수익: 모두 "신호일 종가 진입" 기준
  - D+1 시초가 매도 = d1_open_pct
  - D+1 종가      = d1_close_pct
  - D+2 고가      = d2_high_pct
  - 최대 가능(천장) = mfe
"""
import csv
import glob
import json
from collections import defaultdict
from statistics import mean

# ── 1. review.json 수집 ──────────────────────────────────────
def load_reviews():
    rows, seen = [], set()
    for f in sorted(glob.glob("data/signals/2026-0[56]-*_review.json")):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for r in data:
            k = (r.get("code"), r.get("signal_date"))
            if k in seen:
                continue
            seen.add(k)
            rows.append(r)
    return rows


# ── 2. signals.csv 조인 (수급 라벨·5일 수급) ──────────────────
def load_signal_meta():
    """(code, date) → {supply_label, inst_5d, frgn_5d}"""
    meta = {}
    for f in sorted(glob.glob("data/signals/2026-0[56]-*_signals.csv")):
        date = f.split("\\")[-1].split("/")[-1][:10]
        try:
            with open(f, encoding="utf-8-sig") as fp:
                for row in csv.DictReader(fp):
                    code = (row.get("종목코드") or "").strip().zfill(6)
                    if not code:
                        continue
                    def _f(key):
                        try:
                            return float(row.get(key) or 0)
                        except ValueError:
                            return 0.0
                    meta[(code, date)] = {
                        "supply_label": (row.get("supply_label") or "").strip(),
                        "inst_5d": _f("inst_net_5d"),
                        "frgn_5d": _f("foreign_net_5d"),
                    }
        except Exception:
            continue
    return meta


def winrate(rows):
    decided = [r for r in rows if r.get("result") in ("성공", "실패")]
    if not decided:
        return None, 0
    w = sum(1 for r in decided if r["result"] == "성공")
    return w / len(decided) * 100, len(decided)


def avg_field(rows, key):
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return mean(vals) if vals else None


def fmt(rows, label):
    wr, n = winrate(rows)
    if wr is None:
        return f"  {label:24s} | 표본 0"
    d1o = avg_field(rows, "d1_open_pct")
    d1c = avg_field(rows, "d1_close_pct")
    mfe = avg_field(rows, "mfe")
    mae = avg_field(rows, "mae")
    return (f"  {label:24s} | 승률 {wr:5.1f}% (n={n:3d}) | "
            f"D+1시가 {d1o:+5.2f}% | D+1종가 {d1c:+6.2f}% | "
            f"천장 {mfe:+5.2f}% | 바닥 {mae:+6.2f}%")


def main():
    rows = load_reviews()
    meta = load_signal_meta()
    for r in rows:
        m = meta.get((r.get("code"), r.get("signal_date")), {})
        r["_supply_label"] = m.get("supply_label", "")
        r["_inst_5d"] = m.get("inst_5d", 0)
        r["_frgn_5d"] = m.get("frgn_5d", 0)

    out = []
    out.append("=" * 100)
    out.append("돌팬티·침착해 관점 5-6월 신호 재검증 (132 신호 / 29거래일 / 2026-05-04~06-12)")
    out.append("수익은 모두 '신호일 종가 진입' 기준. 승률 = result 성공 비율(진행중 제외).")
    out.append("=" * 100)

    out.append("\n[0] 베이스라인 — 전체")
    out.append(fmt(rows, "전체"))

    out.append("\n[1] 교집합 여부 (상승률+거래대금 동시 상위)")
    out.append(fmt([r for r in rows if r.get("in_inter")],       "교집합"))
    out.append(fmt([r for r in rows if not r.get("in_inter")],   "비교집합"))

    out.append("\n[2] 종합 점수 구간")
    out.append(fmt([r for r in rows if (r.get("total_score") or 0) >= 13],            "13점 이상 (강한 후보)"))
    out.append(fmt([r for r in rows if 10 <= (r.get("total_score") or 0) <= 12],      "10-12점 (일반)"))
    out.append(fmt([r for r in rows if 7 <= (r.get("total_score") or 0) <= 9],        "7-9점 (소액)"))
    out.append(fmt([r for r in rows if (r.get("total_score") or 0) < 7],              "7점 미만"))

    out.append("\n[3] 거래대금 구간 (침착해: 1조+ 선호)")
    out.append(fmt([r for r in rows if (r.get("signal_tv") or 0) >= 1e13],                    "1조 이상"))
    out.append(fmt([r for r in rows if 5e12 <= (r.get("signal_tv") or 0) < 1e13],             "5천억-1조"))
    out.append(fmt([r for r in rows if 1.5e11 <= (r.get("signal_tv") or 0) < 5e12],           "1500억-5천억"))

    out.append("\n[4] 수급 라벨 (★ = 5일 누적+비율 강조)")
    out.append(fmt([r for r in rows if r["_supply_label"].startswith("★")],          "★ 강조 수급"))
    out.append(fmt([r for r in rows if "쌍매수" in r["_supply_label"]],              "쌍매수 (기관+외인)"))
    out.append(fmt([r for r in rows if r["_supply_label"] in ("기관매수", "★기관매수")], "기관매수"))
    out.append(fmt([r for r in rows if r["_supply_label"] in ("외인매수", "★외인매수")], "외인매수"))
    out.append(fmt([r for r in rows if r["_supply_label"] in ("혼조", "")],          "혼조/없음"))

    out.append("\n[5] 5일 수급 연속성 근사 (침착해: 수급 연속성 핵심)")
    out.append(fmt([r for r in rows if r["_inst_5d"] > 0 and r["_frgn_5d"] > 0],      "5일 양매수 (기관+외인>0)"))
    out.append(fmt([r for r in rows if not (r["_inst_5d"] > 0 and r["_frgn_5d"] > 0)], "그 외"))

    out.append("\n[6] 패턴 타입")
    for pt in ("당일돌파형", "재돌파형", "고가수축형", "고가횡보형"):
        out.append(fmt([r for r in rows if r.get("pattern_type") == pt], pt))

    out.append("\n[7] 진입 타점 분석 — 종가 진입 후 어디서 파는 게 최적인가 (전체)")
    decided = [r for r in rows if r.get("result") in ("성공", "실패")]
    for key, lbl in [("d1_open_pct", "D+1 시초가"), ("d1_high_pct", "D+1 고가"),
                     ("d1_close_pct", "D+1 종가"), ("d2_high_pct", "D+2 고가"),
                     ("d2_close_pct", "D+2 종가"), ("d3_close_pct", "D+3 종가"),
                     ("d5_close_pct", "D+5 종가")]:
        v = avg_field(decided, key)
        pos = sum(1 for r in decided if (r.get(key) or 0) > 0)
        tot = sum(1 for r in decided if r.get(key) is not None)
        out.append(f"  {lbl:12s} 평균 {v:+6.2f}% | 양봉비율 {pos/tot*100:4.1f}% ({pos}/{tot})" if v is not None else f"  {lbl}: 데이터 없음")

    out.append("\n[8] 천장(MFE) 도달 시점 분포 — '언제 최고가인가'")
    mfe_days = defaultdict(int)
    for r in decided:
        if r.get("mfe_day"):
            mfe_days[r["mfe_day"]] += 1
    for d in ("D+1", "D+2", "D+3", "D+5"):
        if mfe_days.get(d):
            out.append(f"  {d}: {mfe_days[d]}건 ({mfe_days[d]/len(decided)*100:.0f}%)")

    text = "\n".join(out)
    open("data/validate_5_6_result.txt", "w", encoding="utf-8").write(text)
    print("저장: data/validate_5_6_result.txt")


if __name__ == "__main__":
    main()
