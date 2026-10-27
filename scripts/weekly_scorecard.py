# scripts/weekly_scorecard.py
"""주간 점검표 — 내 판단(텔레그램 판·매수·무포)을 거래대금 1위 종목과 같은 조건으로 나란히 놓는다.

2026-10-28 사용자: "지금 내가 하고있는 종베의 workflow가 맞는건지 틀린건지 객관적으로 점검 할 수 있는 방법".
비교 기준은 거래대금 1위 — 봇 검증에서 국면과 상관없이 유효했던 것이 거래대금 순위뿐이라 이걸 이기는지가 출발점이다.
청산은 모두 같은 축: KRX 정규장 종가(15:35 저장분) → 다음 거래일 KRX 시가. 내 매수가가 있으면 매수가 → 다음 날 시가도 따로.
고수 3인 칸은 비워 둔다 — daily_view_log 정리 때 사람이 채운다(일지 문장을 기계로 판정하지 않는다).

로컬에서 주 1회:
    python -m scripts.weekly_scorecard --trades "../jongbe-data-backup/data/trades" --out "../jongbe-data-backup/매매일지분석/주간점검"
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

KRX_CLOSE_DIR = Path("data") / "krx_close"
TWIN = {"005930", "000660"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _close(day: str) -> dict:
    with open(KRX_CLOSE_DIR / f"{day}.csv", encoding="utf-8-sig") as f:
        return {r["종목코드"]: r for r in csv.DictReader(f)}


def _rows(store: Path, day: str) -> list[dict]:
    p = store / f"{day}.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _pct(a, b):
    return (b / a - 1) * 100 if a and b else None


def _fmt(v):
    return "-" if v is None else f"{v:+.2f}%"


def build(store: Path, days_n: int) -> str:
    from scripts import fetch_market_data as fmd
    from scripts.morning_briefing import _code_by_name
    from scripts.ranking import filter_excluded_stocks
    import pandas as pd

    raw = fmd.run()
    allr = pd.concat([raw[k] for k in ("KOSPI", "KOSDAQ") if k in raw])
    ex = filter_excluded_stocks(allr)
    universe = set(ex["종목코드"].astype(str))          # ETF·ETN 등 제외 — 같은 매매 가능 종목군에서 1위를 뽑는다
    names = dict(zip(allr["종목코드"].astype(str), allr["종목명"]))

    from scripts.market_calendar import get_next_trading_day
    files = sorted(p.stem for p in KRX_CLOSE_DIR.glob("*.csv"))
    # 저장이 빠진 날이 있으면 "다음 저장일"이 아니라 실제 다음 거래일만 짝짓는다(GPT 검토 10-28)
    pairs = [(a, b) for a, b in zip(files, files[1:])
             if get_next_trading_day(a.replace("-", "")) == b.replace("-", "")][-days_n:]
    lines = ["| 날짜 | 내 기록(그날 14:00부터 20:00까지 판·매수·무포) | 거래대금 1위 → 다음 날 시가 | 투탑 뺀 1위 → 다음 날 시가 | 내 종목 → 다음 날 시가 | 고수 3인 |",
             "|---|---|---|---|---|---|"]
    stat = {"days": 0, "recorded": 0, "mupo_base": [], "trade_base": [], "mine": []}
    for day, nxt in pairs:
        a, b = _close(day), _close(nxt)
        ranked = sorted(((c, (_f(r.get("거래대금")) or 0) + (_f(r.get("NXT거래대금")) or 0)) for c, r in a.items() if c in universe),
                        key=lambda t: -t[1])
        if not ranked or not ranked[0][1]:
            continue
        stat["days"] += 1

        def d1(code):
            return _pct(_f((a.get(code) or {}).get("현재가")), _f((b.get(code) or {}).get("시가")))

        top1 = ranked[0][0]
        top1x = next((c for c, _ in ranked if c not in TWIN), None)
        # 판단 창(14:00부터 20:00) 안의 기록 전부. 그날 매수가 하나라도 있으면 매수일, 없고 무포(또는 판 무포)면 무포일.
        recs = [r for r in _rows(store, day) if r.get("side") in ("판", "매수", "무포") and "14:00" <= r.get("time", "")[11:16] <= "20:00"]
        mine_txt, mine_ret = "기록 없음", "-"
        if recs:
            stat["recorded"] += 1
            parts = []
            for r in recs:
                tag = "연습 " if r.get("practice") else ""
                if r["side"] == "판":
                    parts.append(f"{tag}판 {r.get('판')}" + (f" / {r['이유'][:30]}" if r.get("이유") else ""))
                elif r["side"] == "무포":
                    parts.append(f"{tag}무포")
                else:
                    parts.append(f"{tag}매수 {r.get('name')}" + (f" 손절 {r['깨는가']:,.0f}" if r.get("깨는가") else " (손절가 없음)"))
            mine_txt = " → ".join(parts)
            buys = [x for x in recs if x["side"] == "매수" and x.get("name")]
            if buys:
                code = _code_by_name([buys[0]["name"]]).get(buys[0]["name"])
                close_ret = d1(code) if code else None
                entry_ret = _pct(buys[0].get("price"), _f((b.get(code) or {}).get("시가"))) if code else None
                mine_ret = f"종가 대비 {_fmt(close_ret)} · 매수가 대비 {_fmt(entry_ret)}"
                base = d1(top1)
                if close_ret is not None and base is not None:      # 같은 날 짝이 다 있을 때만 평균에 넣는다
                    stat["mine"].append(close_ret)
                    stat["trade_base"].append(base)
            elif any(x["side"] == "무포" or x.get("판") == "무포" for x in recs):
                stat["mupo_base"].append(d1(top1))
        lines.append(f"| {day[5:]} | {mine_txt} | {names.get(top1, top1)} {_fmt(d1(top1))} | "
                     f"{names.get(top1x, top1x)} {_fmt(d1(top1x))} | {mine_ret} | |")

    def avg(v):
        v = [x for x in v if x is not None]
        return f"{sum(v)/len(v):+.2f}% (n={len(v)})" if v else "없음"
    head = [f"# 주간 점검표 {pairs[0][0] if pairs else ''} 부터 {pairs[-1][0] if pairs else ''}",
            "",
            "청산 축: KRX 정규장 종가(15:35 저장분) → 다음 거래일 KRX 시가. 비용 제외. 같은 축으로만 비교한다.",
            "⚠ 거래대금 1위는 15:35 확정 순위로 고른 **사후 참고 기준**이다 — 판단 시각(15:00 전후)엔 순위가 달랐을 수 있다. 연습과 실전은 표에 '연습'으로 구분하고 평균엔 함께 넣는다.",
            "",
            f"- 기록한 날: {stat['recorded']} / {stat['days']}거래일",
            f"- 무포한 날의 거래대금 1위 다음 날 시가: {avg(stat['mupo_base'])} — 무포가 나쁜 날을 걸렀는지",
            f"- 매수한 날: 내 종목 {avg(stat['mine'])} vs 같은 날 거래대금 1위 {avg(stat['trade_base'])} — 종목 선별이 기준보다 나았는지",
            "- 실행(매수가 대비)은 표의 '매수가 대비'로 따로 본다. 표본이 작을 때는 방향만 보고 결론 내지 않는다.",
            ""]
    return "\n".join(head + lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, help="백업 레포 data/trades 폴더")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--out", default=None, help="저장 폴더(없으면 화면 출력)")
    a = ap.parse_args()
    text = build(Path(a.trades), a.days)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        files = sorted(p.stem for p in KRX_CLOSE_DIR.glob("*.csv"))
        p = out / f"{files[-2] if len(files) > 1 else 'x'}_주간점검.md"
        p.write_text(text, encoding="utf-8")
        print(p)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
