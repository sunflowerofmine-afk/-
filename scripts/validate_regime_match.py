# scripts/validate_regime_match.py
"""시장 국면 × 종목 특성 교차 검증 (일회성 분석).

질문: 어떤 시장 국면에서 어떤 종목 특성이 가장 잘 맞는가?
국면(코스닥 지수 기준):
  강세 = 종가 > 5일선 AND 5일선 상승추세
  약세 = 종가 < 5일선 AND 5일선 하락추세
  혼조 = 그 외
각 국면 안에서 종목 특성별(점수/교집합/수급/거래대금) 승률·타점 집계.
"""
import glob
import json
import re
from statistics import mean

import requests
from bs4 import BeautifulSoup
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT

INDEX_URL = "https://finance.naver.com/sise/sise_index_day.naver"


# ── 지수 일봉 수집 (네이버) ──────────────────────────────────
def fetch_index_daily(code: str, pages: int = 6) -> dict:
    """code: KOSPI/KOSDAQ. {YYYY-MM-DD: 종가} 반환."""
    out = {}
    for p in range(1, pages + 1):
        try:
            r = requests.get(f"{INDEX_URL}?code={code}&page={p}",
                             headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.encoding = "euc-kr"
            s = BeautifulSoup(r.text, "lxml")
            for tr in s.select("table.type_1 tr"):
                td = tr.select("td")
                if len(td) < 2:
                    continue
                d = td[0].text.strip()
                c = td[1].text.strip().replace(",", "")
                if re.match(r"\d{4}\.\d{2}\.\d{2}", d) and c:
                    out[d.replace(".", "-")] = float(c)
        except Exception:
            continue
    return out


def build_regime_map(idx: dict) -> dict:
    """{date: 종가} → {date: regime_label}. 5일선·추세 계산."""
    dates = sorted(idx.keys())
    closes = [idx[d] for d in dates]
    regime = {}
    for i, d in enumerate(dates):
        if i < 7:
            continue
        ma5      = mean(closes[i-4:i+1])
        ma5_prev = mean(closes[i-7:i-2])  # 3거래일 전 5일선
        above = closes[i] > ma5
        rising = ma5 > ma5_prev
        if above and rising:
            regime[d] = "강세"
        elif (not above) and (not rising):
            regime[d] = "약세"
        else:
            regime[d] = "혼조"
    return regime


# ── review 로드 ──────────────────────────────────────────────
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


def winrate(rows):
    dec = [r for r in rows if r.get("result") in ("성공", "실패")]
    if not dec:
        return None, 0
    return sum(1 for r in dec if r["result"] == "성공") / len(dec) * 100, len(dec)


def _avg(rows, key):
    v = [r.get(key) for r in rows if r.get(key) is not None]
    return mean(v) if v else None


def line(rows, label):
    wr, n = winrate(rows)
    if wr is None:
        return f"    {label:22s} | 표본 0"
    d1o = _avg(rows, "d1_open_pct"); d1h = _avg(rows, "d1_high_pct")
    return f"    {label:22s} | 승률 {wr:5.1f}% (n={n:3d}) | D+1시가 {d1o:+5.2f}% | D+1고가 {d1h:+5.2f}%"


def main():
    kosdaq = fetch_index_daily("KOSDAQ")
    kospi  = fetch_index_daily("KOSPI")
    regime = build_regime_map(kosdaq)
    rows = load_reviews()
    for r in rows:
        r["_regime"] = regime.get(r.get("signal_date"), "?")

    out = []
    out.append("=" * 96)
    out.append("시장 국면(코스닥 5일선·추세) × 종목 특성 교차 검증 — 5-6월")
    out.append("강세=종가>5일선&5일선상승 / 약세=종가<5일선&5일선하락 / 혼조=그외")
    out.append("=" * 96)

    # 국면별 분포
    out.append("\n[국면별 신호 분포 및 베이스라인]")
    for g in ("강세", "혼조", "약세"):
        grp = [r for r in rows if r["_regime"] == g]
        out.append(line(grp, f"{g} 전체"))

    # 국면 × 종목 특성
    for g in ("강세", "혼조", "약세"):
        grp = [r for r in rows if r["_regime"] == g]
        wr, n = winrate(grp)
        if not grp:
            continue
        out.append(f"\n[{g} 국면 — 어떤 종목이 맞는가] (전체 승률 {wr:.1f}%, n={n})")
        out.append(line([r for r in grp if r.get("in_inter")],                     "교집합"))
        out.append(line([r for r in grp if not r.get("in_inter")],                 "비교집합"))
        out.append(line([r for r in grp if (r.get("total_score") or 0) >= 13],     "13점 이상"))
        out.append(line([r for r in grp if 10 <= (r.get("total_score") or 0) <= 12], "10-12점"))
        out.append(line([r for r in grp if 7 <= (r.get("total_score") or 0) <= 9], "7-9점"))
        out.append(line([r for r in grp if (r.get("signal_tv") or 0) >= 5e12],     "거래대금 5천억+"))
        out.append(line([r for r in grp if (r.get("signal_change_pct") or 0) >= 20], "당일 +20%↑(급등)"))
        out.append(line([r for r in grp if (r.get("signal_change_pct") or 0) < 20],  "당일 +20% 미만"))

    # 코스피도 같이 라벨해 교차 (보조)
    kospi_reg = build_regime_map(kospi)
    out.append("\n[코스피·코스닥 동시 국면 — 양 지수 모두 강세 vs 엇갈림]")
    both_bull = [r for r in rows if r["_regime"] == "강세" and kospi_reg.get(r.get("signal_date")) == "강세"]
    mixed     = [r for r in rows if not (r["_regime"] == "강세" and kospi_reg.get(r.get("signal_date")) == "강세")]
    out.append(line(both_bull, "코스피+코스닥 강세"))
    out.append(line(mixed,     "그 외(엇갈림/약세)"))

    text = "\n".join(out)
    open("data/validate_regime_result.txt", "w", encoding="utf-8").write(text)
    print("저장: data/validate_regime_result.txt")
    print(f"코스닥 국면 라벨 {len(regime)}일 | 신호 매칭 {sum(1 for r in rows if r['_regime']!='?')}/{len(rows)}")


if __name__ == "__main__":
    main()
