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
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
ET = ZoneInfo("America/New_York")
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
_FF_FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# 지표 이름 한글화(2026-11-03 사용자 "한글로 해야지"). 정확히 맞는 이름 → 아래 규칙 → 그래도 없으면 영어 그대로
_COUNTRY_KO = {"USD": "미국", "CNY": "중국", "JPY": "일본", "EUR": "유로"}
_TITLE_KO = {
    "Non-Farm Employment Change": "비농업 고용 변화", "ADP Non-Farm Employment Change": "ADP 민간 고용 변화",
    "Unemployment Rate": "실업률", "Unemployment Claims": "신규 실업수당 청구", "Average Hourly Earnings": "평균 시간당 임금",
    "JOLTS Job Openings": "JOLTS 구인 건수", "CPI": "소비자물가", "Core CPI": "근원 소비자물가",
    "CPI Flash Estimate": "소비자물가 예비치", "Core CPI Flash Estimate": "근원 소비자물가 예비치",
    "PPI": "생산자물가", "Core PPI": "근원 생산자물가", "Core PCE Price Index": "근원 PCE 물가",
    "PCE Price Index": "PCE 물가", "Advance GDP": "GDP 속보치", "Prelim GDP": "GDP 잠정치", "Final GDP": "GDP 확정치",
    "GDP": "GDP", "Retail Sales": "소매판매", "Core Retail Sales": "근원 소매판매",
    "ISM Manufacturing PMI": "ISM 제조업 PMI", "ISM Services PMI": "ISM 서비스업 PMI",
    "Flash Manufacturing PMI": "제조업 PMI 예비치", "Flash Services PMI": "서비스업 PMI 예비치",
    "Manufacturing PMI": "제조업 PMI", "Non-Manufacturing PMI": "비제조업 PMI",
    "Caixin Manufacturing PMI": "차이신 제조업 PMI", "Caixin Services PMI": "차이신 서비스업 PMI",
    "Federal Funds Rate": "미국 기준금리 결정", "FOMC Statement": "FOMC 성명", "FOMC Press Conference": "FOMC 기자회견",
    "FOMC Meeting Minutes": "FOMC 의사록", "FOMC Economic Projections": "FOMC 경제전망",
    "BOJ Policy Rate": "일본은행 금리 결정", "Monetary Policy Statement": "통화정책 성명", "BOJ Press Conference": "일본은행 기자회견",
    "BOJ Outlook Report": "일본은행 전망 보고서", "Main Refinancing Rate": "ECB 기준금리 결정",
    "ECB Press Conference": "ECB 기자회견", "Congressional Elections": "의회 선거",
    "Presidential Election": "대통령 선거", "Prelim UoM Consumer Sentiment": "미시간대 소비자심리 예비치",
    "Revised UoM Consumer Sentiment": "미시간대 소비자심리 확정치", "CB Consumer Confidence": "콘퍼런스보드 소비자신뢰",
    "Durable Goods Orders": "내구재 주문", "Core Durable Goods Orders": "근원 내구재 주문",
    "Empire State Manufacturing Index": "뉴욕 제조업지수", "Philly Fed Manufacturing Index": "필라델피아 제조업지수",
    "Industrial Production": "산업생산", "Trade Balance": "무역수지", "Tokyo Core CPI": "도쿄 근원 소비자물가",
    "National Core CPI": "전국 근원 소비자물가", "German Prelim CPI": "독일 소비자물가 예비치",
    "Crude Oil Inventories": "원유 재고", "Treasury Currency Report": "미 재무부 환율보고서",
}
_SUFFIX_KO = {"m/m": "전월 대비", "y/y": "전년 대비", "q/q": "전분기 대비", "q/y": "전년 동기 대비"}
_WHO_KO = {"Fed Chair": "연준 의장", "BOJ Gov": "일본은행 총재", "ECB President": "ECB 총재",
           "FOMC Member": "FOMC 위원", "Treasury Secretary": "재무장관", "President": "대통령"}


