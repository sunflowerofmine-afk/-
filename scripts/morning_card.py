# scripts/morning_card.py
"""아침 브리핑 카드 한 장 — 위에서 아래로 판단 순서(갭 방향 → 미국 지수 → 유가·환율 → 이벤트 → 보유).

2026-11-05 사용자: "가독성이 좀 더 좋았으면 좋겠어. 화려한 이미지는 필요 없어. 다만 가독성이 최대한 좋게 시간을 아낄 수 있도록".
갭 칸 순서는 하이닉스 시초 갭과의 순위상관 기준(필라 반도체 0.62, 마이크론 0.60, EWY 0.49, n=484) + 준돌 장전 4대 매크로.
해석 문구 없이 숫자만. 그리는 데 실패하면 morning_briefing이 텍스트로 대신 보낸다.
"""
from __future__ import annotations

from pathlib import Path

from scripts.market_card import _Canvas, FLAT, INK, SUB, PANEL, W, _pct, _tone

_WD = ["월", "화", "수", "목", "금", "토", "일"]
_MAX_LINE = 46   # 글자 크기 11.5에서 한 줄에 들어가는 글자 수


def _tile(c, x, y, w, h, label, big, tone, size=20, sub="", sub_tone=SUB):
    c.box(x, y, w, h, PANEL)
    c.text(x + 2, y + 3.6, label, size=10.5, color=SUB)
    if sub:
        c.text(x + w - 2, y + h - 4.2, sub, size=12.5, color=sub_tone, ha="right", bold=True)
    c.text(x + 2, y + h - 4.2, big, size=size, color=tone, bold=True)


def _line(c, y, s, size=11.5, color=INK, bold=False):
    c.text(3, y, s if len(s) <= _MAX_LINE else s[:_MAX_LINE - 1] + "…", size=size, color=color, bold=bold)


def _pct_tile(c, x, y, w, h, label, v, size=20):
    _tile(c, x, y, w, h, label, _pct(v), _tone(v), size)


def build(d: dict, out: Path) -> Path:
    now = d["now"]
    c = _Canvas(260)
    c.rect(0, 0, W, 10, "#1b2230")
    c.text(3, 5, f"아침 브리핑  {now.month:02d}/{now.day:02d} ({_WD[now.weekday()]})", size=17, color="white", bold=True)
    c.text(97, 5, f"{now:%H:%M} 기준", size=11, color="#c9d1e0", ha="right")

    y = 15
    c.section(y, "갭 방향")
    y += 5
    nf = d.get("night") or {}
    tiles = [("코스피200 야간선물", nf.get("pct")), ("나스닥 선물", d.get("nq_fut")), ("필라 반도체", d.get("sox")),
             ("마이크론", d.get("MU")), ("SKHY", d.get("SKHY")), ("EWY", d.get("EWY"))]
    for i, (label, v) in enumerate(tiles):
        _pct_tile(c, 3 + 32 * (i % 3), y + 16 * (i // 3), 30, 14.5, label, v)
    y += 33
    if nf:
        c.text(3, y, f"야간선물 {nf['last']:,.2f} · 정규장 종가 {nf['day_close']:,.2f} 대비", size=9.5, color=SUB)
    else:
        c.text(3, y, "야간선물 조회 실패", size=9.5, color=SUB)

    y += 6
    c.section(y, "미국 전일 종가")
    y += 5
    for i, (label, v) in enumerate((("나스닥", d.get("nasdaq")), ("S&P500", d.get("sp500")),
                                    ("다우", d.get("dow")), ("샌디스크", d.get("SNDK")))):
        _pct_tile(c, 3 + 23.67 * i, y, 23, 11.5, label, v, size=14)
    y += 16

    c.section(y, "유가 · 환율 · 변동성")
    y += 5
    wti, krw, vix = d.get("wti"), d.get("usdkrw"), d.get("vix")
    _tile(c, 3, y, 30, 11.5, "WTI", f"{wti:,.2f}" if wti else "-", INK, 14, _pct(d.get("wti_pct")), _tone(d.get("wti_pct")))
    _tile(c, 35, y, 30, 11.5, "달러/원", f"{krw:,.0f}" if krw else "-", INK, 14, _pct(d.get("usdkrw_pct")), _tone(d.get("usdkrw_pct")))
    _tile(c, 67, y, 30, 11.5, "VIX", f"{vix:.1f}" if vix else "-", INK, 14, _pct(d.get("vix_pct")), _tone(d.get("vix_pct")))
    y += 16

    done, sched = d.get("done") or [], d.get("sched") or []
    if done or sched or d.get("macro_failed"):
        c.section(y, "이벤트")
        y += 5.5
        if done:
            c.text(3, y, "지난 브리핑 이후 실적", size=10, color=SUB)
            y += 4.6
            for e in done:
                _line(c, y, e)
                y += 5
        if sched:
            y += 0.8 if done else 0
            c.text(3, y, "다음 브리핑 전까지 (한국 시각)", size=10, color=SUB)
            y += 4.6
            for s in sched:
                _line(c, y, s)
                y += 5
        if d.get("macro_failed"):
            c.text(3, y, "지표 일정 조회 실패", size=10, color=SUB)
            y += 5
        y += 2

    pos = d.get("pos") or []
    c.section(y, f"보유 {len(pos)}건" if pos else "보유 없음")
    y += 5.5
    for p in pos:
        _line(c, y, p["head"], size=12.5, bold=True)
        y += 5.2
        for s in p["lines"]:
            _line(c, y, s)
            y += 5
    if pos and d.get("pre_open"):
        y += 0.5
        c.text(3, y, "08:00부터 08:50이 대응 창", size=11.5, color=FLAT, bold=True)
        y += 5
    return c.save(out, y + 1.5)
