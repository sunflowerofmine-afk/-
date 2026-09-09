"""선별력·게이트·손절 감사 — 봇이 실제로 무엇을 하고 있는지 데이터로 되묻는다.

2026-09-09 로직 점검에서 나온 세 구멍에 대응한다. 셋 다 "코드가 틀렸다"가 아니라
**되짚을 방법이 없다**는 문제였고, 그래서 사람이 매번 손으로 조인해야 했다.

  A. 게이트 등급이 성과 순서를 만드는가
     실측(2026-09-09, n=51): 관찰만 58.6% > 소액만 42.9%, "종가베팅 허용"은 21일간 0회.
     즉 4단계인데 실질 2단계이고 그 2단계가 뒤집혀 있었다. 표본이 얇아 확정은 못 한다.
     `gate_grade`는 2026-08-11부터만 쌓이므로 시간이 답을 준다 — 그때 다시 돌린다.

  B. 손절을 넣으면 성과가 어떻게 보이는가
     `review.is_win`은 갭의 부호만 본다. -20%와 -0.1%가 같은 "실패 1건"이다.
     사용자 규칙은 금액이다(감당 손절 50만/1회 · 일일 -50만 · 운용 -200만).
     비율 승률이 아니라 **그 선을 넘은 빈도**가 계속할 수 있는지를 가른다.

  C. 봇이 버린 종목은 어떻게 됐는가
     지금까지 성과는 봇이 **고른 것만** 쟀다. "고른 것 중 승률"은 알아도
     "고를 수 있었던 것 대비 우위"는 원리적으로 알 수 없었다.
     2026-09-09부터 `daily_summary.rejected_candidates`에 탈락분을 남긴다.
     C는 그 데이터가 쌓여야 답이 나온다 — 그전에는 "표본 없음"을 정직하게 출력한다.

    python -m scripts.audit_selection                    # 백업 레포에서 읽음
    python -m scripts.audit_selection --data-dir <경로>  # 다른 위치 지정
"""

from __future__ import annotations

import argparse
import collections
import glob
import io
import json
import os
import re
import statistics as st
import sys

_DEFAULT_DATA = r"C:\Users\purpl\Jongbe Project\jongbe-data-backup\data\signals"
_WINDOW_START = "2026-04-27"     # 분석 구간 시작(그 이전은 저장 형식이 다르다)


def _load(data_dir: str):
    """복기 결과와 일별 요약을 날짜로 묶어 돌려준다."""
    reviews, summaries = [], {}
    for f in sorted(glob.glob(os.path.join(data_dir, "*_review.json"))):
        d = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f)).group(1)
        if d < _WINDOW_START:
            continue
        try:
            for r in json.load(io.open(f, encoding="utf-8")):
                r["_date"] = d
                reviews.append(r)
        except Exception as e:
            print(f"  ⚠ 읽기 실패 {f}: {e}")
    for f in sorted(glob.glob(os.path.join(data_dir, "daily_summary_*.json"))):
        d = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f)).group(1)
        try:
            summaries[d] = json.load(io.open(f, encoding="utf-8"))
        except Exception:
            pass
    return reviews, summaries


def _stat_line(label: str, pcts: list[float], width: int = 16) -> str:
    if not pcts:
        return f"  {label:<{width}} 표본 없음"
    win = sum(1 for x in pcts if x > 0) / len(pcts) * 100
    return (f"  {label:<{width}} n={len(pcts):>3}  양수비율 {win:5.1f}%  "
            f"평균 {sum(pcts)/len(pcts):+6.2f}%  중앙값 {st.median(pcts):+6.2f}%")


def audit_gate(reviews, summaries) -> None:
    """A. 게이트 등급 ↔ 성과 순서."""
    print("=" * 78)
    print("A. 게이트 등급이 성과 순서를 만드는가")
    print("=" * 78)
    order = ["매매 금지", "관찰만", "소액만", "종가베팅 허용"]
    by = collections.defaultdict(list)
    for r in reviews:
        g = (summaries.get(r["_date"]) or {}).get("gate_grade")
        p = r.get("d1_open_pct")
        if g and p is not None:
            by[g].append(p)
    if not by:
        print("  게이트 기록이 있는 날의 신호가 없다. `gate_grade`는 2026-08-11부터 쌓인다.")
        return
    days = sum(1 for d, s in summaries.items() if s.get("gate_grade"))
    print(f"  게이트 기록 {days}일 · 매칭 신호 {sum(len(v) for v in by.values())}건\n")
    for g in order:
        print(_stat_line(g, by.get(g, [])))
    seen = [g for g in order if by.get(g)]
    if len(seen) >= 2:
        rates = [sum(1 for x in by[g] if x > 0) / len(by[g]) for g in seen]
        if rates != sorted(rates):
            print("\n  ★ 등급 순서와 성과 순서가 일치하지 않는다.")
            print("    게이트는 예측 장치가 아니라 사용자 본인의 제한 정책이다")
            print("    (user_trading_philosophy). 다만 예측처럼 읽히게 표시하면 안 된다.")
    missing = [g for g in order if not by.get(g)]
    if missing:
        print(f"\n  ⚠ 한 번도 나오지 않은 등급: {', '.join(missing)}")


