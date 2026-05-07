# scripts/public_report.py
"""공개용 일일 한국 주식시장 리포트 HTML 생성"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SITE_TITLE = "한국주식 데이터 리포트"
_SITE_URL   = "https://stock-report-site.pages.dev"  # Cloudflare Pages 도메인 확정 후 변경

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;
     background:#f5f6fa;color:#1a1a2e;line-height:1.6;font-size:15px}
a{color:#2563eb;text-decoration:none}
a:hover{text-decoration:underline}
header{background:#1a1a2e;color:#fff;padding:16px 0}
.inner{max-width:900px;margin:0 auto;padding:0 16px}
header h1{font-size:.95rem;font-weight:400;opacity:.7}
header h2{font-size:1.45rem;font-weight:700;margin-top:2px}
.section{background:#fff;border-radius:10px;padding:20px;margin:14px 0;
         box-shadow:0 1px 4px rgba(0,0,0,.06)}
.section h3{font-size:.95rem;font-weight:700;margin-bottom:14px;
            border-left:3px solid #2563eb;padding-left:10px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:#f8f9fc;border-radius:8px;padding:14px;text-align:center}
.card .lbl{font-size:.75rem;color:#777;margin-bottom:4px}
.card .val{font-size:1.2rem;font-weight:700}
.card .sub{font-size:.78rem;margin-top:2px}
.pos{color:#e63946}.neg{color:#2563eb}.neu{color:#555}
.sec-card{border:1px solid #eee;border-radius:8px;padding:12px;margin-bottom:8px}
.sec-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:4px}
.sec-name{font-weight:600;font-size:.93rem}
.sec-meta{font-size:.8rem;color:#777}
.sec-stocks{display:flex;flex-wrap:wrap;gap:6px}
.stk{background:#eff6ff;color:#2563eb;padding:3px 9px;border-radius:12px;font-size:.78rem}
.stk:hover{background:#dbeafe}
table{width:100%;border-collapse:collapse;font-size:.87rem}
thead tr{background:#f0f4ff}
th{padding:8px 10px;text-align:left;font-weight:600;color:#444;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid #f0f0f0}
td.rk{color:#bbb;text-align:center;width:32px;font-size:.8rem}
td.mkt{color:#999;font-size:.78rem}
td.chg{font-weight:600}
td.tv{font-weight:500}
.tip{background:#fff7ed;border-left:4px solid #f59e0b;padding:10px 14px;
     border-radius:0 6px 6px 0;margin:10px 0;font-size:.84rem;color:#555;line-height:1.7}
.ctx{background:#eff6ff;border-left:4px solid #2563eb;padding:12px 16px;
     border-radius:0 8px 8px 0;font-size:.9rem;color:#333;line-height:1.75}
.disc{font-size:.77rem;color:#aaa;line-height:1.8}
footer{text-align:center;padding:24px 16px;font-size:.78rem;color:#bbb}
@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}}
"""

# ── 헬퍼 ──────────────────────────────────────────────────

def _sign(v) -> str:
    if v is None:
        return "-"
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def _tv_eok(won: float) -> str:
    if won >= 1_000_000_000_000:
        return f"{won / 1_000_000_000_000:.1f}조"
    return f"{won / 100_000_000:.0f}억"


def _tv_eok_from_eok(val: float) -> str:
    if val >= 10000:
        return f"{val / 10000:.1f}조"
    return f"{val:,.0f}억"


# ── HTML 조각 렌더러 ────────────────────────────────────────

def _render_cards(ms: dict) -> str:
    kospi  = ms.get("kospi_level") or 0
    kosdaq = ms.get("kosdaq_level") or 0
    kchg   = ms.get("kospi_chg")
    dchg   = ms.get("kosdaq_chg")
    regime = ms.get("market_regime", "")
    mtype  = ms.get("market_type", "")
    lu     = ms.get("limit_up_count", 0)
    ktv    = ms.get("kospi_tv_eok") or 0
    dtv    = ms.get("kosdaq_tv_eok") or 0

    regime_map = {"강세": ("강세", "pos"), "약세": ("약세", "neg"), "중립": ("중립", "neu")}
    rl, rc = regime_map.get(regime, (regime, "neu"))
    kc = "pos" if (kchg or 0) >= 0 else "neg"
    dc = "pos" if (dchg or 0) >= 0 else "neg"

    return f"""<div class="cards">
  <div class="card"><div class="lbl">KOSPI</div><div class="val">{kospi:,.0f}</div>
    <div class="sub {kc}">{_sign(kchg)}</div></div>
  <div class="card"><div class="lbl">KOSDAQ</div><div class="val">{kosdaq:,.0f}</div>
    <div class="sub {dc}">{_sign(dchg)}</div></div>
  <div class="card"><div class="lbl">장세 판단</div>
    <div class="val {rc}" style="font-size:.95rem">{rl}</div>
    <div class="sub neu" style="font-size:.72rem">{mtype[:18] if mtype else ''}</div></div>
  <div class="card"><div class="lbl">상한가</div>
    <div class="val pos">{lu}종목</div></div>
  <div class="card"><div class="lbl">KOSPI 거래대금</div>
    <div class="val" style="font-size:1rem">{_tv_eok_from_eok(ktv)}</div></div>
  <div class="card"><div class="lbl">KOSDAQ 거래대금</div>
    <div class="val" style="font-size:1rem">{_tv_eok_from_eok(dtv)}</div></div>
</div>"""


