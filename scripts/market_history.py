# scripts/market_history.py
"""시황 누적 기록 — 거래대금 "전일 대비 · 20거래일 평균 대비" 비교의 원자료.

한 실행에 한 행을 `data/market_history.csv`에 붙인다(워크플로가 커밋). 슬롯은 실행의
명목 시각(1420 / 1535 / 1750 / 1930)이고, 그 외 시각은 실제 HHMM을 쓴다.

비교 규칙
- 코스피·코스닥 거래대금은 15:30 이후 실행이면 확정치라 슬롯과 무관하게 같은 값이다.
  → "전일 확정" = 전 거래일의 가장 늦은 마감 후 행, "20일 평균" = 그런 행 20개 평균.
- 14:20(마감 전)은 부분 누적이라 확정치와 비교하면 안 된다.
  → "전일 같은 시각" = 전 거래일 1420 행, 거기에 "전일 하루치 대비 진행률"을 덧붙인다.
- NXT 거래대금은 20:00까지 계속 쌓이므로 항상 **같은 슬롯끼리** 비교한다.

⚠ 2026-09-11부터 거래대금이 API의 실제 누적치다(그 전은 현재가×거래량 근사). 몇 % 안쪽의
   단절이 있다. 09-11 이전 행은 백업 레포의 daily_summary에서 옮겨 심은 확정치다.
"""
from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

HISTORY_PATH = DATA_DIR / "market_history.csv"
FIELDS = ["date", "slot", "time", "kospi_tv_eok", "kosdaq_tv_eok", "nxt_tv_eok",
          "adl_pct", "limit_up", "top5_pct", "kospi_chg", "kosdaq_chg"]
CLOSED_SLOTS = ("1535", "1750", "1930", "확정")   # 마감 후 = 확정 거래대금


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def append(row: dict) -> None:
    """같은 (date, slot) 행이 있으면 덮어쓴다(재실행 대비)."""
    rows = [r for r in load() if not (r.get("date") == row["date"] and r.get("slot") == row["slot"])]
    rows.append({k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS})
    rows.sort(key=lambda r: (r["date"], r["time"] or ""))
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _closed_by_date(rows: list[dict], before: str) -> list[tuple[str, dict]]:
    """날짜별 마감 후 행 하나(가장 늦은 시각). before 날짜 미만만, 날짜 오름차순."""
    best: dict[str, dict] = {}
    for r in rows:
        if r.get("date", "") >= before or r.get("slot") not in CLOSED_SLOTS:
            continue
        d = r["date"]
        if d not in best or (r.get("time") or "") > (best[d].get("time") or ""):
            best[d] = r
    return sorted(best.items())


def _pct(cur: float | None, base: float | None) -> float | None:
    if cur is None or not base:
        return None
    return round((cur / base - 1) * 100, 1)


def compare(today: str, slot: str, kospi: float | None, kosdaq: float | None,
            nxt: float | None) -> dict:
    """반환 키: kospi_vs_prev / kospi_vs_avg20 / kosdaq_… / nxt_vs_prev / progress_kospi / progress_kosdaq / n_avg
    값이 없으면 None. 1420 슬롯은 prev·avg20이 '전일 1420'·'1420 행 평균'이고 progress가 붙는다."""
    rows = load()
    out: dict = {"n_avg": 0}
    closed = _closed_by_date(rows, before=today)

    if slot in CLOSED_SLOTS:
        prev = closed[-1][1] if closed else None
        recent = [r for _, r in closed[-20:]]
        out["kospi_vs_prev"]  = _pct(kospi,  _f(prev and prev.get("kospi_tv_eok")))
        out["kosdaq_vs_prev"] = _pct(kosdaq, _f(prev and prev.get("kosdaq_tv_eok")))
        ks = [_f(r.get("kospi_tv_eok")) for r in recent if _f(r.get("kospi_tv_eok"))]
        kd = [_f(r.get("kosdaq_tv_eok")) for r in recent if _f(r.get("kosdaq_tv_eok"))]
        out["kospi_vs_avg20"]  = _pct(kospi,  sum(ks) / len(ks)) if ks else None
        out["kosdaq_vs_avg20"] = _pct(kosdaq, sum(kd) / len(kd)) if kd else None
        out["n_avg"] = len(ks)
    else:
        same = [r for r in rows if r.get("slot") == slot and r.get("date", "") < today]
        same.sort(key=lambda r: r["date"])
        prev = same[-1] if same else None
        recent = same[-20:]
        out["kospi_vs_prev"]  = _pct(kospi,  _f(prev and prev.get("kospi_tv_eok")))
        out["kosdaq_vs_prev"] = _pct(kosdaq, _f(prev and prev.get("kosdaq_tv_eok")))
        ks = [_f(r.get("kospi_tv_eok")) for r in recent if _f(r.get("kospi_tv_eok"))]
        kd = [_f(r.get("kosdaq_tv_eok")) for r in recent if _f(r.get("kosdaq_tv_eok"))]
        out["kospi_vs_avg20"]  = _pct(kospi,  sum(ks) / len(ks)) if ks else None
        out["kosdaq_vs_avg20"] = _pct(kosdaq, sum(kd) / len(kd)) if kd else None
        out["n_avg"] = len(ks)
        # 전일 하루치 대비 진행률 — 마감 전 값이 하루치의 몇 %인가
        last_closed = closed[-1][1] if closed else None
        pk = _f(last_closed and last_closed.get("kospi_tv_eok"))
        pd_ = _f(last_closed and last_closed.get("kosdaq_tv_eok"))
        out["progress_kospi"]  = round(kospi / pk * 100)  if (kospi and pk) else None
        out["progress_kosdaq"] = round(kosdaq / pd_ * 100) if (kosdaq and pd_) else None

    # NXT: 같은 슬롯끼리
    same_nxt = [r for r in rows if r.get("slot") == slot and r.get("date", "") < today and _f(r.get("nxt_tv_eok"))]
    same_nxt.sort(key=lambda r: r["date"])
    out["nxt_vs_prev"] = _pct(nxt, _f(same_nxt[-1].get("nxt_tv_eok"))) if same_nxt else None
    return out
