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
    # 범례는 지도 밖(아래)에 — 지도 위에 겹치면 박스 글자를 가린다. bbox_inches="tight"가 잘라내지 않고 포함한다
    ax.text(100, -1.5, "크기 = 거래대금 · 색 = 등락률 · 오른 종목이 더 많은 테마 중 등락률 상위 20 (거래대금 300억↑)",
            ha="right", va="top", fontsize=8, fontproperties=fp, color="#555", clip_on=False)
    ax.text(100, -5, "2↑ 1↓ / 3 = 오른 종목 2 · 내린 종목 1 / 테마 전체 3종목 (나머지는 보합)",
            ha="right", va="top", fontsize=8, fontproperties=fp, color="#555", clip_on=False)
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


# ── 거래대금 중심 테마 히트맵 (14:20 앨범, 2026-10-25 사용자 결정) ─────────────────────────
# 주식시장 히트맵 형식: 테마 박스 안에 종목 박스, 크기 = 종목 KRX 거래대금, 색 = 등락률.
# 테마를 거래대금 순으로만 뽑으면 삼성전자·SK하이닉스가 들어간 테마가 상위를 다 차지한다(10-23 견본 실측)
# → 투탑은 맨 위 띠로 따로 두고, 아래는 투탑을 뺀 나머지로 거래대금이 큰 테마를 고른다.
# 한 종목이 여러 테마에 있으면 먼저 뽑힌 테마 한 곳에만 둔다. 구성 종목은 아침에 저장한 파일(theme_members),
# 등락률·거래대금은 그 시각 전 종목 시세 — 그 자리에서 구성 종목을 받으면 1분 넘게 늦는다.
HEAT_THEMES    = 8
HEAT_STOCKS    = 6
HEAT_CANDIDATE = 40
TWIN = {"005930": "삼성전자", "000660": "SK하이닉스"}

_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
]


def _font_bold():
    from matplotlib import font_manager
    for p in _BOLD_CANDIDATES:
        if Path(p).exists():
            return font_manager.FontProperties(fname=p)
    return _font()


def select_heat_groups(themes: list[dict], members: dict, quotes: dict) -> list[dict]:
    """[투탑 띠] + 테마 HEAT_THEMES개. quotes: 종목코드 → {name, chg, tv(억)}. members: 테마 no → 종목코드 목록.
    themes: 네이버 테마 목록(이름·전체 등락률·오른/내린 수). 조회 실패면 [] — 저장된 이름으로 그리고 테마 수치는 뺀다.

    테마 크기는 네이버 테마 거래대금이 아니라 '투탑과 이미 표시한 종목을 뺀 구성 종목 거래대금 합'으로 매번 다시 잰다.
    네이버 값으로 후보를 자르면 투탑이 든 테마가 후보를 다 차지한다(GPT 검토 10-25)."""
    info = {str(t.get("no")): t for t in themes}
    names = members.get("_names", {})
    cand = []
    for no, codes in members.items():
        if no == "_names":
            continue
        t = info.get(no, {})
        name = str(t.get("name") or names.get(no) or "")
        if not name or len(codes) < MIN_STOCKS or any(k in name.lower() for k in _INDEX_LIKE):
            continue
        cand.append({"no": no, "name": name, "chg": to_float(t.get("changeRate")) if t else None,
                     "rise": int(to_float(t.get("riseCnt"), 0) or 0), "fall": int(to_float(t.get("fallCnt"), 0) or 0)})

    def row(code):
        q = quotes.get(code)
        if not q or q.get("chg") is None or not q.get("tv"):
            return None
        return {"code": code, "name": q["name"], "chg": q["chg"], "tv": q["tv"]}

    twin = [r for r in (row(c) for c in TWIN) if r]
    groups = [{"name": "반도체 투탑", "stocks": twin, "size": sum(r["tv"] for r in twin)}] if twin else []
    used = set(TWIN)
    mem = {t["no"]: [r for r in (row(c) for c in dict.fromkeys(members[t["no"]])) if r] for t in cand}
    picked: set[str] = set()
    while len(picked) < HEAT_THEMES:
        best = None
        for t in cand:
            if t["no"] in picked:
                continue
            rows = [r for r in mem[t["no"]] if r["code"] not in used]
            size = sum(r["tv"] for r in rows)
            if len(rows) >= 2 and (best is None or size > best[1]):
                best = (t, size, rows)
        if not best:
            break
        t, _, rows = best
        picked.add(t["no"])
        top = sorted(rows, key=lambda r: -r["tv"])[:HEAT_STOCKS]
        used.update(r["code"] for r in top)      # 표시한 종목만 — 7위 아래 종목은 다른 테마에 나올 수 있다
        groups.append({**t, "stocks": top, "size": sum(r["tv"] for r in top)})
    return groups


def _fit(name: str, width: float, height: float, sizes=(10, 9, 8, 7, 6)) -> tuple[str, float]:
    for fs in sizes:
        if len(name) * fs * 0.145 <= width - 0.6 and fs * 0.3 <= height:
            return name, fs
    fs = sizes[-1]
    n = max(2, int((width - 0.6) / (fs * 0.145)))
    return (name if len(name) <= n else name[:n - 1] + "…"), fs


