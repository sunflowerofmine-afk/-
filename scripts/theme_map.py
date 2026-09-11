# scripts/theme_map.py
"""주도 테마 맵 — 시황 알림에 붙는 이미지 한 장.

테마 목록(266개)에서 **오른 종목이 내린 종목보다 많은 테마 중 등락률 상위 20개**를 고르고,
박스 크기 = 그 테마의 거래대금, 색 = 등락률(빨강 상승 / 파랑 하락)로 그린다.
"오른 종목 > 내린 종목" 조건이 없으면 삼성페이(26종목 중 14개 하락, 평균은 +2.3%)처럼
대형주 한둘이 평균을 끌어올린 통이 3.8조짜리 박스로 맵을 덮는다(2026-09-11 실측). 종목명은 넣지 않는다 — 시황 판단용이지
종목 선별용이 아니다(2026-09-11 사용자 결정).

제외: 종목 3개 미만 / 거래대금 300억 미만 / 지수형 묶음(밸류업·S7 등 — 삼성전자·하이닉스가
들어간 통에 크기만 크고 "테마"가 아니다).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.naver_api import fetch_group_list, to_float

logger = logging.getLogger(__name__)

TOP_N          = 20
MIN_STOCKS     = 3
MIN_TV_EOK     = 300
_INDEX_LIKE    = ("지수", "index", "s7", "코스피", "코스닥", "밸류업")

_FONT_CANDIDATES = [
    os.getenv("THEME_MAP_FONT", ""),
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",          # ubuntu: apt install fonts-nanum
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "C:/Windows/Fonts/malgun.ttf",                               # windows
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",                # mac
]


def _font():
    from matplotlib import font_manager
    for p in _FONT_CANDIDATES:
        if p and Path(p).exists():
            return font_manager.FontProperties(fname=p)
    logger.warning("한글 폰트를 못 찾았다 — 테마명이 깨질 수 있다")
    return font_manager.FontProperties()


def select_themes(themes: list[dict], top_n: int = TOP_N) -> list[dict]:
    """등락률 상위 top_n. 반환 각 항목: name, chg, tv_eok, n, rise, fall."""
    rows = []
    for t in themes:
        name = str(t.get("name") or "").strip()
        n    = int(to_float(t.get("totalCnt"), 0) or 0)
        tv   = (to_float(t.get("totalAccAmount"), 0) or 0) * 1000 / 1e8   # 천원 → 억
        chg  = to_float(t.get("changeRate"))
        if not name or chg is None or n < MIN_STOCKS or tv < MIN_TV_EOK:
            continue
        if any(k in name.lower() for k in _INDEX_LIKE):
            continue
        rise = int(to_float(t.get("riseCnt"), 0) or 0)
        fall = int(to_float(t.get("fallCnt"), 0) or 0)
        if rise <= fall:
            continue
        rows.append({"name": name, "chg": chg, "tv_eok": tv, "n": n,
                     "rise": rise, "fall": fall})
    rows.sort(key=lambda r: r["chg"], reverse=True)
    return rows[:top_n]


def _color(chg: float) -> tuple:
    """등락률 → 색. +8% 이상 진한 빨강, -8% 이하 진한 파랑, 0 근처 회백."""
    k = max(-1.0, min(1.0, chg / 8.0))
    if k >= 0:
        return (0.96, 0.92 - 0.62 * k, 0.90 - 0.66 * k)
    k = -k
    return (0.90 - 0.66 * k, 0.92 - 0.50 * k, 0.97)


def _fmt_tv(eok: float) -> str:
    return f"{eok/10000:.1f}조" if eok >= 10000 else f"{eok:,.0f}억"


def render(themes: list[dict], out_path: Path, title: str) -> Path | None:
    """treemap PNG 저장. 항목이 없으면 None."""
    rows = select_themes(themes)
    if not rows:
        logger.warning("테마 맵: 그릴 항목 없음")
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import squarify

    fp = _font()
    sizes = [r["tv_eok"] for r in rows]
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=130)
    rects = squarify.normalize_sizes(sizes, 100, 100)
    rects = squarify.squarify(rects, 0, 0, 100, 100)
    for r, rc in zip(rows, rects):
        ax.add_patch(plt.Rectangle((rc["x"], rc["y"]), rc["dx"], rc["dy"],
                                   facecolor=_color(r["chg"]), edgecolor="white", linewidth=2))
        dx, dy = rc["dx"], rc["dy"]
        # 줄 수는 박스 높이가, 글자 크기는 짧은 변이 정한다 — 넓고 납작한 박스에 3줄을 넣으면 넘친다
        max_chars = max(4, int(dx / 2.2))
        name = r["name"] if len(r["name"]) <= max_chars else r["name"][:max_chars - 1] + "…"
        nl = chr(10)
        if dy >= 14 and dx >= 18:
            label = f"{name}{nl}{r['chg']:+.1f}%  {_fmt_tv(r['tv_eok'])}{nl}{r['rise']}↑ {r['fall']}↓ / {r['n']}"
        elif dy >= 8:
            label = f"{name}{nl}{r['chg']:+.1f}%  {_fmt_tv(r['tv_eok'])}"
        else:
            label = f"{name} {r['chg']:+.1f}%"
        short = min(dx, dy)
        fs = 11 if short >= 18 else (9 if short >= 11 else 7)
        ax.text(rc["x"] + rc["dx"] / 2, rc["y"] + rc["dy"] / 2, label,
                ha="center", va="center", fontsize=fs, fontproperties=fp,
                color="black" if abs(r["chg"]) < 6 else "white")
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")
    ax.set_title(title, fontproperties=fp, fontsize=13, loc="left")
    fig.text(0.99, 0.01, "크기 = 거래대금 · 색 = 등락률 · 오른 종목이 더 많은 테마 중 등락률 상위 20 (거래대금 300억↑)",
             ha="right", va="bottom", fontsize=8, fontproperties=fp, color="#555")
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def build(out_path: Path, title: str) -> Path | None:
    """테마 목록을 받아 그린다. 실패 시 None (알림은 텍스트만 나간다)."""
    try:
        themes = fetch_group_list("theme")
    except Exception as e:
        logger.warning(f"테마 목록 수집 실패 — 맵 생략: {e}")
        return None
    try:
        return render(themes, out_path, title)
    except Exception as e:
        logger.warning(f"테마 맵 렌더 실패 — 맵 생략: {e}")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = build(Path("reports") / "theme_map_test.png", "테마 맵 테스트")
    print(p)
