# scripts/check_cards.py
"""14:20 점검 카드 — 강사가 판단 전에 확인하는 질문 하나에 카드 한 장, 답은 숫자만(2026-10-25 사용자 결정).

  1 돌팬티 9강 ① 지수가 오르나, 내리나      — 지수 일봉 60일 + 20·60일선
  2 돌팬티 9강 ② 시장에 돈이 들어오나       — 지수 거래대금, 전일 같은 시각 대비·진행률, 최근 6거래일
  3 돌팬티 9강 ③ 어디에 돈이 몰렸나         — 거래대금 상위 10, 투탑, 최근 4일 정규장 일봉
  4 돌팬티 9강 ④⑤ 상한가와 같은 테마        — 상한가 상위 5(거래대금), 기사 제목 원문, 같은 테마 종목
  5 돌팬티 9강 ⑥ 수급이 붙어 있나           — 시장 순매수(잠정), 상위 10 기관·외인 연속 일수(전일까지)
  6 미모사 5강   돈이 한 곳에 몰렸나        — 상위 5 비중·오른 종목 비율·29.5% 이상 수, 최근 6거래일
  7 미모사 5강   신고가 부근 주도주·NXT     — 상위 10의 250거래일 고가 대비, NXT 거래상위 포함 여부 + 판 입력 안내

판정·해석 문구는 넣지 않는다([[feedback_display_discipline]]). 기사 제목은 원문이고 상승 원인은 확인하지 않는다.
복기 카드(`build_review`)는 판단 전에 보내지 않는다 — 사용자가 매수(연습 포함)를 적은 뒤 수거 때 보낸다.
어느 수집이 실패해도 그 칸만 비우고 나머지는 그린다. 견본·GPT 검토: 백업 레포 `교차검증/운용전략_2026-10-25/`.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from statistics import mean

from scripts.market_card import _Canvas, UP, DOWN, FLAT, INK, SUB, KOSPI_C, KOSDAQ_C, _tone, _pct, W

logger = logging.getLogger(__name__)

KRX_CLOSE_DIR = Path("data") / "krx_close"
TWIN_CODES = ("005930", "000660")
ORANGE = "#d97706"
_WD = ["월", "화", "수", "목", "금", "토", "일"]


def _tv(eok):
    if not eok:
        return "-"
    if eok >= 10000:
        return f"{eok/10000:.1f}조"
    return f"{int(Decimal(str(eok)).quantize(Decimal('1'), ROUND_HALF_UP)):,}억"


# ── 수집 ────────────────────────────────────────────────────────────────
def _closed_days(today_s: str, n: int) -> list[str]:
    days = sorted(p.stem for p in KRX_CLOSE_DIR.glob("*.csv") if p.stem < today_s)
    return days[-n:]


def _load_close(day: str) -> dict:
    try:
        with open(KRX_CLOSE_DIR / f"{day}.csv", encoding="utf-8-sig") as f:
            return {r["종목코드"]: r for r in csv.DictReader(f)}
    except OSError:
        return {}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rank_history(today_s: str, universe: set | None, n: int = 5) -> list[dict]:
    """지난 n거래일 각 날의 거래대금 순위(15:35 저장분, KRX 정규장 + NXT 15:35까지). 종목코드 → 순위.
    universe = 오늘 상위 10을 뽑은 종목군(ETF 등 제외) — 같은 종목군에서 매겨야 오늘 순위와 견줄 수 있다."""
    out = []
    for day in _closed_days(today_s, n):
        rows = []
        for code, r in _load_close(day).items():
            if universe is not None and code not in universe:
                continue
            tv = (_f(r.get("거래대금")) or 0) + (_f(r.get("NXT거래대금")) or 0)
            if tv > 0:
                rows.append((code, tv))
        rows.sort(key=lambda t: -t[1])
        out.append({code: i for i, (code, _) in enumerate(rows, 1)})
    return out


def _index_rows(code: str, today_s: str) -> list[dict]:
    from scripts.fetch_index_data import INDEX_URL
    from scripts.naver_api import get_json, to_float
    raw = {}
    for page in (1, 2, 3):
        data = get_json(INDEX_URL.format(code=code), {"pageSize": 60, "page": page}) or []
        for x in data:
            k = str(x.get("localTradedAt"))
            if k <= today_s:
                raw[k] = {f: to_float(str(x.get(f"{f}Price")).replace(",", "")) for f in ("open", "high", "low", "close")}
        if len(raw) >= 140:
            break
    ks = sorted(k for k in raw if all(raw[k].values()))
    rows = []
    for i, k in enumerate(ks):
        r = {"date": k, **raw[k]}
        if i >= 19:
            r["ma20"] = mean(raw[x]["close"] for x in ks[i - 19:i + 1])
        if i >= 59:
            r["ma60"] = mean(raw[x]["close"] for x in ks[i - 59:i + 1])
        rows.append(r)
    return rows[-60:]


def _consec(rows: list[dict], key: str) -> int:
    from scripts.naver_api import to_float
    vals = [to_float(x.get(key)) or 0 for x in rows]
    if not vals or vals[0] == 0:
        return 0
    s = 1 if vals[0] > 0 else -1
    n = 0
    for v in vals:
        if v * s > 0:
            n += 1
        else:
            break
    return s * n


def _supply_days(code: str, today_s: str) -> tuple[int | None, int | None]:
    """기관·외인 연속 순매수(+)/순매도(-) 일수 — 전일까지(장중엔 오늘 확정치가 없다)."""
    from scripts.naver_api import STOCK_TREND_URL, get_json
    try:
        rows = get_json(STOCK_TREND_URL.format(code=code), {"pageSize": 20}) or []
        rows = [x for x in rows if str(x.get("bizdate", "")) < today_s.replace("-", "")]
        return _consec(rows, "organPureBuyQuant"), _consec(rows, "foreignerPureBuyQuant")
    except Exception as e:
        logger.warning(f"[{code}] 수급 일수 실패: {e}")
        return None, None


def _hi250(code: str, today_s: str, today_high: float | None) -> tuple[float | None, int]:
    from scripts.fetch_stock_data import fetch_daily_history
    try:
        df = fetch_daily_history(code, pages=26)
    except Exception as e:
        logger.warning(f"[{code}] 일봉 실패: {e}")
        return None, 0
    if df is None or not len(df):
        return today_high, 1 if today_high else 0
    df = df[df["date"].astype(str).str.replace(".", "-").str[:10] < today_s]
    vals = [float(x) for x in df.head(249)["high"].tolist()] + ([today_high] if today_high else [])
    return (max(vals) if vals else None), len(vals)


def _news_title(code: str, name: str, since: str, until: str) -> dict | None:
    """since·until: YYYYMMDDHHMM. 게시 시각으로 거른 가장 최근 기사 한 건(원문 제목)."""
    from scripts.fetch_news import NEWS_URL
    from scripts.naver_api import get_json
    try:
        g = get_json(NEWS_URL.format(code=code), {"pageSize": 30, "page": 1}) or []
    except Exception as e:
        logger.warning(f"[{code}] 기사 실패: {e}")
        return {"err": True}
    items = [it for grp in g for it in (grp.get("items") or [])]
    ok = sorted((it for it in items if since <= str(it.get("datetime", ""))[:12] <= until),
                key=lambda it: str(it.get("datetime")), reverse=True)
    for it in ok:
        title = str(it.get("titleFull") or it.get("title") or "").strip()
        if name in title or name[:3] in title:
            dt = str(it["datetime"])
            return {"title": title, "office": it.get("officeName"), "time": f"{dt[4:6]}/{dt[6:8]} {dt[8:10]}:{dt[10:12]}"}
    return {"none_named": bool(ok)}


def collect_extra(d: dict) -> dict:
    """market_brief.collect() 결과(d)에 점검 카드용 자료를 더한다. 14:20 실행에서만."""
    from scripts import market_history as mh
    from scripts.naver_api import fetch_stock_default, fetch_group_list, fetch_group_stocks, to_float
    now: datetime = d["now"]
    today_s = now.date().isoformat()
    x: dict = {"time": now.strftime("%H:%M")}

    for code in ("KOSPI", "KOSDAQ"):
        try:
            x[f"idx_{code}"] = _index_rows(code, today_s)
        except Exception as e:
            logger.warning(f"[{code}] 지수 일봉 실패: {e}")
            x[f"idx_{code}"] = []

    hist = [r for r in mh.load() if r.get("slot") == "1535" and r.get("date", "") < today_s][-5:]
    x["hist"] = [{"date": r["date"], "kospi": _f(r.get("kospi_idx_tv_eok")), "kosdaq": _f(r.get("kosdaq_idx_tv_eok")),
                  "top5": _f(r.get("top5_pct")), "adl": _f(r.get("adl_pct")), "lu": r.get("limit_up")} for r in hist]

    # 오늘 정규장 시가·고가·저가(지금까지) — 15:35 저장과 같은 API
    today_ohlc: dict[str, tuple] = {}
    try:
        for r in fetch_stock_default(trade_type="KRX", market_type="ALL", order_type="marketSum"):
            o, h, l = to_float(r.get("openPrice")), to_float(r.get("highPrice")), to_float(r.get("lowPrice"))
            if o and h and l:
                today_ohlc[str(r.get("itemcode") or "")] = (o, h, l)
    except Exception as e:
        logger.warning(f"오늘 시가·고가·저가 실패: {e}")
    days = _closed_days(today_s, 3)
    closes = {day: _load_close(day) for day in days}
    prev = closes[days[-1]] if days else {}
    x["days"] = [d_[5:].replace("-", "/") for d_ in days] + [today_s[5:].replace("-", "/")]

    def bars(code: str, price: float | None) -> list:
        out = []
        for day in days:
            r = closes[day].get(code)
            vals = tuple(_f(r.get(k)) for k in ("시가", "고가", "저가", "현재가")) if r else None
            out.append(vals if vals and all(vals) else None)
        t = today_ohlc.get(code)
        out.append((t[0], t[1], t[2], price) if t and price else None)
        return out

    price = (d.get("krx_price") or {})
    # 관심이 새로 붙는가 이미 붙어 있었나(2026-10-28 사용자 "많은 사람이 나와 같은 뷰를 보고 있는건지") — 전일 순위·최근 5일 상위 10 횟수
    hist = _rank_history(today_s, d.get("rank_universe"))
    ranks = [h for h in hist if h]
    last = hist[-1] if hist else {}          # 저장이 빠진 날을 건너뛰어 그 전날을 "전일"로 쓰지 않는다(GPT 검토 10-28)
    x["rank_days"] = len(ranks)
    top = []
    for i, r in enumerate((d.get("day_top") or [])[:10], 1):
        code = r["code"]
        f = dict(r, rank=i, new=bool(prev) and code not in prev)
        f["bars"] = bars(code, price.get(code))
        f["inst_days"], f["frgn_days"] = _supply_days(code, today_s)
        th = (today_ohlc.get(code) or (None, None))[1]
        hi, n = _hi250(code, today_s, th)
        cur = price.get(code)
        f["vs_hi"] = round((cur / hi - 1) * 100, 1) if (hi and cur) else None
        f["n_days"] = n
        f["prev_rank"] = (last.get(code) or 999) if last else None
        f["top10_cnt"] = sum(1 for h in ranks if h.get(code, 99) <= 10)
        top.append(f)
    x["top10"] = top

    # 상한가(29.5% 이상) — 상장 첫날은 따로. 상위 5만 기사·테마를 붙인다.
    lus = d.get("limit_up_rows") or []
    x["new_listings"] = [r for r in lus if prev and r["code"] not in prev]
    real = [r for r in lus if not (prev and r["code"] not in prev)]
    real.sort(key=lambda r: -(r.get("tv_eok") or 0))
    x["limit_n"] = len(real)
    since = (days[-1].replace("-", "") + "1530") if days else today_s.replace("-", "") + "0000"
    until = now.strftime("%Y%m%d%H%M")
    themes, members, mnames = [], {}, {}
    try:
        themes = sorted((t for t in fetch_group_list("theme") if to_float(t.get("changeRate")) is not None),
                        key=lambda t: -to_float(t.get("changeRate")))[:30]
        for t in themes:
            ms = fetch_group_stocks("theme", t.get("no"))
            members[t.get("name")] = [str(m.get("itemcode")) for m in ms]
            mnames.update({str(m.get("itemcode")): m.get("itemname") for m in ms})
    except Exception as e:
        logger.warning(f"테마 구성 실패: {e}")
    chg_now = {r["code"]: r.get("chg") for r in (d.get("day_rows") or [])}
    lim = []
    for r in real[:5]:
        f = dict(r)
        f["news"] = _news_title(r["code"], r["name"], since, until)
        th = next((t for t in themes if r["code"] in members.get(t.get("name"), [])), None)
        if th:
            mates = [m for m in members[th.get("name")] if m != r["code"] and chg_now.get(m) is not None]
            tvs = {x_["code"]: x_.get("total_eok") or 0 for x_ in (d.get("day_rows") or [])}
            mates.sort(key=lambda m: -tvs.get(m, 0))
            f["theme"] = {"name": th.get("name"), "chg": to_float(th.get("changeRate")),
                          "mates": [(mnames.get(m, m), chg_now.get(m)) for m in mates[:4]]}
        lim.append(f)
    x["limit_top"] = lim
    x["limit_rest"] = [r["name"] for r in real[5:]]
    return x


# ── 그리기 ──────────────────────────────────────────────────────────────
def _stamp(d: dict, x: dict) -> str:
    now: datetime = d["now"]
    return f"{now.month:02d}/{now.day:02d}({_WD[now.weekday()]}) {x['time']} 기준 · 마감 전 잠정 · 네이버 · 판정 칸 없음"


def _head(c, no, who, q, src):
    c.rect(0, 0, W, 15, "#1b2230")
    c.text(3, 4.5, f"{no}  {who}", size=11, color="#c9d1e0")
    c.text(3, 10.5, q, size=16, color="white", bold=True)
    c.text(97, 4.5, src, size=9, color="#9aa4b5", ha="right")


def _foot(c, y, stamp, extra=None):
    c.hline(y)
    if extra:
        c.text(3, y + 3.5, extra, size=9, color=ORANGE)
        y += 4
    c.text(3, y + 3.5, stamp, size=8.5, color=SUB)
    return y + 7


def _candles(c, rows, x0, x1, top, h):
    vals = [r[k] for r in rows for k in ("high", "low")] + [r[k] for r in rows for k in ("ma20", "ma60") if r.get(k)]
    lo, hi = min(vals), max(vals)
    step = (x1 - x0) / len(rows)
    def X(i): return x0 + step * (i + 0.5)
    def Y(v): return top + h - (v - lo) / ((hi - lo) or 1) * h
    span = hi - lo
    unit = 100 if span > 600 else (50 if span > 250 else 20)
    lv = (int(lo // unit) + 1) * unit
    while lv < hi:
        c.ax.plot([x0, x1], [Y(lv), Y(lv)], color="#eeeeee", linewidth=0.8, zorder=0)
        c.text(x1 + 0.3, Y(lv), f"{lv:,.0f}", size=7.5, color=SUB)
        lv += unit * (2 if span / unit > 8 else 1)
    for i in range(0, len(rows) - 1, 10):
        c.text(X(i), top + h + 3, rows[i]["date"][5:].replace("-", "/"), size=8, color=SUB, ha="center")
    c.text(X(len(rows) - 1), top + h + 3, rows[-1]["date"][5:].replace("-", "/"), size=8, color=INK, ha="center", bold=True)
    for key, col in (("ma60", "#9aa4b5"), ("ma20", ORANGE)):
        pts = [(X(i), Y(r[key])) for i, r in enumerate(rows) if r.get(key)]
        if pts:
            xs, ys = zip(*pts)
            c.ax.plot(xs, ys, color=col, linewidth=1.4, zorder=1)
    for i, r in enumerate(rows):
        col = UP if r["close"] > r["open"] else (DOWN if r["close"] < r["open"] else FLAT)
        c.ax.plot([X(i), X(i)], [Y(r["high"]), Y(r["low"])], color=col, linewidth=0.8, zorder=2)
        yt, yb = Y(max(r["open"], r["close"])), Y(min(r["open"], r["close"]))
        c.ax.add_patch(c.plt.Rectangle((X(i) - step * 0.35, yt), step * 0.7, max(yb - yt, 0.15),
                                       facecolor=col, edgecolor=col, zorder=3))


def _mini(c, x0, x1, ymid, hh, bars) -> int:
    ok = [b for b in bars if b]
    if not ok:
        return 0
    lo, hi = min(b[2] for b in ok), max(b[1] for b in ok)
    step = (x1 - x0) / len(bars)
    def Y(v): return ymid + hh / 2 - (v - lo) / ((hi - lo) or 1) * hh
    for i, b in enumerate(bars):
        if not b:
            continue
        o, h, l, cl = b
        col = UP if cl > o else (DOWN if cl < o else FLAT)
        xc = x0 + step * (i + 0.5)
        c.ax.plot([xc, xc], [Y(h), Y(l)], color=col, linewidth=0.9, zorder=2)
        yt, yb = Y(max(o, cl)), Y(min(o, cl))
        c.ax.add_patch(c.plt.Rectangle((xc - step * 0.3, yt), step * 0.6, max(yb - yt, 0.12),
                                       facecolor=col, edgecolor=col, zorder=3))
    return len(ok)


def _card1(d, x, out, stamp):
    from scripts.market_brief import _macro_line
    c = _Canvas(130)
    _head(c, "1/7", "돌팬티 9강 · 2시 30분 점검 ①", "지수가 오르나, 내리나", "9강 사전 점검 6단계")
    y = 20
    idx = d.get("index") or {}
    for code, key in (("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")):
        rows = x.get(f"idx_{code}") or []
        chg = idx.get(f"{key}_chg")
        c.text(3, y, code, size=12, bold=True)
        lvl = idx.get(f"{key}_level") or (rows[-1]["close"] if rows else None)
        c.text(20, y, "-" if lvl is None else f"{lvl:,.2f}", size=13, bold=True)
        c.text(40, y, _pct(chg), size=12, color=_tone(chg), bold=True)
        if rows and rows[-1].get("ma20") and rows[-1].get("ma60"):
            last = rows[-1]
            g20 = (last["close"] / last["ma20"] - 1) * 100
            g60 = (last["close"] / last["ma60"] - 1) * 100
            c.text(97, y, f"20일선 대비 {g20:+.1f}% · 60일선 대비 {g60:+.1f}%", size=10, color=SUB, ha="right")
        if len(rows) >= 10:
            _candles(c, rows, 3, 91, y + 4, 30)
        else:
            c.text(3, y + 15, "지수 일봉 수집 실패", size=10, color=SUB)
        y += 44
    c.text(3, y - 2, "일봉 · 빨강 양봉 · 파랑 음봉 · 주황 선 20일선 · 회색 선 60일선 · 오늘 봉은 지금까지", size=8.5, color=SUB)
    ml = _macro_line(d)
    if ml:
        c.text(3, y + 3, ml, size=9, color=INK)
        y += 4
    c.save(out, _foot(c, y + 2, stamp))


def _card2(d, x, out, stamp):
    c = _Canvas(100)
    _head(c, "2/7", "돌팬티 9강 · 2시 30분 점검 ②", "시장에 돈이 들어오나", "9강 사전 점검 6단계")
    ci = d.get("cmp_idx") or {}
    y = 21
    for i, (nm, k, col) in enumerate((("코스피", "kospi", KOSPI_C), ("코스닥", "kosdaq", KOSDAQ_C))):
        xx = 3 + i * 48
        c.box(xx, y, 46, 22)
        c.text(xx + 3, y + 5, f"{nm} 거래대금(주식·정규장, 지금까지)", size=9.5, color=SUB)
        c.text(xx + 3, y + 11.5, _tv(d.get(f"{k}_idx_tv_eok")), size=17, bold=True)
        prog = ci.get(f"progress_{k}")
        c.text(xx + 3, y + 18, f"전일 같은 시각 대비 {_pct(ci.get(f'{k}_vs_prev'), 1)} · 전일 하루치의 {'-' if prog is None else f'{prog}%'}",
               size=9, color=INK)
    y = 50
    c.text(3, y, "최근 거래일 (각 날 15:35 실행분) + 오늘 지금까지", size=11, bold=True)
    c.text(83, y, "■ 코스피", size=9.5, color=KOSPI_C, ha="right")
    c.text(97, y, "■ 코스닥", size=9.5, color=KOSDAQ_C, ha="right")
    rows = list(x.get("hist") or []) + [{"date": d["now"].date().isoformat(), "kospi": d.get("kospi_idx_tv_eok"),
                                           "kosdaq": d.get("kosdaq_idx_tv_eok"), "today": True}]
    vmax = max(((r.get("kospi") or 0) + (r.get("kosdaq") or 0)) for r in rows) or 1
    for j, r in enumerate(rows):
        xx = 6 + j * 15.5
        base = y + 34
        hk = 28 * (r.get("kospi") or 0) / vmax
        hq = 28 * (r.get("kosdaq") or 0) / vmax
        a = 0.45 if r.get("today") else 1.0
        c.rect(xx, base - hk, 9, hk, KOSPI_C, alpha=a)
        c.rect(xx, base - hk - hq, 9, hq, KOSDAQ_C, alpha=a)
        c.text(xx + 4.5, base - hk - hq - 2.5, _tv((r.get("kospi") or 0) + (r.get("kosdaq") or 0)), size=8, ha="center")
        lab = "오늘(지금까지)" if r.get("today") else r["date"][5:].replace("-", "/")
        c.text(xx + 4.5, base + 3, lab, size=8.5, color=INK if r.get("today") else SUB, ha="center")
    c.save(out, _foot(c, y + 41, stamp))


def _card3(d, x, out, stamp):
    rows = x.get("top10") or []
    c = _Canvas(44 + len(rows) * 9)
    _head(c, "3/7", "돌팬티 9강 · 2시 30분 점검 ③", "어디에 돈이 몰렸나 · 투탑이 대금을 끌고 가나", "9강 사전 점검 6단계")
    y = 20
    days = x.get("days") or []
    for xx, s in ((3, "순위"), (10, "종목"), (40, "등락"), (55, "거래대금 KRX+NXT"),
                  (78, f"{days[0]} → {days[-1]} 일봉" if days else "최근 4일 일봉")):
        c.text(xx, y, s, size=9, color=SUB)
    c.hline(y + 2.5)
    nd = x.get("rank_days") or 0
    for r in rows:
        y += 9
        twin = r["code"] in TWIN_CODES
        if twin:
            c.rect(1.5, y - 3.3, 97, 8.2, "#fff4e0")
        c.text(4.5, y, str(r["rank"]), size=11, bold=True, ha="center")
        c.text(10, y, r["name"], size=11, bold=twin)
        if nd and not r.get("new"):
            pr = r.get("prev_rank")
            prev_s = "전일 저장 없음" if pr is None else (f"전일 {pr}위" if pr <= 99 else "전일 100위 밖")
            c.text(10, y + 3.6, f"{prev_s} · 최근 {nd}일 중 상위 10 {r.get('top10_cnt', 0)}번", size=8, color=SUB)
        c.text(40, y, _pct(r.get("chg")), size=11, color=_tone(r.get("chg")), bold=True)
        c.text(55, y, _tv(r.get("total_eok")), size=11)
        n = _mini(c, 78, 97, y, 5.6, r.get("bars") or [])
        if r.get("new"):
            c.text(78, y, "상장 첫날", size=8.5, color=SUB)
        elif n == 0:
            c.text(78, y, "일봉 없음", size=8.5, color=SUB)
    y += 9
    c.text(3, y, "봉: KRX 정규장 시가·고가·저가·종가(지난날은 15:35 저장분, 오늘은 지금까지) · 종목마다 자기 범위로 그림 · 주황 줄 = 투탑",
           size=8, color=SUB)
    if nd:
        y += 4
        c.text(3, y, "전일 순위·상위 10 횟수: 각 날 15:35 저장분(KRX 정규장 + NXT) 거래대금, 오늘과 같은 종목군 · 오늘 순위는 지금까지 누적이라 시점이 다름", size=8, color=SUB)
    c.save(out, _foot(c, y + 4, stamp))


def _card4(d, x, out, stamp):
    lim = x.get("limit_top") or []
    news_rows = len(lim)
    c = _Canvas(48 + max(news_rows, 1) * 13)
    _head(c, "4/7", "돌팬티 9강 · 2시 30분 점검 ④⑤", "상한가는 무엇이고, 같은 테마는 같이 움직였나", "9강 00:02:05")
    y = 21
    if not lim:
        c.text(3, y, "상한가(29.5% 이상, 상장 첫날 제외) 없음", size=11, color=SUB)
        y += 13
    for f in lim:
        c.text(3, y, f["name"], size=11.5, bold=True)
        c.text(28, y, _pct(f.get("chg")), size=10.5, color=UP, bold=True)
        c.text(97, y, f"거래대금 {_tv(f.get('tv_eok'))}", size=10, color=SUB, ha="right")
        n = f.get("news") or {}
        if n.get("title"):
            line, col = f"기사 {n['time']} {n.get('office') or ''}: {n['title'][:42]}", INK
        elif n.get("err"):
            line, col = "기사 조회 실패", SUB
        elif n.get("none_named"):
            line, col = "조회 범위(최근 30건) 안에 종목명이 들어간 기사 없음", SUB
        else:
            line, col = "조회 범위(최근 30건, 전 거래일 15:30부터 지금) 안에 기사 없음", SUB
        c.text(5, y + 4.5, line, size=9, color=col)
        th = f.get("theme")
        if th:
            mates = " · ".join(f"{nm} {_pct(ch, 1)}" for nm, ch in th["mates"])
            c.text(5, y + 8.5, f"테마 {th['name']} {_pct(th.get('chg'), 1)}  |  {mates}", size=8.5, color=SUB)
        else:
            c.text(5, y + 8.5, "등락률 상위 30개 테마에 없음", size=8.5, color=SUB)
        y += 13
    rest = x.get("limit_rest") or []
    if rest:
        c.text(3, y - 1, f"상한가 {x.get('limit_n')}개 중 거래대금 상위 5개 · 나머지: " + " · ".join(rest[:8]), size=8.5, color=SUB)
        y += 4.5
    for r in x.get("new_listings") or []:
        c.text(3, y - 1, f"상장 첫날(상한가 아님): {r['name']} {_pct(r.get('chg'))} · 거래대금 {_tv(r.get('tv_eok'))}", size=8.5, color=SUB)
        y += 4.5
    c.save(out, _foot(c, y - 2, stamp, "기사 제목은 원문 그대로입니다. 그 기사가 오른 원인인지는 확인되지 않았습니다."))


def _card5(d, x, out, stamp):
    rows = x.get("top10") or []
    c = _Canvas(70 + len(rows) * 6.5)
    _head(c, "5/7", "돌팬티 9강 · 2시 30분 점검 ⑥", "수급이 붙어 있나", "9강 00:02:15")
    y = 21
    for i, mk in enumerate(("KOSPI", "KOSDAQ")):
        xx = 3 + i * 48
        fl = (d.get("flow") or {}).get(mk) or {}
        c.box(xx, y, 46, 17)
        c.text(xx + 3, y + 4.5, f"{'코스피' if mk == 'KOSPI' else '코스닥'} 오늘 순매수(잠정)", size=10, color=SUB)
        for j, (nm, k) in enumerate((("외국인", "foreign"), ("기관", "inst"), ("개인", "personal"))):
            v = fl.get(k)
            c.text(xx + 3 + j * 14.5, y + 10, nm, size=9, color=SUB)
            c.text(xx + 3 + j * 14.5, y + 14, "-" if v is None else f"{v:+,.0f}억", size=10.5, color=_tone(v), bold=True)
    y = 46
    c.text(3, y, "거래대금 상위 10 · 연속 순매수(+)/순매도(-) 일수 · 전일까지", size=11, bold=True)
    y += 5
    for xx, s in ((3, "종목"), (45, "기관"), (65, "외국인")):
        c.text(xx, y, s, size=9, color=SUB)
    for r in rows:
        y += 6.5
        c.text(3, y, r["name"], size=10.5)
        for xx, k in ((45, "inst_days"), (65, "frgn_days")):
            v = r.get(k)
            if r.get("new"):
                c.text(xx, y, "상장 첫날", size=10, color=SUB)
            else:
                c.text(xx, y, "-" if v is None else f"{v:+d}일", size=10.5, color=_tone(v), bold=True)
    y += 7
    c.text(3, y, "프로그램 수급: 자료 확보 미확인 (표시 안 함)", size=9, color=SUB)
    c.save(out, _foot(c, y + 4, stamp))


def _card6(d, x, out, stamp):
    c = _Canvas(100)
    _head(c, "6/7", "미모사 5강 · 판단 순서 1", "돈이 한 곳에 몰렸나, 흩어졌나", "5강 판단 순서")
    y = 21
    for i, (nm, v, unit) in enumerate((("거래대금 상위 5 비중", d.get("top5_pct"), "%"),
                                      ("오른 종목 비율", d.get("adl_pct"), "%"),
                                      ("상한가(상장 첫날 제외)", x.get("limit_n"), "개"))):
        xx = 3 + i * 32
        c.box(xx, y, 30, 18)
        c.text(xx + 3, y + 5, nm, size=9.5, color=SUB)
        c.text(xx + 3, y + 12.5, "-" if v is None else f"{v:g}{unit}", size=18, bold=True)
    y = 48
    c.text(3, y, "최근 거래일 (각 날 15:35 실행분)", size=11, bold=True)
    y += 6
    for xx, s in ((3, "날짜"), (25, "상위 5 비중"), (50, "오른 종목"), (75, "29.5% 이상")):
        c.text(xx, y, s, size=9, color=SUB)
    for r in x.get("hist") or []:
        y += 6
        c.text(3, y, r["date"][5:].replace("-", "/"), size=10.5)
        c.text(25, y, "-" if r.get("top5") is None else f"{r['top5']:.1f}%", size=10.5)
        c.text(50, y, "-" if r.get("adl") is None else f"{r['adl']:.1f}%", size=10.5)
        c.text(75, y, str(r.get("lu") or "-"), size=10.5)
    c.text(3, y + 6, "지난날 29.5% 이상 수에는 상장 첫날 종목이 섞일 수 있습니다.", size=8.5, color=SUB)
    c.text(3, y + 10.5, "상위 5 비중 = KRX+NXT 거래대금 상위 5 ÷ KRX 전체(ETF 포함) — 분자·분모 기준이 다릅니다", size=8.5, color=SUB)
    c.save(out, _foot(c, y + 15, stamp))


def _card7(d, x, out, stamp):
    from scripts.market_brief import nights_note
    rows = x.get("top10") or []
    c = _Canvas(62 + len(rows) * 7)
    _head(c, "7/7", "미모사 5강 · 판단 순서 3·4", "신고가 부근에 주도주가 있나 · NXT로 대응 가능한가", "5강 판단 순서")
    y = 21
    for xx, s in ((3, "종목"), (36, "등락"), (52, "최근 250거래일 고가 대비"), (80, "NXT 거래상위")):
        c.text(xx, y, s, size=9, color=SUB)
    c.hline(y + 2.5)
    for r in rows:
        y += 7
        c.text(3, y, r["name"], size=11)
        c.text(36, y, _pct(r.get("chg")), size=11, color=_tone(r.get("chg")), bold=True)
        v, nd = r.get("vs_hi"), r.get("n_days") or 0
        if r.get("new"):
            txt = "상장 첫날"
        elif v is None:
            txt = "-"
        else:
            txt = f"{v:+.1f}%" + (f" (상장 {nd}일째)" if nd < 250 else "")
        c.text(52, y, txt, size=11, color=SUB if (v is None or r.get("new")) else INK)
        nx = r.get("nxt_eok") or 0
        c.text(80, y, f"포함 {_tv(nx)}" if nx > 0 else "미포함", size=10.5, color=INK if nx > 0 else SUB)
    y += 8
    c.text(3, y, "NXT 거래 가능 여부는 자료가 없어 미확인 — NXT 거래대금 상위(시장별 100종목) 포함 여부만 표시", size=8.5, color=SUB)
    nd = d.get("next_day")
    if nd:
        y += 5
        c.text(3, y, f"다음 거래일 {nd.month:02d}/{nd.day:02d} ({_WD[nd.weekday()]}) {nights_note(d)}", size=10, color=INK, bold=True)
    y += 7
    c.box(3, y, 94, 13, "#eef3ff")
    c.text(6, y + 4.5, "판단을 먼저 적어 주세요", size=11, bold=True)
    c.text(6, y + 9.5, "판 대형주|개별주|무포 / 돈: A vs B → A / 근거   ·   매수는 주문 전에 '손절 손절가'까지", size=10)
    c.save(out, _foot(c, y + 17, stamp))


def build(d: dict, out_dir: Path) -> list[Path]:
    """점검 카드 7장. 자료 수집·그리기가 실패한 장은 건너뛴다(빈 목록이면 호출부가 예전 카드로)."""
    try:
        x = collect_extra(d)
    except Exception as e:
        logger.warning(f"점검 카드 자료 수집 실패: {e}")
        return []
    stamp = _stamp(d, x)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, fn in enumerate((_card1, _card2, _card3, _card4, _card5, _card6, _card7), 1):
        p = out_dir / f"check_{i}.png"
        try:
            fn(d, x, p, stamp)
            paths.append(p)
        except Exception as e:
            logger.warning(f"점검 카드 {i} 그리기 실패: {e}")
    return paths


# ── 복기 카드 (매수 기록 뒤) ─────────────────────────────────────────────
def build_review(name: str, code: str, practice: bool, out: Path) -> Path | None:
    """사용자가 적은 매수 종목을 강사 질문으로 다시 본다. 판단 뒤에만 보낸다."""
    from scripts.fetch_stock_data import fetch_daily_history
    today_s = datetime.now().date().isoformat()
    days = _closed_days(today_s + "z", 1)
    last = _load_close(days[-1]).get(code) if days else None
    try:
        df = fetch_daily_history(code, pages=26)
        df = df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"[{code}] 복기 카드 일봉 실패: {e}")
        df = None
    hi = vs = None
    if df is not None and len(df) >= 2:
        hi = float(df.tail(250)["high"].max())
        cur = _f(last.get("현재가")) if last else float(df.iloc[-1]["close"])
        vs = round((cur / hi - 1) * 100, 1) if hi and cur else None
    # 양봉 다음 날 시가 — 네이버 일봉 종가는 애프터마켓가라 15:35 저장 파일(정규장)로만 잰다
    recent = [(day, _load_close(day).get(code)) for day in _closed_days(today_s + "z", 11)]
    recent = [(day, r) for day, r in recent if r and _f(r.get("시가")) and _f(r.get("현재가"))]
    gaps = []
    for (_, a), (_, b) in zip(recent, recent[1:]):
        if _f(a["현재가"]) > _f(a["시가"]):
            gaps.append(round((_f(b["시가"]) / _f(a["현재가"]) - 1) * 100, 1))
    gaps = gaps[-5:]
    if len(recent) < 2:
        gap_txt = "정규장 저장 자료 부족"
    elif gaps:
        gap_txt = "최근 양봉 다음 날 시가: " + " ".join(f"{g:+.1f}%" for g in gaps)
    else:
        gap_txt = f"최근 {len(recent)}거래일 양봉 없음"
    tv = _f(last.get("거래대금")) / 1e8 if last and _f(last.get("거래대금")) else None
    c = _Canvas(150)
    _head(c, "복기", "매수 기록 뒤에만 옵니다", "고른 종목을 강사 질문으로 다시 보기", "판단 전에는 보내지 않음")
    y = 21
    c.text(3, y, f"{'연습 ' if practice else ''}매수: {name}"
                 + (f" · 정규장 거래대금 {_tv(tv)}" if tv else "") + (f" · 250일 고가 대비 {vs:+.1f}%" if vs is not None else ""),
           size=10.5, bold=True)
    blocks = [
        ("돌팬티 9강", [("거래대금 1,500억 이상인가", _tv(tv) if tv else "확인 불가"),
                       ("NXT: 7시 이후 보합을 뚫고 오르는가, 아니면 7시 반 이후까지 기다린다", "19:00 이후 직접 확인"),
                       ("NXT에서 손실 중이면 KRX로 넘기지 않는다 (00:18:59)", "다음 거래일 08:00부터"),
                       ("손실 중이면 15분 안에 대응을 끝낸다 (00:19:29)", "다음 거래일")]),
        ("미모사 5강", [("신고가 또는 신고가 부근인가", f"250일 고가 대비 {vs:+.1f}%" if vs is not None else "확인 불가"),
                       ("뉴스·테마·일정 명분이 있는가", "직접 확인"),
                       ("제외: 양봉 다음 날마다 갭하락하는 종목인가", gap_txt)]),
        ("이러면 틀린 판단 (미리 적기)", [("평균단가를 깨고 약해지면 손절 (미모사 5강)", "가격: 직접 적기"),
                                     ("1회 손절 15만 원 (하루 시간표)", "300만 원 기준 -5%")]),
    ]
    y += 7
    for title, items in blocks:
        c.text(3, y, title, size=12, bold=True)
        c.hline(y + 2.5)
        y += 7
        for q, a in items:
            c.text(5, y, q, size=10)
            c.text(97, y, a, size=10, color=SUB, ha="right")
            y += 6.2
        y += 3
    return c.save(out, _foot(c, y, "전 거래일 정규장 값(15:35 저장분) · 네이버 일봉 · 판정 칸 없음"))