def render_heatmap(groups: list[dict], out_path: Path, title: str, basis: str) -> Path | None:
    twin = groups[0] if groups and groups[0]["name"] == "반도체 투탑" else None
    rest = [g for g in groups if g is not twin]
    if not rest:
        logger.warning("테마 히트맵: 그릴 테마 없음")
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import squarify

    fp, fpb = _font(), _font_bold()
    W_, H_ = 100, 74
    fig, ax = plt.subplots(figsize=(10, 7.4), dpi=135)
    TH = 10 if twin else 0
    if twin:
        tot = sum(s["tv"] for s in twin["stocks"])
        xw = 0
        for s in twin["stocks"]:
            w = W_ * s["tv"] / tot
            ax.add_patch(plt.Rectangle((xw, H_ - TH), w, TH, facecolor=_color(s["chg"]), edgecolor="white", linewidth=2.5))
            ax.text(xw + w / 2, H_ - TH / 2, f"{s['name']}  {s['chg']:+.1f}%  KRX 거래대금 {_fmt_tv(s['tv'])}",
                    ha="center", va="center", fontsize=11 if w >= 40 else 8, fontproperties=fpb,
                    color="white" if abs(s["chg"]) >= 6 else "black")
            xw += w
        ax.text(0.5, H_ + 0.6, "반도체 투탑 (크기 = 두 종목 KRX 거래대금 비율, 아래 지도와 축척 다름)",
                fontsize=8, fontproperties=fp, color="#555", va="bottom")
    HH = H_ - TH - (1.5 if twin else 0)
    outer = squarify.squarify(squarify.normalize_sizes([g["size"] for g in rest], W_, HH), 0, 0, W_, HH)
    for g, rc in zip(rest, outer):
        hdr = 3.2
        ax.add_patch(plt.Rectangle((rc["x"], rc["y"]), rc["dx"], rc["dy"], facecolor="#1b2230", edgecolor="white", linewidth=2.5))
        wide = rc["dx"] >= 24 and g.get("chg") is not None
        nm, fs = _fit(g["name"], rc["dx"] * (0.62 if wide else 1.0), hdr, (9, 8, 7, 6))
        ax.text(rc["x"] + 0.6, rc["y"] + rc["dy"] - hdr / 2, nm, fontsize=fs, fontproperties=fpb, color="white", va="center")
        if wide:
            ax.text(rc["x"] + rc["dx"] - 0.6, rc["y"] + rc["dy"] - hdr / 2, f"{g['chg']:+.1f}% {g['rise']}↑{g['fall']}↓",
                    fontsize=7.5, fontproperties=fp, color="#c9d1e0", va="center", ha="right")
        ix, iy, iw, ih = rc["x"] + 0.4, rc["y"] + 0.4, rc["dx"] - 0.8, rc["dy"] - hdr - 0.6
        if iw <= 0 or ih <= 0:
            continue
        inner = squarify.squarify(squarify.normalize_sizes([s["tv"] for s in g["stocks"]], iw, ih), ix, iy, iw, ih)
        for s, r2 in zip(g["stocks"], inner):
            ax.add_patch(plt.Rectangle((r2["x"], r2["y"]), r2["dx"], r2["dy"], facecolor=_color(s["chg"]), edgecolor="white", linewidth=1))
            nm, fs = _fit(s["name"], r2["dx"], r2["dy"] / 2)
            lab = nm + chr(10) + f"{s['chg']:+.1f}%" if r2["dy"] >= fs * 0.55 else nm
            ax.text(r2["x"] + r2["dx"] / 2, r2["y"] + r2["dy"] / 2, lab, ha="center", va="center", fontsize=fs,
                    fontproperties=fp, color="white" if abs(s["chg"]) >= 6 else "black")
    ax.set_xlim(0, W_); ax.set_ylim(0, H_); ax.axis("off")
    ax.set_title(title, fontproperties=fpb, fontsize=14, loc="left", pad=26 if twin else 8)
    ax.text(100, -1.2, f"투탑을 뺀 거래대금이 큰 테마 {len(rest)}개 · 테마마다 상위 {HEAT_STOCKS}종목 · "
                       f"크기 = 종목 KRX 거래대금 · 색 = 등락률({basis})",
            ha="right", va="top", fontsize=8, fontproperties=fp, color="#555", clip_on=False)
    ax.text(100, -4.2, "한 종목은 먼저 뽑힌 테마 한 곳에만 둔다(네이버 분류 기준, 등락 원인 아님) · "
                       "테마 이름 옆 숫자는 네이버 테마 전체 등락률·오른/내린 종목 수",
            ha="right", va="top", fontsize=8, fontproperties=fp, color="#555", clip_on=False)
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def build_heatmap(quotes: dict, out_path: Path, title: str, basis: str) -> Path | None:
    """저장된 구성 종목 + 지금 시세로 그린다. 구성 파일이 없거나 실패하면 None — 호출부가 예전 테마 맵으로 대신한다."""
    from scripts.theme_members import load
    cache = load()
    if not cache or not quotes:
        logger.warning("테마 히트맵 생략 — 구성 종목 파일 또는 시세 없음")
        return None
    try:
        members = {str(t["no"]): t["codes"] for t in cache["themes"]}
        members["_names"] = {str(t["no"]): t["name"] for t in cache["themes"]}
        try:
            themes = fetch_group_list("theme")
        except Exception as e:
            logger.warning(f"테마 목록 조회 실패 — 저장된 이름으로 그리고 테마 수치는 뺀다: {e}")
            themes = []
        groups = select_heat_groups(themes, members, quotes)
        return render_heatmap(groups, out_path, title, basis)
    except Exception as e:
        logger.warning(f"테마 히트맵 실패: {e}")
        return None