def ko_num(v: str) -> str:
    """65K → 6.5만, -1.2M → -120만, 3.4B → 34억. 퍼센트 등 그 밖의 값은 그대로."""
    import re
    m = re.fullmatch(r"(-?[\d.]+)([KMB])", v.strip())
    if not m:
        return v
    n = float(m.group(1)) * {"K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
    if abs(n) >= 1e8:
        return f"{n / 1e8:,.4g}억"
    return f"{n / 1e4:,.4g}만"


def ko_title(title: str) -> str:
    t = title.strip()
    suf = ""
    for en, ko in _SUFFIX_KO.items():
        if t.endswith(" " + en):
            t, suf = t[: -len(en) - 1], f"({ko})"
            break
    if t in _TITLE_KO:
        return _TITLE_KO[t] + suf
    if t.endswith(" Speaks"):                     # "Fed Chair Powell Speaks" → "연준 의장 Powell 발언"
        who = t[: -len(" Speaks")]
        for en, ko in _WHO_KO.items():
            if who.startswith(en):
                who = ko + who[len(en):]
                break
        return f"{who} 발언"
    return title


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


def _night_check(o: dict) -> tuple[dict | None, list[str]]:
    """KIS 응답 검증(2026-11-12 사용자 "검증 추가해줘").
    필수 값이 없으면 (None, 사유) — 0으로 채워 '보합'처럼 보이는 일을 막는다.
    값끼리 맞지 않으면 값은 돌려주되 사유를 붙인다 → 화면에 '확인 필요'."""
    def num(k):
        v = str(o.get(k) or "").strip()
        try:
            return float(v) if v else None
        except ValueError:
            return None
    last, chg, pct, base = num("futs_prpr"), num("futs_prdy_vrss"), num("futs_prdy_ctrt"), num("futs_sdpr")
    if not last or chg is None or pct is None or not base:
        return None, ["필수 값 없음"]
    # 하락이면 등락·등락률 부호가 응답에 따라 양수로 올 수 있어 부호 필드로 맞춘다(4·5 = 하락)
    if str(o.get("prdy_vrss_sign")) in ("4", "5"):
        chg, pct = -abs(chg), -abs(pct)
    warn = []
    if abs((last - base) - chg) > 0.06:
        warn.append("등락폭 불일치")
    if abs(chg / base * 100 - pct) > 0.03:
        warn.append("등락률 불일치")
    hi, lo = num("futs_mxpr"), num("futs_llam")
    if hi and lo and not (lo <= last <= hi):
        warn.append("가격제한폭 밖")
    vol = int(num("acml_vol") or 0)
    if vol == 0:
        warn.append("체결 없음")
    return {"last": last, "chg": chg, "pct": pct, "day_close": base, "vol": vol}, warn


def night_futures(today: date | None = None) -> dict | None:
    """{last, chg, pct, day_close, vol, code, asof, warn} — 정규장 종가(기준가) 대비. 키가 없거나 실패하면 None.
    KIS 응답엔 체결 시각이 없다 → asof는 조회 시각과 세션 구분(06:00부터 18:00 사이 조회면 직전 야간장 마감값)."""
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
        now = datetime.now(KST)
        code = front_month_code(today or now.date())
        r = requests.get(f"{_KIS}/uapi/domestic-futureoption/v1/quotations/inquire-price", headers=h, timeout=10,
                         params={"FID_COND_MRKT_DIV_CODE": "CM", "FID_INPUT_ISCD": code})
        j = r.json()
        if str(j.get("rt_cd")) != "0":
            logger.warning(f"야간선물 응답 오류 ({code}): {j.get('msg1')}")
            return None
        v, warn = _night_check(j.get("output1") or {})
        if v is None:
            logger.warning(f"야간선물 응답 검증 실패 ({code}): {warn}")
            return None
        if warn:
            logger.warning(f"야간선물 확인 필요 ({code}): {warn}")
        closed = 6 <= now.hour < 18
        asof = f"{now:%H:%M} 조회 · " + ("직전 야간장 마감값" if closed else "야간장 진행 중")
        return {"code": code, **v, "asof": asof, "warn": warn}
    except Exception as e:
        logger.warning(f"야간선물 조회 실패 (무시): {e}")
        return None


def night_label(nf: dict | None) -> str:
    """화면용 보조 한 줄 — 기준·조회 시각·확인 필요. 조회 실패면 그렇게 적는다(빈칸 금지)."""
    if not nf:
        return "야간선물 조회 실패"
    s = f"정규장 종가 {nf['day_close']:,.2f} 대비 · {nf.get('asof', '')}".rstrip(" ·")
    if nf.get("warn"):
        s += f" · 확인 필요({', '.join(nf['warn'])})"
    return s


def evening_line() -> str:
    """19:30 카드 설명글 맨 위 한 줄 — 저녁 매수 전에 볼 야간선물·SKHY 시간외(2026-11-02 사용자 메모
    "미장 skhy 상승과 야간선물 확인 안한건 미스"). 둘 다 실패하면 빈 문자열."""
    parts = []
    nf = night_futures()
    if nf:
        parts.append(f"코스피200 야간선물 {nf['last']:,.2f} ({nf['pct']:+.2f}%)"
                     + (f" 확인 필요({', '.join(nf['warn'])})" if nf.get("warn") else ""))
    else:
        parts.append("야간선물 조회 실패")
    try:
        import yfinance as yf
        t = yf.Ticker("SKHY")
        close = float(t.history(period="5d")["Close"].dropna().iloc[-1])
        intr = t.history(period="1d", interval="5m", prepost=True)["Close"].dropna()
        if not intr.empty:
            parts.append(f"SKHY 시간외 {(float(intr.iloc[-1]) / close - 1) * 100:+.2f}%")
    except Exception as e:
        logger.warning(f"SKHY 시간외 조회 실패 (무시): {e}")
    return "🌙 " + " · ".join(parts) + "\n\n" if parts else ""


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
        rd = e["when"].astimezone(ET).date()
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


def _nasdaq_hits(since: datetime, until: datetime, have: set[str]) -> list[dict]:
    """yfinance가 놓친 종목을 나스닥 실적 달력으로 채운다. 2026-11-04 브리핑에서 AMD(11-03 장 마감 후)가 빠졌다."""
    import requests
    out, d = [], since.astimezone(ET).date()
    while d <= until.astimezone(ET).date():
        if d.weekday() < 5:
            try:
                rows = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()}, timeout=6,
                                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}).json()["data"]["rows"] or []
            except Exception as ex:
                logger.warning(f"나스닥 실적 달력 조회 실패 (무시): {ex}")
                return out
            for r in rows:
                sym = r.get("symbol")
                if sym not in EARNINGS_WATCH or sym in have:
                    continue
                bmo = "pre-market" in (r.get("time") or "")
                when = datetime(d.year, d.month, d.day, 8 if bmo else 16, tzinfo=ET).astimezone(KST)
                if since <= when < until:
                    est = (r.get("epsForecast") or "").replace("$", "").replace("(", "-").replace(")", "")
                    out.append({"sym": sym, "name": EARNINGS_WATCH[sym], "when": when, "rep": None, "bmo": bmo,
                                "est": float(est) if est else None})
        d += timedelta(days=1)
    return out


def earnings(since: datetime, until: datetime) -> tuple[list[dict], list[dict]]:
    """(발표된 것, 예정인 것). since부터 지금까지 발표 = 결과, 지금부터 until까지 = 예정."""
    now = datetime.now(KST)

    def job(sym):
        try:
            return _one_earnings(sym, since, until)
        except Exception as ex:
            logger.warning(f"{sym} 실적 일정 실패: {ex}")
            return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        hits = [h for h in ex.map(job, EARNINGS_WATCH) if h]
    hits += _nasdaq_hits(since, until, {h["sym"] for h in hits})
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
    out = []
    try:
        for e in requests.get(_FF_FEED, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).json():
            if e.get("impact") != "High" or e.get("country") not in _FF_COUNTRIES:
                continue
            when = datetime.fromisoformat(e["date"]).astimezone(KST)
            if now <= when < until:
                out.append({"when": when, "country": e["country"], "title": e["title"],
                            "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
    except Exception as ex:
        logger.warning(f"지표 일정 조회 실패: {ex}")
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