def _render_sectors(leading_sectors: list) -> str:
    if not leading_sectors:
        return "<p style='color:#aaa;font-size:.85rem'>섹터 데이터 없음</p>"
    parts = []
    for sec in leading_sectors[:5]:
        name    = sec.get("sector_name", "")
        tv      = sec.get("tv_eok", 0) or 0
        chg     = sec.get("change_pct", 0) or 0
        stocks  = sec.get("top_stocks", [])[:3]
        cc      = "pos" if chg >= 0 else "neg"
        stk_html = "".join(
            f'<a href="https://finance.naver.com/item/main.naver?code={s.get("종목코드","")}"'
            f' target="_blank" rel="noopener" class="stk">'
            f'{s.get("종목명","")} <span class="{"pos" if (s.get("등락률") or 0) >= 0 else "neg"}">'
            f'{_sign(s.get("등락률"))}</span></a>'
            for s in stocks
        )
        parts.append(
            f'<div class="sec-card">'
            f'<div class="sec-hd">'
            f'<span class="sec-name">{name}</span>'
            f'<span class="sec-meta">{_tv_eok_from_eok(tv)} &nbsp;·&nbsp; '
            f'<span class="{cc}">{_sign(chg)}</span></span></div>'
            f'<div class="sec-stocks">{stk_html}</div></div>'
        )
    return "\n".join(parts)


def _render_top_tv(records: list) -> str:
    if not records:
        return "<tr><td colspan='5' style='text-align:center;color:#aaa'>데이터 없음</td></tr>"
    rows = []
    for i, r in enumerate(records[:20], 1):
        name  = r.get("종목명", "")
        code  = r.get("종목코드", "")
        mkt   = r.get("시장", "")
        chg   = r.get("등락률") or 0
        tv    = r.get("거래대금") or 0
        cc    = "pos" if chg > 0 else ("neg" if chg < 0 else "")
        rows.append(
            f'<tr><td class="rk">{i}</td>'
            f'<td class="name"><a href="https://finance.naver.com/item/main.naver?code={code}"'
            f' target="_blank" rel="noopener">{name}</a></td>'
            f'<td class="mkt">{mkt}</td>'
            f'<td class="chg {cc}">{_sign(chg)}</td>'
            f'<td class="tv">{_tv_eok(tv)}</td></tr>'
        )
    return "\n".join(rows)


def _build_context(ms: dict, leading_sectors: list) -> str:
    regime  = ms.get("market_regime", "")
    sectors = [s["sector_name"] for s in leading_sectors[:3]]

    if regime == "강세":
        base = "오늘 시장은 전반적으로 강세를 보이며 상승 종목이 하락 종목을 크게 웃돌았습니다."
    elif regime == "약세":
        base = "오늘 시장은 전반적으로 약세를 보이며 하락 종목이 우세했습니다."
    else:
        base = "오늘 시장은 중립적인 흐름으로 상승·하락 종목이 혼재했습니다."

    if len(sectors) >= 2:
        names = "·".join(sectors)
        base += f" 주도 섹터는 {names} 등으로, 해당 업종에 거래대금이 집중됐습니다."

    return base


# ── 전체 리포트 렌더링 ─────────────────────────────────────

def _render_report(date_str: str, ms: dict, leading_sectors: list, top_tv_records: list) -> str:
    kospi  = ms.get("kospi_level") or 0
    kosdaq = ms.get("kosdaq_level") or 0
    regime = ms.get("market_regime", "")
    sectors_preview = "·".join(s["sector_name"] for s in leading_sectors[:3])
    meta_desc = (
        f"{date_str} 한국 주식시장 요약. 장세:{regime}, 주도섹터:{sectors_preview}. "
        f"KOSPI {kospi:,.0f} KOSDAQ {kosdaq:,.0f}. "
        "거래대금 상위 종목 및 섹터 분석."
    )

    cards_html   = _render_cards(ms)
    sectors_html = _render_sectors(leading_sectors)
    tv_rows      = _render_top_tv(top_tv_records)
    context      = _build_context(ms, leading_sectors)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{date_str} 한국 주식시장 거래대금·섹터 분석 | {_SITE_TITLE}</title>
<meta name="description" content="{meta_desc}">
<meta property="og:title" content="{date_str} 한국 주식시장 리포트">
<meta property="og:description" content="{meta_desc}">
<meta property="og:type" content="article">
<link rel="canonical" href="{_SITE_URL}/reports/{date_str}.html">
<style>{_CSS}</style>
</head>
<body>
<header>
<div class="inner">
  <h1>{_SITE_TITLE}</h1>
  <h2>{date_str} 일일 리포트</h2>
</div>
</header>
<div class="inner">

