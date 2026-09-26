# scripts/save_sim_daily.py
"""종베 시뮬레이터 재료 — 분봉·뉴스를 매일 저장 (2026-09-27 사용자 결정 "매일저장 추가").

왜: 네이버 분봉(fchart minute)은 최근 6거래일만 준다(2026-09-27 실측: 09-16 09:00부터 09-23 19:59).
저장하지 않으면 장 후반 돈의 흐름을 과거 날짜로는 영영 복원할 수 없다. 뉴스는 과거로 넘겨 받을 수
있지만 기사가 많은 종목은 금방 밀려난다(삼성전자 100쪽 = 열흘 전, 300쪽은 거절).

언제: nxt_save.yml(20:10 KST, 애프터마켓 마감 뒤). 비공개 백업 레포를 clone한 뒤 SIM_DIR로 넘긴다.
실행이 빠진 날은 다음 실행이 6거래일 창 안에서 채운다.

종목(날짜별): 15:35 스냅샷(`data/sim_snapshot/`, market_brief.save_sim_snapshot)의 거래대금 상위 40 +
상승률 상위 15 + 상한가 + 그날 봇 후보 + 삼성전자·SK하이닉스. 스냅샷이 없으면 15:35 정규장 파일의 거래대금 상위 40.

저장 (SIM_DIR 아래):
  minute/YYYY-MM-DD.csv.gz   종목코드·시각·가격·누적거래량. 09:00부터 19:59(애프터마켓 포함).
                             15:20부터 15:29는 동시호가라 행이 없고 15:30 행에 종가 체결이 들어 있다. NXT 08시대는 없다.
  news/YYYY-MM-DD.json       그날 종목과 고른 이유 + 기사(시각·언론사·제목) — 직전 거래일 15:30부터 그날 20:10까지
                             + 종목별 최근 5거래일 기사 수(각 날 15:30 기준 창) — 돌팬티 "재료 신선도: 이슈가 며칠째인지"
  snapshot/YYYY-MM-DD.json   15:35 스냅샷 사본
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT
from scripts.market_calendar import get_now_kst, is_trading_day
from scripts.naver_api import get_json

logger = logging.getLogger(__name__)

START = date(2026, 9, 28)      # 자동 저장 첫날. 09-16부터 09-23은 백업 레포 `시뮬레이터_자료/분봉_0916-0923/`에 따로 받아 둠
WINDOW = 6                     # fchart 분봉이 주는 거래일 수
FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"
NEWS_URL = "https://m.stock.naver.com/api/news/stock/{code}"
NEWS_MAX_PAGES = 40            # 투탑은 기사가 많아 15쪽(300건)으로는 전일 15:30까지 못 내려갔다(2026-09-27 시험). 300쪽은 거절
FRESH_DAYS = 5                 # 기사 수를 셀 거래일 수(재료가 며칠째인지). 투탑은 40쪽 안에 다 못 내려가면 부분값
TWO_TOP = ("005930", "000660")
SNAP_DIR = Path("data") / "sim_snapshot"
KRX_CLOSE_DIR = Path("data") / "krx_close"


def sim_dir() -> Path:
    return Path(os.getenv("SIM_DIR", "data/sim"))


def _prev_trading_day(d: date) -> date:
    p = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(p):
            return p
        p -= timedelta(days=1)
    return p


def target_days(now: datetime, start: date = START) -> list[date]:
    """분봉 창(최근 6거래일) 안의 거래일 중 start 이후. 오늘은 20:00 뒤에만(애프터마켓까지 다 찬 뒤)."""
    d = now.date()
    if not (is_trading_day(d) and now.strftime("%H%M") >= "2000"):
        d = _prev_trading_day(d)
    days = [d]
    while len(days) < WINDOW:
        days.append(_prev_trading_day(days[-1]))
    return sorted(x for x in days if x >= start)


def codes_for(day: date) -> dict[str, list[str]]:
    """그날 저장할 종목 → 고른 이유."""
    why: dict[str, list[str]] = {}

    def add(codes, tag: str) -> None:
        for c in codes:
            c = str(c).zfill(6)
            why.setdefault(c, [])
            if tag not in why[c]:
                why[c].append(tag)

    snap = SNAP_DIR / f"{day.isoformat()}.json"
    if snap.exists():
        s = json.loads(snap.read_text(encoding="utf-8"))
        add([x["code"] for x in s.get("top40") or []], "거래대금상위40")
        add([x["code"] for x in s.get("gainers15") or []], "상승률상위15")
        add([x["code"] for x in s.get("limit_ups") or []], "상한가")
    else:
        p = KRX_CLOSE_DIR / f"{day.isoformat()}.csv"
        if p.exists():
            k = pd.read_csv(p, dtype={"종목코드": str}, encoding="utf-8-sig")
            if "거래대금" in k.columns:
                add(k.nlargest(40, "거래대금")["종목코드"], "거래대금상위40(정규장 파일, ETF 포함)")
    for f in glob.glob(str(Path(os.getenv("SIGNALS_DIR", "data/signals")) / f"{day.isoformat()}_*_signals.csv")):
        try:
            add(pd.read_csv(f, dtype={"종목코드": str}, encoding="utf-8-sig")["종목코드"].dropna(), "봇후보")
        except Exception as e:
            logger.warning(f"후보 파일 읽기 실패 {f}: {e}")
    add(TWO_TOP, "투탑")
    return why


def fetch_minute(code: str) -> pd.DataFrame:
    """최근 6거래일 분봉. 값은 그 분 마지막 체결가와 그 시각까지의 누적 거래량."""
    r = requests.get(FCHART_URL, params={"symbol": code, "timeframe": "minute", "count": 10000, "requestType": 0},
                     headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    rows = []
    for item in re.findall(r'<item data="([^"]+)"', r.text):
        p = item.split("|")
        if len(p) >= 6 and re.match(r"\d{12}$", p[0]):
            rows.append((p[0], p[4], p[5]))
    return pd.DataFrame(rows, columns=["시각", "가격", "누적거래량"])


def fetch_news(code: str, since: str) -> tuple[list[dict], bool]:
    """since(YYYYMMDDHHMM) 이후 기사와 "since까지 다 내려갔는가". 1쪽(최신)부터 넘기다
    since보다 오래된 기사가 나오면 멈춘다. 쪽 상한·오류로 멈추면 False(부분값)."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for page in range(1, NEWS_MAX_PAGES + 1):
        try:
            groups = get_json(NEWS_URL.format(code=code), {"pageSize": 20, "page": page})
        except Exception as e:
            logger.warning(f"[{code}] 뉴스 {page}쪽 실패: {e}")
            return out, False
        items = [it for g in (groups or []) for it in (g.get("items") or [])]
        if not items:
            return out, True           # 끝까지 다 봤다(기사가 적은 종목)
        for it in items:
            dt = str(it.get("datetime") or "")
            title = str(it.get("titleFull") or it.get("title") or "").strip()
            if dt >= since and title and (dt, title) not in seen:
                seen.add((dt, title))
                out.append({"code": code, "time": dt, "office": it.get("officeName"), "title": title})
        if min(str(it.get("datetime") or "") for it in items) < since:
            return out, True
        time.sleep(0.2)
    return out, False


