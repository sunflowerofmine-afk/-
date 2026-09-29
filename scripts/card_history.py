# scripts/card_history.py
"""시황 카드에 붙는 흐름 자료 — collect()가 만든 dict에 키를 더한다(2026-09-30 사용자 요청).

  twin_hist  투탑 최근 5거래일 거래대금(KRX·NXT, 억). 과거 날은 15:35 저장 파일(`data/krx_close/`)의
             `거래대금`(정규장)과 `NXT거래대금`(15:35까지 누적), 오늘은 지금 값. 14:20이면 오늘 막대는 진행 중.
  mkt_hist   코스피·코스닥 최근 5거래일 시장 거래대금(주식만, 억). 기록 파일(`data/market_history.csv`)에서
             14:20 슬롯은 과거 날도 그날 14:20 행 — 같은 시각끼리 비교한다. 그 외엔 15:35 행(정규장 마감).
  저녁 상위 10 행에 nxt_day_eok(NXT 15:30 이전) · krx_day_eok(KRX 하루 = 정규장 + 15:35 이후)를 붙인다 —
             하루 시간 순서 막대(NXT 낮 → KRX → NXT 저녁)용.

실패하면 키를 비워 둔다. 카드는 빈 키를 "-"로 그린다.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

KRX_CLOSE_DIR = Path("data") / "krx_close"
TWIN_CODES = ("005930", "000660")
DAYS = 5


def _label(ds: str) -> str:
    return f"{ds[5:7]}/{ds[8:10]}"


def _twin_hist(d: dict, today_s: str, partial: bool) -> dict:
    past = sorted(p.stem for p in KRX_CLOSE_DIR.glob("*.csv") if p.stem < today_s)[-(DAYS - 1):]
    out = {c: [] for c in TWIN_CODES}
    for ds in past:
        try:
            df = pd.read_csv(KRX_CLOSE_DIR / f"{ds}.csv", dtype={"종목코드": str}, encoding="utf-8-sig")
            df = df.set_index("종목코드")
        except Exception as e:
            logger.warning(f"투탑 흐름: {ds} 파일 읽기 실패 {e}")
            df = pd.DataFrame()
        for c in TWIN_CODES:
            krx = nxt = None
            if c in df.index:
                krx = float(df.at[c, "거래대금"]) / 1e8 if pd.notna(df.at[c, "거래대금"]) else None
                if "NXT거래대금" in df.columns and pd.notna(df.at[c, "NXT거래대금"]):
                    nxt = float(df.at[c, "NXT거래대금"]) / 1e8
            out[c].append({"label": _label(ds), "krx": krx, "nxt": nxt, "partial": False})
    rows = {r["code"]: r for r in (d.get("day_rows") or d.get("day_top") or [])}
    for c in TWIN_CODES:
        r = rows.get(c) or {}
        out[c].append({"label": _label(today_s), "krx": r.get("krx_eok"), "nxt": r.get("nxt_eok"), "partial": partial})
    return out


def _mkt_hist(d: dict, today_s: str) -> tuple[dict, str]:
    from scripts import market_history as mh
    slot = "1420" if d.get("slot") == "1420" else "1535"
    rows = [r for r in mh.load() if r.get("slot") == slot and r.get("date", "") < today_s]
    by_date = {}
    for r in rows:                      # 같은 날 같은 슬롯이 둘이면 뒤의 것
        by_date[r["date"]] = r
    past = sorted(by_date)[-(DAYS - 1):]
    out = {}
    for k in ("kospi", "kosdaq"):
        seq = [{"label": _label(ds), "tv": mh._f(by_date[ds].get(f"{k}_idx_tv_eok")), "partial": False} for ds in past]
        seq.append({"label": _label(today_s), "tv": d.get(f"{k}_idx_tv_eok"), "partial": False})
        out[k] = seq
    basis = "같은 시각(14:20)끼리 비교" if slot == "1420" else "정규장 마감 기준"
    return out, basis


def _evening_rows(d: dict) -> None:
    live = d.get("krx_live_tv_eok") or {}
    for x in d.get("nxt_top") or []:
        eve = x.get("nxt_eve_eok") or 0.0
        x["nxt_day_eok"] = max((x.get("tv_eok") or 0.0) - eve, 0.0)
        x["krx_day_eok"] = live.get(x.get("code"))


def attach(d: dict) -> dict:
    now: datetime = d["now"]
    today_s = now.date().isoformat()
    if now.strftime("%H%M") >= "1600":
        try:
            _evening_rows(d)
        except Exception as e:
            logger.warning(f"저녁 막대 자료 실패: {e}")
        return d
    try:
        d["twin_hist"] = _twin_hist(d, today_s, partial=d.get("slot") == "1420")
    except Exception as e:
        logger.warning(f"투탑 흐름 자료 실패: {e}")
    try:
        d["mkt_hist"], d["mkt_hist_basis"] = _mkt_hist(d, today_s)
    except Exception as e:
        logger.warning(f"시장 거래대금 흐름 자료 실패: {e}")
    return d