def audit_stop(reviews) -> None:
    """B. 손절 기준을 넣었을 때."""
    from config.settings import (CAMPAIGN_STOP_KRW, DAILY_STOP_KRW,
                                 PAIN_STOP_KRW, POSITION_KRW_NORMAL)
    print("\n" + "=" * 78)
    print("B. 손절을 넣으면 — 비율이 아니라 금액으로")
    print("=" * 78)
    rows = [r for r in reviews if r.get("d1_open_pct") is not None]
    if not rows:
        print("  표본 없음")
        return
    pos = POSITION_KRW_NORMAL
    print(f"  기준: 1종목 {pos//10000:,}만원 · 감당 손절 {PAIN_STOP_KRW//10000}만원/회 "
          f"· 일일 중단 {DAILY_STOP_KRW//10000}만원 · 운용 중단 {CAMPAIGN_STOP_KRW//10000}만원\n")
    pnl = [int(round(pos * r["d1_open_pct"] / 100)) for r in rows]
    pain = [x for x in pnl if x <= -PAIN_STOP_KRW]
    print(f"  신호 {len(rows)}건 · 합계 {sum(pnl):+,}원 · 건당 평균 {sum(pnl)//len(pnl):+,}원")
    print(f"  감당 손절({PAIN_STOP_KRW//10000}만원) 초과: {len(pain)}건 "
          f"({len(pain)/len(pnl)*100:.1f}%) · 그 합계 {sum(pain):+,}원")
    if pain:
        print(f"  최악 1건 {min(pnl):+,}원")

    by_day = collections.defaultdict(int)
    for r, v in zip(rows, pnl):
        by_day[r["_date"]] += v
    daily_out = [d for d, v in by_day.items() if v <= -DAILY_STOP_KRW]
    print(f"\n  하루 전량 진입 가정 시 일일 중단선 도달: {len(daily_out)}일 / {len(by_day)}일")
    if daily_out:
        worst = min(by_day.items(), key=lambda x: x[1])
        print(f"    최악의 날 {worst[0]} {worst[1]:+,}원")
    print("  ⚠ 하루 여러 신호를 전부 샀다는 가정이다. 실제로는 1종목만 산다 —")
    print("    상한이 아니라 '그날 후보 전체를 담았다면' 값으로 읽을 것.")


def audit_rejected(reviews, summaries, data_dir: str) -> None:
    """C. 고른 것 vs 버린 것."""
    print("\n" + "=" * 78)
    print("C. 봇이 버린 종목은 어떻게 됐는가 (선별력)")
    print("=" * 78)
    days = [d for d, s in summaries.items() if s.get("rejected_candidates")]
    if not days:
        print("  탈락 종목 기록이 아직 없다.")
        print("  `daily_summary.rejected_candidates`는 2026-09-09부터 저장한다.")
        print("  → 며칠 쌓인 뒤 `scripts.review`에 탈락분 D+1 측정을 붙이면 비교가 된다.")
        print("\n  ★ 그전까지는 '봇의 선별에 실력이 있는가'를 원리적으로 답할 수 없다.")
        print("    지금 수치(양수비율 48.1%)는 '고른 것 중'이지 '고를 수 있었던 것 대비'가 아니다.")
        return
    n_rej = sum(len(summaries[d]["rejected_candidates"]) for d in days)
    print(f"  탈락 기록 {len(days)}일 · {n_rej}건 (측정은 별도 — review에 붙인 뒤 이 절이 채워진다)")
    reasons = collections.Counter(
        r.get("reason", "?").split(" (")[0]
        for d in days for r in summaries[d]["rejected_candidates"])
    print("\n  탈락 사유 분포:")
    for k, v in reasons.most_common():
        print(f"    {k:<24} {v:>4}건")


def main() -> int:
    ap = argparse.ArgumentParser(description="선별력·게이트·손절 감사")
    ap.add_argument("--data-dir", default=_DEFAULT_DATA, help="signals 디렉터리")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not os.path.isdir(args.data_dir):
        print(f"경로 없음: {args.data_dir}")
        return 1
    reviews, summaries = _load(args.data_dir)
    print(f"복기 {len(reviews)}건 · 일별요약 {len(summaries)}일 ({_WINDOW_START} 이후)\n")
    audit_gate(reviews, summaries)
    audit_stop(reviews)
    audit_rejected(reviews, summaries, args.data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
