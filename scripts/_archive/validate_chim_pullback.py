# scripts/validate_chim_pullback.py
"""침착해 "재료 살아있으면 D+1 눌림에 추가매수" 전략 검증 (일회성 분석).

검증 질문:
  Q1. D+1에 눌린 종목이 D+2/D+3에 회복하는가?
  Q2. 재료·수급이 강한 종목일수록 눌림 회복률이 높은가? (침착해의 전제)
  Q3. 종가 100% 진입 vs 종가50%+D+1눌림50% 추매 — 어느 쪽이 유리한가?

데이터: review.json (D+1~D+3 고가/종가, 눌림생존·구조붕괴 판정).
'눌림' 정의: D+1 종가가 신호일 종가보다 낮음 (d1_close_pct < 0).
추매 시뮬: 신호일 종가 50% + D+1 종가 50% → 평단 기준 D+2 고가 매도.
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


def _avg(rows, key):
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return mean(vals) if vals else None


def _recover_rate(rows, key, thresh=0.0):
    """key 값이 thresh 초과인 비율 (회복 비율)."""
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return None, 0
    rec = sum(1 for v in vals if v > thresh)
    return rec / len(vals) * 100, len(vals)


def _addbuy_pnl(r):
    """종가50%+D+1종가50% 추매 후 D+2 고가 매도 수익률(%).
    신호일 종가=100 기준. D+1 종가가격=100*(1+d1c/100). 평단=둘의 평균.
    D+2 고가가격=100*(1+d2h/100). 수익=(D2고가/평단-1)*100."""
    d1c = r.get("d1_close_pct")
    d2h = r.get("d2_high_pct")
    if d1c is None or d2h is None:
        return None
    p_d1 = 100 * (1 + d1c / 100)
    avg  = (100 + p_d1) / 2
    p_d2 = 100 * (1 + d2h / 100)
    return (p_d2 / avg - 1) * 100


def _single_pnl_d2(r):
    """종가 100% 진입 → D+2 고가 매도 수익률(%) = d2_high_pct."""
    return r.get("d2_high_pct")


def main():
    rows = load_reviews()
    out = []
    out.append("=" * 96)
    out.append("침착해 '재료 살아있으면 D+1 눌림 추가매수' 검증 (5-6월 / 132신호)")
    out.append("눌림 = D+1 종가가 신호일 종가보다 낮은 종목 (d1_close_pct < 0)")
    out.append("=" * 96)

    decided = [r for r in rows if r.get("result") in ("성공", "실패")]
    pull = [r for r in decided if (r.get("d1_close_pct") or 0) < 0]
    out.append(f"\n판정 종목 {len(decided)}개 중 D+1 눌림 종목 {len(pull)}개 "
               f"({len(pull)/len(decided)*100:.0f}%)")

    # ── Q1. D+1 눌림 종목의 D+2/D+3 회복 ─────────────────────
    out.append("\n[Q1] D+1 눌림 종목이 이후 회복하는가? (눌림 종목 기준)")
    for key, lbl in [("d2_high_pct", "D+2 고가"), ("d2_close_pct", "D+2 종가"),
                     ("d3_high_pct", "D+3 고가"), ("d3_close_pct", "D+3 종가")]:
        rate, n = _recover_rate(pull, key)
        avg = _avg(pull, key)
        if rate is not None:
            out.append(f"  {lbl:9s} | 플러스 회복 {rate:5.1f}% (n={n:3d}) | 평균 {avg:+6.2f}%")

    # ── Q2. 재료 강도별 눌림 회복률 ──────────────────────────
    out.append("\n[Q2] 재료·수급 강한 눌림 종목일수록 회복 잘 되는가? (D+2 고가 플러스 회복률)")

    def _grp(cond, label):
        g = [r for r in pull if cond(r)]
        rate, n = _recover_rate(g, "d2_high_pct")
        avg = _avg(g, "d2_high_pct")
        addbuy = [_addbuy_pnl(r) for r in g if _addbuy_pnl(r) is not None]
        ab = mean(addbuy) if addbuy else None
        if rate is None:
            out.append(f"  {label:26s} | 표본 0")
        else:
            ab_s = f"{ab:+6.2f}%" if ab is not None else "  -  "
            out.append(f"  {label:26s} | 회복 {rate:5.1f}% (n={n:2d}) | D+2고가 {avg:+6.2f}% | 추매수익 {ab_s}")

    _grp(lambda r: (r.get("signal_tv") or 0) >= 5e12,   "거래대금 5천억+ (강)")
    _grp(lambda r: (r.get("signal_tv") or 0) < 5e12,    "거래대금 5천억 미만")
    _grp(lambda r: r.get("in_inter"),                    "교집합 (강)")
    _grp(lambda r: not r.get("in_inter"),                "비교집합")
    _grp(lambda r: (r.get("total_score") or 0) >= 10,    "10점 이상 (강)")
    _grp(lambda r: (r.get("total_score") or 0) < 10,     "10점 미만")
    _grp(lambda r: r.get("alive_pullback") is True,      "눌림생존 판정 (봇)")
    _grp(lambda r: r.get("failed_structure") is True,    "구조붕괴 판정 (봇)")

    # ── Q3. 단순 진입 vs 눌림 추매 (눌림 종목 전체) ───────────
    out.append("\n[Q3] 종가 진입 후 전략 비교 — D+1 눌림 종목 전체 기준")
    single = [_single_pnl_d2(r) for r in pull if _single_pnl_d2(r) is not None]
    addbuy = [_addbuy_pnl(r)   for r in pull if _addbuy_pnl(r)   is not None]
    out.append(f"  종가 100% 진입 → D+2 고가 매도   : 평균 {mean(single):+6.2f}% (n={len(single)})")
    out.append(f"  종가50%+D+1눌림50% → D+2 고가 매도: 평균 {mean(addbuy):+6.2f}% (n={len(addbuy)})")
    out.append(f"  → 추매로 평단을 낮춘 효과: {mean(addbuy)-mean(single):+.2f}%p")

    # 구조 살아있는 눌림만 (failed_structure != True)
    alive = [r for r in pull if not r.get("failed_structure")]
    a_single = [_single_pnl_d2(r) for r in alive if _single_pnl_d2(r) is not None]
    a_addbuy = [_addbuy_pnl(r)   for r in alive if _addbuy_pnl(r)   is not None]
    if a_single and a_addbuy:
        out.append(f"\n  [구조 살아있는 눌림만 — 침착해 조건 ' 재료 살아있으면']")
        out.append(f"  종가 100% 진입 → D+2 고가       : 평균 {mean(a_single):+6.2f}% (n={len(a_single)})")
        out.append(f"  종가50%+D+1눌림50% → D+2 고가    : 평균 {mean(a_addbuy):+6.2f}% (n={len(a_addbuy)})")

    # 구조 붕괴 눌림 (failed_structure == True) — 추매 금지 대상
    broke = [r for r in pull if r.get("failed_structure") is True]
    if broke:
        b_add = [_addbuy_pnl(r) for r in broke if _addbuy_pnl(r) is not None]
        if b_add:
            out.append(f"\n  [구조 붕괴 눌림 — 추매 금지 대상 검증]")
            out.append(f"  종가50%+D+1눌림50% → D+2 고가    : 평균 {mean(b_add):+6.2f}% (n={len(b_add)})")

    text = "\n".join(out)
    open("data/validate_chim_pullback_result.txt", "w", encoding="utf-8").write(text)
    print("저장: data/validate_chim_pullback_result.txt")


if __name__ == "__main__":
    main()