def run(now: datetime | None = None, start: date = START) -> list[date]:
    now = now or get_now_kst()
    out = sim_dir()
    days = [d for d in target_days(now, start)
            if not ((out / "minute" / f"{d}.csv.gz").exists() and (out / "news" / f"{d}.json").exists())]
    if not days:
        logger.info("저장할 날 없음")
        return []
    plan = {d: codes_for(d) for d in days}
    codes = sorted({c for why in plan.values() for c in why})
    first = min(days)
    for _ in range(FRESH_DAYS):
        first = _prev_trading_day(first)
    since = first.strftime("%Y%m%d") + "1530"
    logger.info(f"대상 {', '.join(map(str, days))} · 종목 {len(codes)} · 기사 {since} 이후")
    minute: dict[str, pd.DataFrame] = {}
    news: dict[str, list[dict]] = {}
    complete: dict[str, bool] = {}
    for code in codes:
        try:
            minute[code] = fetch_minute(code)
        except Exception as e:
            logger.warning(f"[{code}] 분봉 실패: {e}")
        news[code], complete[code] = fetch_news(code, since)
        time.sleep(0.2)
    for sub in ("minute", "news", "snapshot"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    saved: list[date] = []
    for d, why in plan.items():
        d8 = d.strftime("%Y%m%d")
        frames = []
        for code in why:
            m = minute.get(code)
            if m is not None and not m.empty:
                x = m[m["시각"].str.startswith(d8)]
                if not x.empty:
                    frames.append(x.assign(종목코드=code)[["종목코드", "시각", "가격", "누적거래량"]])
        if frames:
            pd.concat(frames).to_csv(out / "minute" / f"{d}.csv.gz", index=False, compression="gzip")
        else:
            logger.warning(f"{d} 분봉 없음 — 6거래일 창 밖이거나 수집 실패")
        lo, hi = _prev_trading_day(d).strftime("%Y%m%d") + "1530", d8 + "2010"
        items = [n for code in why for n in news.get(code, []) if lo <= n["time"] <= hi]
        # 최근 5거래일 기사 수 — 각 날의 창은 [직전 거래일 15:30, 그날 15:30). 마지막 값이 판단 시각 전 그날 몫
        wins, x = [], d
        for _ in range(FRESH_DAYS):
            wins.append((_prev_trading_day(x).strftime("%Y%m%d") + "1530", x.strftime("%Y%m%d") + "1530"))
            x = _prev_trading_day(x)
        wins.reverse()
        counts = {code: [sum(a <= n["time"] < b for n in news.get(code, [])) for a, b in wins] for code in why}
        (out / "news" / f"{d}.json").write_text(json.dumps(
            {"date": d.isoformat(), "from": lo, "to": hi, "stocks": why, "items": items,
             "counts5": counts, "counts5_days": [b[:8] for _, b in wins],
             "counts5_partial": sorted(c for c in why if not complete.get(c, False))},
            ensure_ascii=False), encoding="utf-8")
        snap = SNAP_DIR / f"{d}.json"
        if snap.exists():
            shutil.copy(snap, out / "snapshot" / snap.name)
        saved.append(d)
        logger.info(f"{d} 저장: 종목 {len(why)} · 분봉 {sum(len(f) for f in frames)}행 · 기사 {len(items)}건"
                    f" · 스냅샷 {'있음' if snap.exists() else '없음'}")
    return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="이 날짜부터 저장(YYYY-MM-DD). 기본 2026-09-28")
    a = ap.parse_args()
    run(start=date.fromisoformat(a.start) if a.start else START)
