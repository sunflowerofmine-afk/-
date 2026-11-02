# scripts/fetch_events.py
"""아침 브리핑용 — 코스피200 야간선물 + 굵직한 이벤트(빅테크·반도체 실적, 미국 주요 지표).

2026-11-03 사용자: "아침 브리핑에 야간선물 나오도록 해주고 굵직한 이벤트가 있으면 같이 출력해줘
예를들어 빅테크 실적 결과라든지".

야간선물: KRX 야간시장(18:00부터 다음 날 06:00, 2025-06부터 KRX 자체 운영). 네이버는 정규장 값만 준다
(2026-11-03 06:40 조회 — 15:34 값 그대로, 관측). KIS 국내선물 시세(FHMIF10000000)에 시장구분 CM(야간선물)을
넣으면 야간 현재가와 정규장 종가 대비 등락이 나온다(같은 시각 883.40 / -35.25 / -3.84%, 정규장 종가 918.65, 재현).
실적: yfinance 실적 일정(발표 시각·예상 EPS·발표 EPS). 발표 직후엔 발표 EPS가 비어 있을 수 있다 — 그땐 주가 반응만 쓴다.
지표: ForexFactory 주간 일정(JSON), 영향도 High만. 일정만 있고 발표치는 없다.
모두 실패해도 예외를 던지지 않는다 — 브리핑은 나가야 한다.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
_KIS = "https://openapi.koreainvestment.com:9443"
_TOKEN_CACHE = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or "/tmp") / "kis_token.json"

# 투톱 갭에 직결되는 미국 빅테크·반도체. 이름은 브리핑 표기용
EARNINGS_WATCH = {
    "NVDA": "엔비디아", "MSFT": "마이크로소프트", "AAPL": "애플", "GOOGL": "알파벳", "AMZN": "아마존",
    "META": "메타", "TSLA": "테슬라", "AVGO": "브로드컴", "AMD": "AMD", "MU": "마이크론",
    "TSM": "TSMC", "ASML": "ASML", "QCOM": "퀄컴", "INTC": "인텔", "ARM": "ARM",
    "ORCL": "오라클", "PLTR": "팔란티어", "AMAT": "어플라이드", "LRCX": "램리서치", "SNDK": "샌디스크",
}
# 지표 일정에서 볼 나라. 한국 원화는 ForexFactory에 없다
_FF_COUNTRIES = {"USD", "CNY", "JPY", "EUR"}
_FF_FEEDS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
             "https://nfs.faireconomy.media/ff_calendar_nextweek.json")


# ── 야간선물 ─────────────────────────────────────────────

def _second_thursday(y: int, m: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(3 - d.weekday()) % 7)
    return d + timedelta(days=7)


def front_month_code(today: date) -> str:
    """코스피200 선물 최근월물 코드(A01 + 연도 끝자리 + 월). 만기(분기 둘째 목요일)가 지나면 다음 분기물."""
    y, m = today.year, today.month
    q = ((m - 1) // 3 + 1) * 3
    if m == q and today > _second_thursday(y, q):
        q += 3
    if q > 12:
        y, q = y + 1, q - 12
    return f"A01{y % 10}{q:02d}"


def _kis_token(key: str, secret: str) -> str | None:
    """접근 토큰. 발급은 1분에 1번만 되므로 같은 러너 안에서는 파일로 재사용한다."""
    import requests
    try:
        if _TOKEN_CACHE.exists() and time.time() - _TOKEN_CACHE.stat().st_mtime < 20 * 3600:
            tok = json.loads(_TOKEN_CACHE.read_text()).get("access_token")
            if tok:
                return tok
    except Exception:
        pass
    r = requests.post(f"{_KIS}/oauth2/tokenP", timeout=10,
                      json={"grant_type": "client_credentials", "appkey": key, "appsecret": secret})
    tok = r.json().get("access_token")
    if tok:
        try:
            _TOKEN_CACHE.write_text(r.text)
        except Exception:
            pass
    return tok


def night_futures(today: date | None = None) -> dict | None:
    """{last, chg, pct, day_close, vol, code} — 정규장 종가 대비. 키가 없거나 실패하면 None."""
    import requests
    key, secret = os.environ.get("KIS_APP_KEY", "").strip(), os.environ.get("KIS_APP_SECRET", "").strip()
    if not key or not secret:
        logger.info("KIS 키 없음 — 야간선물 생략")
        return None
    try:
        tok = _kis_token(key, secret)
        if not tok:
            return None
        h = {"authorization": f"Bearer {tok}", "appkey": key, "appsecret": secret,
             "tr_id": "FHMIF10000000", "custtype": "P"}
        code = front_month_code(today or datetime.now(KST).date())
        r = requests.get(f"{_KIS}/uapi/domestic-futureoption/v1/quotations/inquire-price", headers=h, timeout=10,
                         params={"FID_COND_MRKT_DIV_CODE": "CM", "FID_INPUT_ISCD": code})
        o = r.json().get("output1") or {}
        last, chg = float(o.get("futs_prpr") or 0), float(o.get("futs_prdy_vrss") or 0)
        if not last:
            logger.warning(f"야간선물 응답 비어 있음 ({code})")
            return None
        # 등락은 정규장 종가 기준(응답의 전일종가 필드는 정규장 종가와 다른 값이 와서 쓰지 않는다)
        return {"code": code, "last": last, "chg": chg, "pct": float(o.get("futs_prdy_ctrt") or 0),
                "day_close": round(last - chg, 2), "vol": int(float(o.get("acml_vol") or 0))}
    except Exception as e:
        logger.warning(f"야간선물 조회 실패 (무시): {e}")
        return None


# ── 실적 ────────────────────────────────────────────────

def _one_earnings(sym: str, since: datetime, until: datetime) -> dict | None:
    import math
    import yfinance as yf
    t = yf.Ticker(sym)
    df = t.get_earnings_dates(limit=8)
    if df is None or df.empty:
        return None
    for ts, row in df.iterrows():
        when = ts.to_pydatetime().astimezone(KST)
        if not (since <= when < until):
            continue
        est, rep = row.get("EPS Estimate"), row.get("Reported EPS")
        est = None if est is None or (isinstance(est, float) and math.isnan(est)) else float(est)
        rep = None if rep is None or (isinstance(rep, float) and math.isnan(rep)) else float(rep)
        return {"sym": sym, "name": EARNINGS_WATCH[sym], "when": when, "est": est, "rep": rep,
                "bmo": ts.hour < 9 or (ts.hour == 9 and ts.minute < 30)}
    return None


def _reaction(e: dict) -> tuple[float | None, str]:
    """발표 뒤 주가 반응. 장 마감 후 발표 → 다음 정규장이 열렸으면 그 등락, 아니면 시간외. 장 전 발표 → 당일 정규장."""
    import yfinance as yf
    try:
        t = yf.Ticker(e["sym"])
        d = t.history(period="10d")["Close"].dropna()
        d.index = [x.date() for x in d.index]
        rd = e["when"].astimezone(timezone(timedelta(hours=-5))).date()   # 미국 날짜(서머타임 하루 경계는 영향 없음)
        days = list(d.index)
        if e["bmo"]:
            if rd in days and days.index(rd) > 0:
                i = days.index(rd)
                return (d.iloc[i] / d.iloc[i - 1] - 1) * 100, "당일 정규장"
            return None, ""
        if rd not in days:
            return None, ""
        i = days.index(rd)
        if i + 1 < len(days):
            return (d.iloc[i + 1] / d.iloc[i] - 1) * 100, "다음 정규장"
        intr = t.history(period="2d", interval="5m", prepost=True)["Close"].dropna()
        intr = intr[intr.index > e["when"]]          # 발표 뒤 봉만 — 시간외가 비면 발표 전 가격을 반응으로 쓰지 않는다
        if intr.empty:
            return None, ""
        return (float(intr.iloc[-1]) / float(d.iloc[i]) - 1) * 100, "시간외"
    except Exception as ex:
        logger.debug(f"{e['sym']} 반응 조회 실패: {ex}")
        return None, ""


def earnings(since: datetime, until: datetime) -> tuple[list[dict], list[dict]]:
    """(발표된 것, 예정인 것). since부터 지금까지 발표 = 결과, 지금부터 until까지 = 예정."""
    now = datetime.now(KST)

    def job(sym):
        try:
            return _one_earnings(sym, since, until)
        except Exception as ex:
            logger.debug(f"{sym} 실적 일정 실패: {ex}")
            return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        hits = [h for h in ex.map(job, EARNINGS_WATCH) if h]
    done = sorted((h for h in hits if h["when"] <= now), key=lambda h: h["when"])
    for h in done:
        h["move"], h["move_label"] = _reaction(h)
    todo = sorted((h for h in hits if h["when"] > now), key=lambda h: h["when"])
    return done, todo


# ── 지표 일정 ───────────────────────────────────────────

def macro_events(until: datetime) -> list[dict] | None:
    """지금부터 until까지 영향도 High 일정. 이번 주 일정을 못 받으면 None(1시간에 여러 번 부르면 429로 막힌다)."""
    import requests
    now = datetime.now(KST)
    out, seen = [], set()
    for url in _FF_FEEDS:
        try:
            for e in requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).json():
                if e.get("impact") != "High" or e.get("country") not in _FF_COUNTRIES:
                    continue
                when = datetime.fromisoformat(e["date"]).astimezone(KST)
                k = (e["country"], e["title"], when)
                if now <= when < until and k not in seen:
                    seen.add(k)
                    out.append({"when": when, "country": e["country"], "title": e["title"],
                                "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
        except Exception as ex:
            logger.warning(f"지표 일정 조회 실패 ({url}): {ex}")
            if url == _FF_FEEDS[0]:
                return None
    return sorted(out, key=lambda x: x["when"])


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(KST)
    print(night_futures())
    d, t = earnings(now - timedelta(days=1), now + timedelta(days=1))
    print(d, t, sep="\n")
    print(macro_events(now + timedelta(days=1)))
