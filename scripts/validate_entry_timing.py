# scripts/validate_entry_timing.py
"""기준봉(장대양봉) 당일 진입 vs 기준봉 이후 진입 — 어느 시점이 유리한가 (일회성).

가설: 점수 높은(급등·과열) 종목은 당일 종가가 이미 천장 근처라,
      기준봉 다음날 눌림/안정 후 진입이 더 유리할 수 있다.

신호일 종가 = 100 기준 (review.json은 신호일=기준봉 진입 가정).
  전략A 당일종가 진입  → D+k 고가 매도 수익 = dk_high_pct
  전략B 익일종가 진입  → D+k 고가 매도 수익 = (1+dkh/100)/(1+d1c/100) - 1
      (D+1 종가에 진입 → 같은 D+k 고가에 매도)
"""
import glob
import json
from statistics import mean


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


def _A(r, hk):
    """당일 종가 진입 → D+k 고가 매도 수익률."""
    return r.get(hk)


def _B(r, hk):
    """익일(D+1) 종가 진입 → D+k 고가 매도 수익률."""
    d1c = r.get("d1_close_pct")
    dkh = r.get(hk)
    if d1c is None or dkh is None:
        return None
    return ((1 + dkh / 100) / (1 + d1c / 100) - 1) * 100


def _winrate_A(r, hk):
    v = r.get(hk)
    return v if v is not None else None


def report(rows, label, out):
    rows = [r for r in rows if r.get("result") in ("성공", "실패")]
    if not rows:
        out.append(f"  {label:20s} | 표본 0")
        return
    for hk, klbl in [("d2_high_pct", "D+2 고가"), ("d3_high_pct", "D+3 고가")]:
        a = [_A(r, hk) for r in rows if _A(r, hk) is not None]
        b = [_B(r, hk) for r in rows if _B(r, hk) is not None]
        if not a or not b:
            continue
        # 익일 진입이 이긴 비율 (종목별 직접 비교)
        wins = 0
        cmp_n = 0
        for r in rows:
            av, bv = _A(r, hk), _B(r, hk)
            if av is not None and bv is not None:
                cmp_n += 1
                if bv > av:
                    wins += 1
        out.append(
            f"  {label:20s} {klbl} | 당일진입 {mean(a):+6.2f}% | "
            f"익일진입 {mean(b):+6.2f}% | 익일우위 {wins/cmp_n*100:4.1f}% (n={cmp_n})"
        )


def main():
    rows = load_reviews()
    out = []
    out.append("=" * 100)
    out.append("기준봉 당일 진입 vs 기준봉 익일(D+1 종가) 진입 — 진입 시점 비교 (5-6월/132신호)")
    out.append("매도 시점은 양 전략 동일(D+k 고가). '익일우위'=종목별로 익일진입이 더 나았던 비율.")
    out.append("=" * 100)

    out.append("\n[전체]")
    report(rows, "전체", out)

    out.append("\n[종합 점수 구간별] — 사용자 관찰: 고점수=저수익 → 익일진입 효과 큰가?")
    report([r for r in rows if (r.get("total_score") or 0) >= 13],         "13점 이상", out)
    report([r for r in rows if 10 <= (r.get("total_score") or 0) <= 12],   "10-12점", out)
    report([r for r in rows if 7 <= (r.get("total_score") or 0) <= 9],     "7-9점", out)

    out.append("\n[신호일 상승률 구간별] — 급등할수록 당일종가가 천장에 가까운가?")
    report([r for r in rows if (r.get("signal_change_pct") or 0) >= 20],          "당일 +20% 이상", out)
    report([r for r in rows if 10 <= (r.get("signal_change_pct") or 0) < 20],     "당일 +10-20%", out)
    report([r for r in rows if (r.get("signal_change_pct") or 0) < 10],           "당일 +10% 미만", out)

    out.append("\n[교집합 여부]")
    report([r for r in rows if r.get("in_inter")],     "교집합", out)
    report([r for r in rows if not r.get("in_inter")], "비교집합", out)

    text = "\n".join(out)
    open("data/validate_entry_timing_result.txt", "w", encoding="utf-8").write(text)
    print("저장: data/validate_entry_timing_result.txt")


if __name__ == "__main__":
    main()