<div class="section">
  <h3>시장 요약</h3>
  {cards_html}
</div>

<div class="section">
  <h3>오늘의 주도 섹터</h3>
  {sectors_html}
  <div class="tip">거래대금이 특정 섹터에 집중될수록 상승 모멘텀이 강해지는 경향이 있습니다.
  섹터 내 여러 종목이 동반 상승하면 개별 급등보다 지속성이 높을 수 있습니다.</div>
</div>

<div class="section">
  <h3>거래대금 상위 20 종목</h3>
  <p style="font-size:.78rem;color:#aaa;margin-bottom:10px">출처: 네이버 금융 공개 데이터 기준</p>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>#</th><th>종목명</th><th>시장</th><th>등락률</th><th>거래대금</th></tr></thead>
    <tbody>{tv_rows}</tbody>
  </table>
  </div>
  <div class="tip">거래대금은 당일 해당 종목에 유입된 자금 규모를 나타냅니다.
  거래대금이 클수록 기관·외국인 등 대형 참여자의 관심이 높을 가능성이 있습니다.</div>
</div>

<div class="section">
  <h3>오늘의 시장 흐름</h3>
  <div class="ctx">{context}</div>
</div>

<div class="section">
  <p class="disc">
    ※ 본 리포트는 네이버 금융 등 공개 데이터를 정리한 정보 제공 목적의 자료로,
    특정 종목의 매수·매도를 권유하지 않습니다.<br>
    투자 결정은 본인의 판단과 책임 하에 이루어져야 하며, 과거 데이터는 미래 수익을 보장하지 않습니다.
  </p>
</div>

</div>
<footer>© {_SITE_TITLE} · 매 거래일 자동 업데이트 · 투자 정보 제공 목적 (투자 권유 아님)</footer>
</body>
</html>"""


# ── 인덱스 생성 ────────────────────────────────────────────

def generate_site_index(site_dir) -> None:
    """site_dir/reports/*.html 를 스캔해 site_dir/index.html 재생성"""
    site_dir = Path(site_dir)
    reports_dir = site_dir / "reports"
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")
    dates = sorted(
        [p.name.replace(".html", "") for p in reports_dir.glob("*.html") if pattern.match(p.name)],
        reverse=True,
    )

    rows = "\n".join(
        f'<tr><td class="dt">{d}</td>'
        f'<td><a href="reports/{d}.html">리포트 보기 →</a></td></tr>'
        for d in dates[:90]
    )

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>한국 주식시장 일일 데이터 리포트 | {_SITE_TITLE}</title>
<meta name="description" content="매 거래일 자동 생성되는 KOSPI·KOSDAQ 거래대금·섹터 분석 리포트 아카이브">
<link rel="canonical" href="{_SITE_URL}/">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;
     background:#f5f6fa;color:#1a1a2e;line-height:1.6}}
header{{background:#1a1a2e;color:#fff;padding:20px 16px;text-align:center}}
header h1{{font-size:1.4rem;font-weight:700}}
header p{{font-size:.88rem;opacity:.7;margin-top:4px}}
.wrap{{max-width:600px;margin:24px auto;padding:0 16px}}
.card{{background:#fff;border-radius:10px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.card h2{{font-size:.95rem;font-weight:700;margin-bottom:14px;
          border-left:3px solid #2563eb;padding-left:10px}}
table{{width:100%;border-collapse:collapse;font-size:.88rem}}
td{{padding:9px 10px;border-bottom:1px solid #f0f0f0}}
td.dt{{color:#888;width:120px}}
a{{color:#2563eb;text-decoration:none}}
a:hover{{text-decoration:underline}}
footer{{text-align:center;padding:20px;font-size:.78rem;color:#bbb}}
</style>
</head>
<body>
<header>
  <h1>{_SITE_TITLE}</h1>
  <p>매 거래일 자동 생성 · KOSPI·KOSDAQ 거래대금·섹터 분석</p>
</header>
<div class="wrap">
<div class="card">
  <h2>리포트 목록</h2>
  <table><tbody>{rows if rows else '<tr><td colspan="2" style="color:#aaa;text-align:center">리포트 없음</td></tr>'}</tbody></table>
</div>
</div>
<footer>© {_SITE_TITLE} · 투자 정보 제공 목적 (투자 권유 아님)</footer>
</body>
</html>"""

    (site_dir / "index.html").write_text(html, encoding="utf-8")
    logger.info(f"사이트 인덱스 생성: {site_dir}/index.html ({len(dates)}개 리포트)")


# ── 진입점 ────────────────────────────────────────────────

def run(
    report_date: str,
    market_summary: dict,
    leading_sectors: list,
    top_tv_records: list,
    output_dir: Path = Path("public_site"),
) -> bool:
    try:
        html = _render_report(report_date, market_summary, leading_sectors, top_tv_records)
        out  = output_dir / "reports" / f"{report_date}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        logger.info(f"공개 리포트 생성: {out}")
        return True
    except Exception as e:
        logger.error(f"공개 리포트 생성 실패: {e}")
        return False
