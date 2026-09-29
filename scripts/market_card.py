# scripts/market_card.py
"""시황 카드 — 텍스트 알림 대신 보내는 이미지 한 장(2026-09-30 사용자: "문자는 가독성이 너무 떨어져").

낮(14:20·15:35)은 판단 순서대로 세 칸이다.
  ① 판 — 투탑(삼성전자·SK하이닉스)이 돈을 빨아들이는가. 사용자가 시뮬레이터 문제 01에서
     "탑2 정보가 없어 판단 불가"라고 적은 자리(2026-09-29) — 그래서 맨 위.
  ② 국면 — 지수 · 시장 거래대금 변화 · 오른 종목 비율 · 상위 5 집중 · 시장 수급
  ③ 돈이 몰린 곳 — 거래대금 상위 10(KRX + NXT) · 상한가
저녁(17:50·19:30)은 저녁장 거래대금과 저녁장 상위 10만 — 정규장 숫자는 반복하지 않는다.

숫자는 market_brief.collect()가 만든 dict 그대로다. 새로 계산하는 것은 투탑 순위와
투탑이 시장(주식, KRX) 거래대금에서 차지하는 비율뿐이다. 해석 문구는 넣지 않는다
([[feedback_display_discipline]] — 판정에 안 쓰는 값·해석은 표시하지 않는다).
그리기에 실패하면 None → 호출부가 텍스트 알림으로 대신 보낸다.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

TWIN = (("005930", "삼성전자"), ("000660", "SK하이닉스"))
_WD = ["월", "화", "수", "목", "금", "토", "일"]

UP, DOWN, FLAT = "#d6293e", "#1f5fd1", "#555555"
INK, SUB, LINE, PANEL = "#111111", "#666666", "#dddddd", "#f4f5f7"
K_BAR, N_BAR = "#8a94a6", "#c9cfda"

W, H_DAY, H_EVE = 100.0, 200.0, 140.0   # 넉넉히 잡고 save()에서 내용 끝에 맞춰 자른다
_BOLD_FONTS = ["/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "C:/Windows/Fonts/malgunbd.ttf"]


def _tone(v):
    if v is None:
        return FLAT
    return UP if v > 0 else (DOWN if v < 0 else FLAT)


def _pct(v, digits=2):
    return "-" if v is None else f"{v:+.{digits}f}%"


def _tv(eok):
    if not eok:
        return "-"
    return f"{eok/10000:.1f}조" if eok >= 10000 else f"{eok:,.0f}억"


class _Canvas:
    """0..100 × 0..H 좌표(위에서 아래로)에 글자·상자를 놓는 얇은 도구."""

    def __init__(self, height: float):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from scripts.theme_map import _font
        self.plt = plt
        self.fp = _font()
        bold = next((p for p in _BOLD_FONTS if Path(p).exists()), None)
        self.fpb = font_manager.FontProperties(fname=bold) if bold else self.fp
        self.H = height
        self.fig = plt.figure(figsize=(8, 8 * height / W), dpi=135)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W)
        self.ax.set_ylim(height, 0)
        self.ax.axis("off")
        self.fig.patch.set_facecolor("white")

    def text(self, x, y, s, size=11, color=INK, ha="left", va="center", bold=False):
        self.ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va,
                     fontproperties=self.fpb if bold else self.fp)

    def box(self, x, y, w, h, color=PANEL, edge=None, lw=0):
        from matplotlib.patches import FancyBboxPatch
        self.ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2",
                                         facecolor=color, edgecolor=edge or color, linewidth=lw))

    def rect(self, x, y, w, h, color):
        self.ax.add_patch(self.plt.Rectangle((x, y), w, h, facecolor=color, edgecolor="none"))

    def hline(self, y, x0=3, x1=97, color=LINE, lw=1):
        self.ax.plot([x0, x1], [y, y], color=color, linewidth=lw)

    def section(self, y, title):
        self.text(3, y, title, size=14, bold=True)
        self.hline(y + 2.3)

    def save(self, out: Path, y_end: float) -> Path:
        """y_end까지만 남긴다 — 가로 축척이 그대로라 글자 크기·비율은 변하지 않는다."""
        self.fig.set_size_inches(8, 8 * y_end / W)
        self.ax.set_ylim(y_end, 0)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.fig.savefig(out, facecolor="white")
        self.plt.close(self.fig)
        return out


def _header(c: _Canvas, d: dict, label: str) -> None:
    now: datetime = d["now"]
    c.rect(0, 0, W, 8.5, "#1b2230")
    c.text(3, 4.3, f"종베 시황  {now.month:02d}/{now.day:02d} ({_WD[now.weekday()]}) {now.strftime('%H:%M')}",
           size=16, color="white", bold=True)
    c.text(97, 4.3, label, size=11, color="#c9d1e0", ha="right")


def _footer(c: _Canvas, d: dict, y: float) -> None:
    from scripts.market_brief import _macro_line
    c.hline(y - 2.5)
    ml = _macro_line(d)
    if ml:
        c.text(3, y, ml, size=9.5, color=SUB)
    nd = d.get("next_day")
    if nd:
        c.text(3, y + 4, f"다음 거래일 {nd.month:02d}/{nd.day:02d} ({_WD[nd.weekday()]}) · 밤 {d.get('nights')}",
               size=10.5, color=INK, bold=True)


def _diverge(c: _Canvas, y: float, label: str, v, x_label=24, x0=62, span=16, lim=60):
    """가운데 0에서 좌우로 뻗는 막대 — 전일 대비 같은 변화율."""
    c.text(x_label, y, label, size=10, color=SUB)
    c.ax.plot([x0, x0], [y - 1.6, y + 1.6], color="#999999", linewidth=1)
    if v is None:
        c.text(x0 + 2, y, "기록 없음", size=10, color=SUB)
        return
    k = max(-1.0, min(1.0, v / lim))
    w = span * k
    c.rect(min(x0, x0 + w), y - 1.2, abs(w), 2.4, _tone(v))
    c.text(97, y, f"{v:+.0f}%", size=12, color=_tone(v), ha="right", bold=True)


def _gauge(c: _Canvas, y: float, label: str, v, marks: tuple, note: str = ""):
    c.text(3, y, label, size=11)
    x0, span = 30, 52
    c.rect(x0, y - 1.1, span, 2.2, "#e6e8ec")
    if v is not None:
        c.rect(x0, y - 1.1, span * max(0.0, min(1.0, v / 100)), 2.2, "#3b4556")
    for m, tag in marks:
        xm = x0 + span * m / 100
        c.ax.plot([xm, xm], [y - 1.9, y + 1.9], color="#e0a100", linewidth=1.4)
        if tag:
            c.text(xm, y + 3.2, tag, size=8, color=SUB, ha="center")
    c.text(97, y, "-" if v is None else f"{v:.0f}%", size=13, ha="right", bold=True)
    if note:
        c.text(3, y + 3.2, note, size=8, color=SUB)


def _top_bars(c: _Canvas, y: float, rows: list[dict], total_key: str, k_key: str, n_key: str,
              right, twin_codes=()) -> float:
    """거래대금 상위 막대 — 진한 몫 = KRX, 옅은 몫 = NXT. right(row) → (문자, 색)."""
    if not rows:
        c.text(3, y, "자료 없음", size=10, color=SUB)
        return y + 4
    vmax = max((r.get(total_key) or 0) for r in rows) or 1
    x_bar, span, step = 27, 44, 3.6
    for i, r in enumerate(rows):
        yy = y + i * step
        tw = r.get("code") in twin_codes
        if tw:
            c.rect(2, yy - 1.7, 96, 3.4, "#fff4d6")
        name = str(r.get("name") or "")
        name = name if len(name) <= 9 else name[:8] + "…"
        c.text(3, yy, f"{i + 1:>2}", size=9, color=SUB)
        c.text(7, yy, name, size=10.5, bold=tw)
        k = r.get(k_key) or 0
        n = r.get(n_key) or 0
        c.rect(x_bar, yy - 1.1, span * k / vmax, 2.2, K_BAR)
        c.rect(x_bar + span * k / vmax, yy - 1.1, span * n / vmax, 2.2, N_BAR)
        c.text(x_bar + span * (k + n) / vmax + 1, yy, _tv(r.get(total_key)), size=9, color=SUB)
        s, col = right(r)
        c.text(97, yy, s, size=11, color=col, ha="right", bold=True)
    return y + len(rows) * step


def _day(d: dict, out: Path, label: str) -> Path:
    c = _Canvas(H_DAY)
    _header(c, d, label)
    rows = d.get("day_rows") or d.get("day_top") or []
    rank = {r["code"]: i + 1 for i, r in enumerate(rows)}
    by_code = {r["code"]: r for r in rows}

    # ① 판 — 투탑
    c.section(13, "① 판 · 투탑")
    for j, (code, nm) in enumerate(TWIN):
        x = 3 + j * 48
        c.box(x, 17, 46, 17)
        r = by_code.get(code)
        c.text(x + 2.5, 20.2, nm, size=13, bold=True)
        if not r:
            c.text(x + 2.5, 27, "자료 없음", size=12, color=SUB)
            continue
        c.text(x + 2.5, 26.3, _pct(r["chg"]), size=24, color=_tone(r["chg"]), bold=True)
        c.text(x + 43.5, 20.2, f"거래대금 {rank.get(code)}위", size=11, ha="right",
               color=UP if rank.get(code, 99) <= 2 else INK, bold=True)
        c.text(x + 2.5, 31.5, f"{_tv(r['total_eok'])} = K {_tv(r['krx_eok'])} + N {_tv(r['nxt_eok'])}",
               size=9.5, color=SUB)
    kt, dt = d.get("kospi_idx_tv_eok"), d.get("kosdaq_idx_tv_eok")
    twin_k = sum((by_code.get(cd) or {}).get("krx_eok", 0) for cd, _ in TWIN)
    if kt and dt and twin_k:
        c.text(3, 37.5, f"투탑 거래대금 = 시장 전체(주식·KRX)의 {twin_k / (kt + dt) * 100:.0f}%",
               size=11.5, bold=True)

    # ② 국면
    c.section(44, "② 국면")
    ix = d.get("index") or {}
    for j, (nm, k) in enumerate((("코스피", "kospi"), ("코스닥", "kosdaq"))):
        x = 3 + j * 48
        c.box(x, 48, 46, 9)
        lv = ix.get(f"{k}_level")
        c.text(x + 2.5, 52.5, nm, size=12, bold=True)
        c.text(x + 17, 52.5, "-" if lv is None else f"{lv:,.2f}", size=11, color=SUB)
        c.text(x + 43.5, 52.5, _pct(ix.get(f"{k}_chg")), size=17, color=_tone(ix.get(f"{k}_chg")),
               ha="right", bold=True)

    ci = d.get("cmp_idx") or {}
    y = 61.5
    c.text(3, y, "거래대금 (주식)" + (" · 지금까지 누적" if d.get("slot") == "1420" else " · 정규장"),
           size=10, color=SUB)
    y += 4
    for nm, k, tv in (("코스피", "kospi", kt), ("코스닥", "kosdaq", dt)):
        c.text(3, y, nm, size=11.5, bold=True)
        c.text(13, y, _tv(tv), size=11.5)
        if d.get("slot") == "1420":
            _diverge(c, y, "전일 같은 시각 대비", ci.get(f"{k}_vs_prev"))
            prog = ci.get(f"progress_{k}")
            c.text(24, y + 3.6, "전일 하루치 진행률", size=10, color=SUB)
            c.text(97, y + 3.6, "-" if prog is None else f"{prog}%", size=11, ha="right")
        else:
            _diverge(c, y, "전일 대비", ci.get(f"{k}_vs_prev"))
            _diverge(c, y + 3.6, "20일 평균 대비", ci.get(f"{k}_vs_avg20"))
        y += 8.6

    _gauge(c, y + 1.5, "오른 종목", d.get("adl_pct"), ((50, "50"),))
    _gauge(c, y + 8.5, "상위 5 집중", d.get("top5_pct"), ((40, "40"), (50, "50")), note="40 미만 분산 · 50 이상 극단")
    y += 15.5
    fl = d.get("flow") or {}
    kf, qf = fl.get("KOSPI") or {}, fl.get("KOSDAQ") or {}
    if kf.get("foreign") is not None:
        tag = "잠정" if str(kf.get("date")) == d["now"].strftime("%Y%m%d") else "전일 확정"
        c.text(3, y, f"수급({tag})", size=10, color=SUB)
        for i, (mk, f) in enumerate((("코스피", kf), ("코스닥", qf))):
            yy = y + i * 4
            c.text(19, yy, mk, size=10.5, bold=True)
            for x, p_name, v in ((32, "외인", f.get("foreign")), (62, "기관", f.get("inst"))):
                c.text(x, yy, p_name, size=9.5, color=SUB)
                c.text(x + 6, yy, "-" if v is None else f"{v:+,.0f}억", size=11, color=_tone(v), bold=True)
        y += 4
    y += 7

    # ③ 돈이 몰린 곳
    c.section(y, "③ 돈이 몰린 곳 · 거래대금 상위 10")
    c.text(97, y, "진한 = KRX · 옅은 = NXT", size=8.5, color=SUB, ha="right")
    y = _top_bars(c, y + 6, d.get("day_top") or [], "total_eok", "krx_eok", "nxt_eok",
                  lambda r: (_pct(r["chg"]), _tone(r["chg"])), twin_codes={cd for cd, _ in TWIN})
    y += 2.5
    lu = d.get("limit_up")
    c.text(3, y, f"상한가 {lu if lu is not None else '-'}", size=12, color=UP, bold=True)
    tops = d.get("limit_up_top") or []
    line, lines = "", []
    for t in (f"{x['name']} {_tv(x['tv_eok'])}" for x in tops):
        if line and len(line) + len(t) > 34:
            lines.append(line); line = ""
        line = f"{line} · {t}" if line else t
    if line:
        lines.append(line)
    for i, ln in enumerate(lines[:2]):
        c.text(17, y + i * 3.8, ln, size=10)
    y += 3.8 * max(0, min(len(lines), 2) - 1)
    _footer(c, d, y + 7)
    return c.save(out, y + 13)


def _evening(d: dict, out: Path, label: str) -> Path:
    c = _Canvas(H_EVE)
    _header(c, d, label)
    prev = d.get("snap_prev") or {}

    def since(cur, key):
        base = prev.get(key)
        return f"17:50 대비 {cur - base:+,.0f}억" if (prev and cur is not None and base is not None) else ""

    c.section(13, "저녁장 거래대금")
    eve = d.get("nxt_eve_total_eok")
    tiles = []
    if eve is not None:
        tiles.append(("NXT 15:30 이후", eve, f"전일 같은 시각 대비 {_pct(d.get('nxt_eve_vs_prev'), 0)}",
                      since(eve, "nxt_eve_total_eok")))
    else:
        tiles.append(("NXT 하루 누적", d.get("nxt_total_eok"), "", since(d.get("nxt_total_eok"), "nxt_total_eok")))
    if d.get("evening_merged"):
        am = d.get("krx_am_total_eok")
        tiles.append(("KRX 15:35 이후", am, "종가매매 + 애프터마켓", since(am, "krx_am_total_eok")))
    for j, (nm, v, s1, s2) in enumerate(tiles):
        x = 3 + j * 48
        c.box(x, 17, 46, 15)
        c.text(x + 2.5, 20.2, nm, size=11.5, bold=True)
        c.text(x + 2.5, 25.2, _tv(v), size=20, bold=True)
        c.text(x + 2.5, 29.8, "  ".join(t for t in (s1, s2) if t), size=9, color=SUB)

    merged = d.get("evening_merged")
    basis = d.get("krx_basis") or "KRX 가격"
    c.section(39, "저녁장 거래대금 상위 10")
    c.text(97, 39, f"등락 = {basis} 대비", size=8.5, color=SUB, ha="right")
    prev_rows = prev.get("rows") or {}
    krx = d.get("krx_price") or {}
    rows = []
    for x in d.get("nxt_top") or []:
        r = dict(x)
        r["_n"] = x.get("nxt_eve_eok", x.get("tv_eok")) or 0
        r["_k"] = (x.get("krx_am_eok") or 0) if merged else 0
        r["_t"] = x.get("total_eok") if merged else r["_n"]
        rows.append(r)

    def right(r):
        px, kp = r.get("price"), krx.get(r.get("code"))
        if not (px and kp):
            return ("기준가 없음", SUB)
        v = (px / kp - 1) * 100
        pv = (prev_rows.get(r.get("code")) or {}).get("price")
        extra = f"  ({(px / pv - 1) * 100:+.1f})" if pv else ""
        return (f"{v:+.2f}%{extra}", _tone(v))

    # 저녁 막대는 진한 = NXT, 옅은 = KRX 애프터마켓 (저녁은 NXT가 본류)
    y = _top_bars(c, 45, rows, "_t", "_n", "_k", right, twin_codes={cd for cd, _ in TWIN})
    note = "진한 = NXT 15:30 이후 · 옅은 = KRX 15:35 이후" if merged else "NXT 거래대금"
    if prev_rows:
        note += " · 괄호 = 17:50 대비"
    c.text(3, y + 1.5, note, size=8.5, color=SUB)
    _footer(c, d, y + 8)
    return c.save(out, y + 14)


def build(d: dict, out_dir: Path) -> Path | None:
    """카드 PNG 경로. 실패하면 None — 호출부가 텍스트로 대신 보낸다."""
    from scripts.market_brief import _SLOT_LABEL
    now: datetime = d["now"]
    label = _SLOT_LABEL.get(d.get("slot"), "수동")
    out = out_dir / f"{now.strftime('%Y-%m-%d_%H%M')}.png"
    try:
        if now.strftime("%H%M") >= "1600":
            return _evening(d, out, label)
        return _day(d, out, label)
    except Exception as e:
        logger.warning(f"시황 카드 렌더 실패 — 텍스트로 대신 보냄: {e}")
        return None
