# scripts/review_dashboard.py
"""
복기 대시보드 생성기.
사용법: python -m scripts.review_dashboard <csv_path> [--out <html_path>]
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.review_parser import parse_orders, calc_trades, summarize, Trade


# ── HTML 헬퍼 ─────────────────────────────────────────────

def _fmt_pnl(pnl: float | None) -> str:
    if pnl is None:
        return "-"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:,.0f}원"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "-"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _pnl_class(pnl: float | None) -> str:
    if pnl is None:
        return "neutral"
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "neutral"


def _note_badge(note: str) -> str:
    if note == "이전기간매수":
        return '<span class="badge badge-prev">이전기간</span>'
    if note == "미청산":
        return '<span class="badge badge-hold">보유중</span>'
    return ""


def _trade_row(t: Trade, idx: int) -> str:
    buy_info  = f"{t.buy_date or '-'}<br><small>{t.buy_time or ''}</small>"
    sell_info = f"{t.sell_date or '-'}<br><small>{t.sell_time or ''}</small>"
    buy_price  = f"{t.buy_price:,.0f}원"  if t.buy_price  else "-"
    sell_price = f"{t.sell_price:,.0f}원" if t.sell_price else "-"
    pnl_cls    = _pnl_class(t.pnl)
    note_html  = _note_badge(t.note)

    return f"""
    <tr class="{pnl_cls}">
      <td class="center">{idx}</td>
      <td><b>{t.stock_name}</b><br><small class="code">{t.stock_code}</small></td>
      <td class="center">{buy_info}</td>
      <td class="center">{buy_price}</td>
      <td class="center">{sell_info}</td>
      <td class="center">{sell_price}</td>
      <td class="center">{t.qty:,}주</td>
      <td class="center {pnl_cls}-text">{_fmt_pnl(t.pnl)}</td>
      <td class="center {pnl_cls}-text">{_fmt_pct(t.pnl_pct)}</td>
      <td class="center">{note_html}</td>
    </tr>"""


def generate_html(trades: list[Trade], csv_path: str) -> str:
    stats  = summarize(trades)
    closed = [t for t in trades if t.pnl is not None]
    others = [t for t in trades if t.pnl is None]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    csv_name = Path(csv_path).name

    # ── 요약 카드 ──────────────────────────────────────────
    if stats:
        total_pnl  = stats["total_pnl"]
        pnl_cls    = "win-text" if total_pnl >= 0 else "loss-text"
        summary_html = f"""
        <div class="summary-grid">
          <div class="card">
            <div class="card-label">총 손익</div>
            <div class="card-value {pnl_cls}">{_fmt_pnl(total_pnl)}</div>
          </div>
          <div class="card">
            <div class="card-label">거래 수</div>
            <div class="card-value">{stats['trade_count']}건</div>
          </div>
          <div class="card">
            <div class="card-label">승률</div>
            <div class="card-value">{stats['win_rate']}%
              <small>({stats['win_count']}승 {stats['loss_count']}패 {stats['even_count']}무)</small>
            </div>
          </div>
          <div class="card">
            <div class="card-label">평균 수익</div>
            <div class="card-value win-text">{_fmt_pnl(stats['avg_win'])}</div>
          </div>
          <div class="card">
            <div class="card-label">평균 손실</div>
            <div class="card-value loss-text">{_fmt_pnl(stats['avg_loss'])}</div>
          </div>
          <div class="card">
            <div class="card-label">최대 수익</div>
            <div class="card-value win-text">{_fmt_pnl(stats['best_trade'].pnl)}
              <small>({stats['best_trade'].stock_name})</small>
            </div>
          </div>
          <div class="card">
            <div class="card-label">최대 손실</div>
            <div class="card-value loss-text">{_fmt_pnl(stats['worst_trade'].pnl)}
              <small>({stats['worst_trade'].stock_name})</small>
            </div>
          </div>
        </div>

        <div class="stock-summary">
          <h3>종목별 손익</h3>
          <div class="stock-grid">
            {"".join(_stock_bar(name, pnl, stats["total_pnl"]) for name, pnl in stats["stock_pnl"])}
          </div>
        </div>"""
    else:
        summary_html = "<p>완결 거래 없음</p>"

    # ── 거래 테이블 ────────────────────────────────────────
    closed_rows = "".join(_trade_row(t, i + 1) for i, t in enumerate(closed))
    other_rows  = "".join(_trade_row(t, i + 1) for i, t in enumerate(others))

    other_section = ""
    if others:
        other_section = f"""
        <h2>미완결 거래 ({len(others)}건)</h2>
        <p class="note">이전 기간 매수 또는 아직 매도 전 보유 중인 종목</p>
        <table>
          <thead>
            <tr>
              <th>#</th><th>종목</th><th>매수일</th><th>매수단가</th>
              <th>매도일</th><th>매도단가</th><th>수량</th><th>손익</th><th>수익률</th><th>비고</th>
            </tr>
          </thead>
          <tbody>{other_rows}</tbody>
        </table>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>매매 복기 — {csv_name}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Malgun Gothic', -apple-system, sans-serif; background: #0f1117; color: #e2e8f0; font-size: 14px; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
    h2 {{ font-size: 16px; font-weight: 600; margin: 28px 0 12px; color: #94a3b8; }}
    h3 {{ font-size: 14px; font-weight: 600; margin: 16px 0 10px; color: #94a3b8; }}
    .meta {{ font-size: 12px; color: #64748b; margin-bottom: 24px; }}

    /* 요약 카드 */
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }}
    .card {{ background: #1e2130; border-radius: 8px; padding: 14px 16px; }}
    .card-label {{ font-size: 11px; color: #64748b; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .card-value {{ font-size: 18px; font-weight: 700; }}
    .card-value small {{ font-size: 11px; font-weight: 400; color: #94a3b8; display: block; margin-top: 2px; }}

    /* 종목별 바 */
    .stock-summary {{ background: #1e2130; border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
    .stock-grid {{ display: flex; flex-direction: column; gap: 8px; }}
    .stock-row {{ display: flex; align-items: center; gap: 10px; }}
    .stock-name {{ width: 120px; font-size: 13px; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .bar-wrap {{ flex: 1; background: #2d3148; border-radius: 4px; height: 20px; position: relative; overflow: hidden; }}
    .bar {{ height: 100%; border-radius: 4px; min-width: 2px; }}
    .bar-win  {{ background: #22c55e; }}
    .bar-loss {{ background: #ef4444; }}
    .stock-pnl {{ width: 110px; font-size: 13px; font-weight: 600; }}

    /* 색상 */
    .win-text  {{ color: #22c55e; }}
    .loss-text {{ color: #ef4444; }}
    .neutral   {{ }}

    /* 테이블 */
    table {{ width: 100%; border-collapse: collapse; background: #1e2130; border-radius: 8px; overflow: hidden; margin-bottom: 24px; }}
    thead tr {{ background: #2d3148; }}
    th {{ padding: 10px 12px; font-size: 12px; color: #94a3b8; text-align: left; font-weight: 600; }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #2d3148; font-size: 13px; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #252840; }}
    tr.win td {{ border-left: 3px solid #22c55e; }}
    tr.loss td {{ border-left: 3px solid #ef4444; }}
    tr.neutral td {{ border-left: 3px solid #475569; }}
    .center {{ text-align: center; }}
    .code {{ color: #64748b; }}
    small {{ font-size: 11px; color: #64748b; }}

    /* 배지 */
    .badge {{ font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 600; }}
    .badge-prev {{ background: #2d3148; color: #94a3b8; }}
    .badge-hold {{ background: #1d4ed8; color: #93c5fd; }}

    .note {{ font-size: 12px; color: #64748b; margin-bottom: 10px; }}
  </style>
</head>
<body>
<div class="container">
  <h1>매매 복기</h1>
  <div class="meta">파일: {csv_name} &nbsp;|&nbsp; 생성: {now_str} &nbsp;|&nbsp; ※ 수수료/세금 미반영</div>

  {summary_html}

  <h2>완결 거래 ({len(closed)}건)</h2>
  <table>
    <thead>
      <tr>
        <th>#</th><th>종목</th><th>매수일</th><th>매수단가</th>
        <th>매도일</th><th>매도단가</th><th>수량</th><th>손익</th><th>수익률</th><th>비고</th>
      </tr>
    </thead>
    <tbody>{closed_rows}</tbody>
  </table>

  {other_section}
</div>
</body>
</html>"""


def _stock_bar(name: str, pnl: float, total: float) -> str:
    max_abs = abs(total) if total != 0 else 1
    pct     = abs(pnl) / max_abs * 100
    width   = min(pct, 100)
    cls     = "bar-win" if pnl >= 0 else "bar-loss"
    pnl_cls = "win-text" if pnl >= 0 else "loss-text"
    sign    = "+" if pnl >= 0 else ""
    return f"""
    <div class="stock-row">
      <div class="stock-name">{name}</div>
      <div class="bar-wrap"><div class="bar {cls}" style="width:{width:.1f}%"></div></div>
      <div class="stock-pnl {pnl_cls}">{sign}{pnl:,.0f}원</div>
    </div>"""


# ── 메인 ──────────────────────────────────────────────────

def run(csv_path: str, out_path: str | None = None) -> str:
    orders = parse_orders(csv_path)
    trades = calc_trades(orders)
    html   = generate_html(trades, csv_path)

    if out_path is None:
        stem    = Path(csv_path).stem
        out_path = str(Path(csv_path).parent / f"review_{stem}.html")

    Path(out_path).write_text(html, encoding="utf-8")
    print(f"복기 리포트 생성: {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="매매 복기 HTML 리포트 생성")
    parser.add_argument("csv_path", help="키움 주문체결 CSV 파일 경로")
    parser.add_argument("--out", default=None, help="출력 HTML 경로 (기본: CSV와 같은 폴더)")
    args = parser.parse_args()
    run(args.csv_path, args.out)
