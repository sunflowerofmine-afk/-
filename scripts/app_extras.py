"""판단 카드용 추가 계산 (2026-11-27 사용자 요청) — 14:20·15:35 앱 스냅샷에 붙인다.

1. theme_flow: 시간대(30분)별로 어느 테마에 돈이 몰렸나. "오늘은 A → B → C로 돈이 옮겨 갔다"를 숫자로.
   거래대금 상위 종목(투탑 제외)의 네이버 1분봉(KRX)으로 30분마다 거래대금(Δ거래량 × 가격)을 만들고,
   theme_members.json 테마에 묶는다. 한 종목은 하루 합계가 가장 큰 테마 하나에만 넣는다(겹침 방지, 히트맵과 같은 원칙).
2. new_highs: 신고가 돌파·근접 + 거래대금 폭발 종목. 거래대금 상위 종목의 일봉으로
   직전 250·60거래일 고가 대비, 오늘 거래대금 ÷ 직전 20일 평균(종가×거래량 추정)을 잰다.

판정 문구는 넣지 않는다 — 숫자만. 실패해도 스냅샷·알림에 영향 없다.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)
FCHART = "https://fchart.stock.naver.com/sise.nhn"
UA = {"User-Agent": "Mozilla/5.0"}
BUCKETS = [f"{h:02d}{m:02d}" for h in range(9, 16) for m in (0, 30)][:13]   # 0900 … 1500
TWO_TOP = {"005930", "000660"}


def _fchart(code: str, tf: str, count: int) -> list[list[str]]:
    r = requests.get(FCHART, params={"symbol": code, "timeframe": tf, "count": count, "requestType": 0},
                     headers=UA, timeout=8)
    r.raise_for_status()
    return [x.split("|") for x in re.findall(r'<item data="([^"]+)"', r.text)]


def _bucket(hhmm: str) -> str | None:
    if not ("0900" <= hhmm <= "1530"):
        return None
    for b in reversed(BUCKETS):
        if hhmm >= b:
            return b
    return None


def _minute_tv(code: str, day8: str) -> dict[str, float]:
    """종목 하나의 30분 구간별 거래대금(억). fchart 분봉의 거래량은 그날 누적이라 차분한다."""
    rows = [r for r in _fchart(code, "minute", 450) if r[0].startswith(day8) and r[4] not in ("null", "") and r[5] not in ("null", "")]
    out: dict[str, float] = {}
    prev = 0.0
    for r in rows:
        vol, price = float(r[5]), float(r[4])
        dv, prev = max(vol - prev, 0.0), vol
        b = _bucket(r[0][8:12])
        if b:
            out[b] = out.get(b, 0.0) + dv * price / 1e8
    return out


def theme_flow(day_rows: list[dict], members: dict, day8: str, quotes: dict | None = None, n: int = 150) -> dict | None:
    if not day_rows or not members:
        return None
    rows = [r for r in day_rows if r["code"] not in TWO_TOP][:n]
    code_themes: dict[str, list[str]] = {}
    from scripts.theme_map import _INDEX_LIKE
    for t in members.get("themes", []):
        if any(k in str(t.get("name", "")).lower() for k in _INDEX_LIKE):
            continue                       # 지수·밸류업 같은 지수형 묶음은 테마가 아니다(히트맵과 같은 기준)
        if len(t.get("codes", [])) < 5:
            continue                       # 5종목 미만 묶음은 테마로 보기 어렵다
        for c in t.get("codes", []):
            code_themes.setdefault(str(c), []).append(t["name"])
    # 테마 강도 = 그 시각 구성 종목 전체의 거래대금 가중 평균 등락률. 종목은 자기가 속한 테마 중 가장 강한 곳에 넣는다
    # (안랩 = 클라우드·보안 중 오늘 같이 오른 보안, 삼성전기 = 자율주행차가 아니라 기판·MLCC 쪽)
    strength: dict[str, float] = {}
    if quotes:
        for t in members.get("themes", []):
            if t["name"] not in {th for v in code_themes.values() for th in v}:
                continue
            w = [(quotes[c]["tv"], quotes[c]["chg"]) for c in map(str, t["codes"]) if c in quotes and quotes[c].get("tv")]
            tot = sum(x for x, _ in w)
            strength[t["name"]] = sum(x * y for x, y in w) / tot if tot else -99.0
    rows = [r for r in rows if r["code"] in code_themes]
    with ThreadPoolExecutor(max_workers=8) as ex:
        per = dict(zip([r["code"] for r in rows], ex.map(lambda r: _safe(_minute_tv, r["code"], day8), rows)))
    day_tot = {c: sum(v.values()) for c, v in per.items() if v}
    # 테마 하루 합(겹침 포함)으로 순서를 정하고, 종목은 그 순서에서 처음 만나는 테마 하나에만
    theme_sum: dict[str, float] = {}
    for c, tot in day_tot.items():
        for th in code_themes[c]:
            theme_sum[th] = theme_sum.get(th, 0.0) + tot
    if strength:
        assign = {c: max(code_themes[c], key=lambda th: strength.get(th, -99.0)) for c in day_tot}
    else:
        order = sorted(theme_sum, key=lambda x: -theme_sum[x])
        rank = {th: i for i, th in enumerate(order)}
        assign = {c: min(code_themes[c], key=lambda th: rank.get(th, 1e9)) for c in day_tot}
    names = {r["code"]: r["name"] for r in rows}
    grid: dict[str, dict[str, float]] = {}
    leaders: dict[str, list[tuple[float, str]]] = {}
    for c, th in assign.items():
        g = grid.setdefault(th, {})
        for b, v in per[c].items():
            g[b] = g.get(b, 0.0) + v
        leaders.setdefault(th, []).append((day_tot[c], names.get(c, c)))
    seen = [b for b in BUCKETS if any(b in g for g in grid.values())]
    totals = {th: sum(g.values()) for th, g in grid.items()}
    top_themes = sorted(totals, key=lambda x: -totals[x])[:8]
    buckets = []
    for b in seen:
        tot_b = sum(g.get(b, 0.0) for g in grid.values()) or 1.0
        best = sorted(grid, key=lambda th: -grid[th].get(b, 0.0))[:3]
        buckets.append({"t": f"{b[:2]}:{b[2:]}", "top": [{"theme": th, "eok": round(grid[th].get(b, 0.0)),
                                                           "share": round(grid[th].get(b, 0.0) / tot_b * 100, 1)} for th in best]})
    return {
        "basis": "투탑 뺀 거래대금 상위 %d종목 · KRX 1분봉 · 종목은 그날 가장 강한 소속 테마 하나에만" % len(rows),
        "buckets": buckets,
        "themes": [{"theme": th, "eok": round(totals[th]),
                    "series": [round(grid[th].get(b, 0.0)) for b in seen],
                    "leaders": [x[1] for x in sorted(leaders[th], reverse=True)[:3]]} for th in top_themes],
        "times": [f"{b[:2]}:{b[2:]}" for b in seen],
    }


def _hist(code: str, today_s: str) -> dict | None:
    rows = [r for r in _fchart(code, "day", 260) if len(r) >= 6 and r[0] < today_s.replace("-", "")]
    if len(rows) < 5:
        return None
    hi = [float(r[2]) for r in rows]
    tv = [float(r[4]) * float(r[5]) / 1e8 for r in rows[-20:]]
    return {"hi250": max(hi[-250:]), "hi60": max(hi[-60:]), "n": len(rows), "avg20_eok": sum(tv) / len(tv)}


def new_highs(day_rows: list[dict], price: dict, today_s: str, n: int = 100) -> list[dict]:
    rows = day_rows[:n]
    with ThreadPoolExecutor(max_workers=8) as ex:
        hs = list(ex.map(lambda r: _safe(_hist, r["code"], today_s), rows))
    out = []
    for r, h in zip(rows, hs):
        p = price.get(r["code"])
        if not h or not p:
            continue
        vs250 = (p / h["hi250"] - 1) * 100
        vs60 = (p / h["hi60"] - 1) * 100
        ratio = r["krx_eok"] / h["avg20_eok"] if h["avg20_eok"] else None
        if vs60 >= -3 or vs250 >= -3:
            out.append({"code": r["code"], "name": r["name"], "chg": round(r["chg"], 2), "krx_eok": round(r["krx_eok"]),
                        "vs250": round(vs250, 1), "vs60": round(vs60, 1), "tv_x": round(ratio, 1) if ratio else None,
                        "new250": vs250 > 0, "new60": vs60 > 0, "days": h["n"]})
    out.sort(key=lambda x: (-(x["new250"] + x["new60"]), -(x["tv_x"] or 0)))
    return out[:15]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        logger.warning(f"{fn.__name__}{a[:1]} 실패: {e}")
        return {} if fn is _minute_tv else None


def us_news(n: int = 6) -> list[dict] | None:
    """구글 뉴스 RSS(한국어) '뉴욕증시·미국증시' 최근 하루 헤드라인. 제목·언론사·시각·링크만(본문 안 가져옴).
    세이브티커는 서버 접속을 봇 확인으로 막아 쓰지 않는다(2026-11-27 확인, 우회 금지)."""
    import xml.etree.ElementTree as ET
    from datetime import timedelta, timezone
    from email.utils import parsedate_to_datetime
    url = "https://news.google.com/rss/search"
    params = {"q": "뉴욕증시 when:1d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    import time as _time
    try:
        for i in range(2):                 # 429면 한 번만 쉬었다 다시(하루 두 번 부르는 양)
            r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0 (compatible; jongbe-bot)"}, timeout=10)
            if r.status_code != 429:
                break
            _time.sleep(5)
        r.raise_for_status()
        items = ET.fromstring(r.content).findall("./channel/item")
    except Exception as e:
        logger.warning(f"미국 뉴스 RSS 실패: {e}")
        return None
    kst = timezone(timedelta(hours=9))
    out, seen = [], set()
    for it in items:
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(" - " + src)]
        key = re.sub(r"\W", "", title)[:30]
        if not title or key in seen:
            continue
        seen.add(key)
        try:
            when = parsedate_to_datetime(it.findtext("pubDate")).astimezone(kst)
        except Exception:
            continue
        out.append({"title": title, "source": src, "when": when.strftime("%m-%d %H:%M"), "ts": when.timestamp(),
                    "link": it.findtext("link") or ""})
    out.sort(key=lambda x: -x["ts"])
    for x in out:
        x.pop("ts", None)
    return out[:n]
