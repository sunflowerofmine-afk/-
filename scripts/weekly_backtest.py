"""scripts/weekly_backtest.py

주간 시스템 선별 후보 성과 백테스트.
지난 금요일 ~ 오늘(최대 목요일)의 2차/수동 신호 기준 D+1/D+2 수익률 집계.

Usage:
    python -m scripts.weekly_backtest
    python -m scripts.weekly_backtest --open
    python -m scripts.weekly_backtest --friday 2026-05-09
"""

import argparse
import html as _html_lib
import json
import logging
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fetch_stock_data import fetch_chart_data
from config.settings import REQUEST_DELAY, SIGNALS_DIR, REPORTS_DIR, GITHUB_PAGES_BASE_URL

logger = logging.getLogger(__name__)
_OUT_DIR = REPORTS_DIR / "weekly_backtest"
_OUT_DIR.mkdir(parents=True, exist_ok=True)
_HIST_FILE = Path("data/weekly_backtest/history.json")
_HIST_FILE.parent.mkdir(parents=True, exist_ok=True)

_GRADE_KR = {"BUY_REVIEW": "매수검토", "WATCH_ONLY": "관찰"}
_PATTERN_ORDER = ["당일돌파형", "고가수축형", "고가횡보형", "없음"]


# ── 날짜 유틸 ────────────────────────────────────────────────────────

def _period_start(today: date) -> date:
    """스크립트 기준 시작일: 오늘이 금요일이면 7일 전, 아니면 직전 금요일."""
    days_since = (today.weekday() - 4) % 7
    if days_since == 0:         # 오늘이 금요일 → 7일 전 금요일
        return today - timedelta(days=7)
    return today - timedelta(days=days_since)


# ── 데이터 로드 ──────────────────────────────────────────────────────

def _load_signals(date_str: str) -> pd.DataFrame | None:
    """해당 날짜 최신 signals.csv 로드 (2차/수동 우선, 없으면 최신)."""
    matches = sorted(Path(SIGNALS_DIR).glob(f"{date_str}_*_signals.csv"), reverse=True)
    for path in matches:
        try:
            df = pd.read_csv(path, dtype={"종목코드": str}, encoding="utf-8-sig")
            if not df.empty:
                return df
        except Exception as e:
            logger.warning(f"signals 로드 실패 {path}: {e}")
    return None


def _load_review(date_str: str) -> dict[str, dict]:
    """review.json → {code: row} 반환. 'pending' 항목 제외."""
    path = Path(SIGNALS_DIR) / f"{date_str}_review.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            str(r["code"]): r for r in data
            if r.get("result") not in ("pending", None)
            and r.get("d1_close_pct") is not None
        }
    except Exception as e:
        logger.warning(f"review.json 로드 실패 {path}: {e}")
        return {}


# ── 등급 계산 ────────────────────────────────────────────────────────

def _grade(pattern: str, in_inter: bool, base_gap) -> str:
    """signals CSV 기준 BUY_REVIEW / WATCH_ONLY 판별."""
    if pattern == "당일돌파형" and in_inter:
        return "BUY_REVIEW"
    if pattern == "고가수축형":
        return "BUY_REVIEW"
    if pattern == "고가횡보형":
        gap = float(base_gap) if pd.notna(base_gap) and base_gap is not None else 0.0
        if gap >= -5.0:
            return "BUY_REVIEW"
    return "WATCH_ONLY"


# ── 수익률 계산 ──────────────────────────────────────────────────────

def _pct(price, entry) -> float | None:
    try:
        p, e = float(price), float(entry)
        return round((p - e) / e * 100, 2) if p > 0 and e > 0 else None
    except (TypeError, ValueError):
        return None


def _fetch_returns(code: str, entry: float, signal_date_str: str) -> dict:
    """fetch_chart_data로 D+1~D+3 수익률 계산."""
    try:
        hist = fetch_chart_data(code)
        time.sleep(REQUEST_DELAY)
        if hist.empty:
            return {}
        sig_dot = signal_date_str.replace("-", ".")
        post = hist[hist["date"] > sig_dot].sort_values("date").reset_index(drop=True)

        def _v(idx, col):
            if idx >= len(post):
                return None
            try:
                v = float(post.iloc[idx][col])
                return v if v > 0 else None
            except (TypeError, ValueError, KeyError):
                return None

        result: dict = {}
        for i, day in enumerate(["d1", "d2", "d3"]):
            result[f"{day}_open_pct"]  = _pct(_v(i, "open"),  entry)
            result[f"{day}_high_pct"]  = _pct(_v(i, "high"),  entry)
            result[f"{day}_close_pct"] = _pct(_v(i, "close"), entry)

        mfe = mae = None
        for i in range(min(3, len(post))):
            h  = _pct(_v(i, "high"), entry)
            lo = _pct(_v(i, "low"),  entry)
            if h  is not None and (mfe is None or h  > mfe): mfe = h
            if lo is not None and (mae is None or lo < mae): mae = lo
        result["mfe"] = round(mfe, 2) if mfe is not None else None
        result["mae"] = round(mae, 2) if mae is not None else None
        return result
    except Exception as e:
        logger.warning(f"[{code}] 수익률 조회 실패: {e}")
        return {}


# ── 주간 데이터 수집 ─────────────────────────────────────────────────

def collect(start: date, end: date) -> list[dict]:
    """start ~ end 범위의 모든 signals 파일 스캔 (평일·주말 무관)."""
    # 범위 내 날짜별 최신 signals 파일 수집
    date_files: dict[str, Path] = {}
    for f in sorted(Path(SIGNALS_DIR).glob("*_signals.csv")):
        ds = f.name[:10]
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            continue
        if start <= d <= end:
            if ds not in date_files or f.name > date_files[ds].name:
                date_files[ds] = f

    rows: list[dict] = []
    cache: dict[tuple, dict] = {}  # (code, signal_date) → returns

    for date_str, path in sorted(date_files.items()):
        try:
            sig_df = pd.read_csv(path, dtype={"종목코드": str}, encoding="utf-8-sig")
        except Exception as e:
            logger.warning(f"로드 실패 {path}: {e}")
            continue
        if sig_df.empty:
            continue

        review_map = _load_review(date_str)
        logger.info(f"{date_str}: {len(sig_df)}개 후보 (review={'있음' if review_map else '없음'})")

        for _, row in sig_df.iterrows():
            code    = str(row.get("종목코드", "")).zfill(6)
            name    = str(row.get("종목명", ""))
            entry   = float(row.get("entry_reference_price") or row.get("signal_price") or 0)
            pattern = str(row.get("pattern_type_label") or "없음")
            in_inter = bool(row.get("in_inter", False))
            base_gap = row.get("base_high_gap_pct")
            if entry <= 0:
                continue

            gr = _grade(pattern, in_inter, base_gap)

            # 수익률: review.json 우선, 없으면 fetch
            if code in review_map:
                rv = review_map[code]
                ret = {
                    "d1_open_pct":  rv.get("d1_open_pct"),
                    "d1_close_pct": rv.get("d1_close_pct"),
                    "d2_close_pct": rv.get("d2_close_pct"),
                    "d3_close_pct": rv.get("d3_close_pct"),
                    "mfe":          rv.get("mfe"),
                    "mae":          rv.get("mae"),
                }
            else:
                key = (code, date_str)
                if key not in cache:
                    logger.info(f"  [{code}] {name} 가격 조회 중...")
                    cache[key] = _fetch_returns(code, entry, date_str)
                ret = cache[key]

            rows.append({
                "signal_date":  date_str,
                "code":         code,
                "name":         name,
                "market":       str(row.get("시장", "")),
                "change_pct":   float(row.get("등락률", 0) or 0),
                "trading_value":float(row.get("거래대금", 0) or 0),
                "entry_price":  entry,
                "pattern":      pattern,
                "grade":        gr,
                "in_inter":     in_inter,
                "total_score":  float(row.get("total_score", 0) or 0),
                "run_type":     str(row.get("run_type", "") or ""),
                **{k: ret.get(k) for k in ["d1_open_pct","d1_close_pct","d2_close_pct","d3_close_pct","mfe","mae"]},
            })

    return rows


# ── 통계 ─────────────────────────────────────────────────────────────

def _stats(rows: list[dict], key: str = "d1_close_pct") -> dict:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": len(rows), "valid": 0, "avg": None, "win_rate": None, "wins": 0}
    wins = sum(1 for v in vals if v > 0)
    return {
        "n":        len(rows),
        "valid":    len(vals),
        "avg":      round(sum(vals) / len(vals), 2),
        "win_rate": round(wins / len(vals) * 100, 1),
        "wins":     wins,
    }


# ── HTML 생성 ────────────────────────────────────────────────────────

_CSS = """
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;
  --text:#e6edf3;--muted:#8b949e;--green:#3fb950;--red:#f85149;
  --yellow:#d29922;--blue:#58a6ff;--purple:#bc8cff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:14px;padding:20px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:18px;font-weight:700;margin-bottom:4px}
.sub{color:var(--muted);font-size:12px;margin-bottom:20px}
h2{font-size:14px;font-weight:600;color:var(--blue);margin:24px 0 10px;
  border-bottom:1px solid var(--border);padding-bottom:6px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:14px 18px;min-width:160px;flex:1}
.card-label{font-size:11px;color:var(--muted);margin-bottom:6px}
.card-val{font-size:22px;font-weight:700}
.pos{color:var(--green)}.neg{color:var(--red)}.muted{color:var(--muted)}
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:var(--bg3);color:var(--muted);font-weight:500;padding:8px 10px;
  text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600}
.b-buy{background:#1c3a1c;color:var(--green)}
.b-watch{background:#1c2a3a;color:var(--blue)}
.b-inter{background:#2a1c3a;color:var(--purple)}
.b-pending{background:var(--bg3);color:var(--muted)}
.date-header{background:var(--bg3);font-weight:600;color:var(--muted);font-size:12px}
.name-col{font-weight:500}
"""

_e = _html_lib.escape


def _fmt(v, suffix="") -> str:
    if v is None:
        return '<span class="muted">-</span>'
    c = "pos" if float(v) > 0 else ("neg" if float(v) < 0 else "muted")
    sign = "+" if float(v) > 0 else ""
    return f'<span class="{c}">{sign}{v:.2f}{suffix}</span>'


def _card(label: str, val: str, cls: str = "") -> str:
    return (f'<div class="card">'
            f'<div class="card-label">{_e(label)}</div>'
            f'<div class="card-val {cls}">{val}</div>'
            f'</div>')


def _grade_badge(gr: str) -> str:
    if gr == "BUY_REVIEW":
        return '<span class="badge b-buy">매수검토</span>'
    return '<span class="badge b-watch">관찰</span>'


def _pattern_badge(pt: str) -> str:
    colors = {"당일돌파형": "#d29922", "고가수축형": "#3fb950", "고가횡보형": "#58a6ff"}
    c = colors.get(pt, "#8b949e")
    return f'<span style="color:{c};font-weight:600">{_e(pt)}</span>'


def _win_badge(v) -> str:
    if v is None:
        return '<span class="badge b-pending">대기중</span>'
    c, t = ("pos", "수익") if float(v) > 0 else ("neg", "손실")
    return f'<span class="{c}">{t}</span>'


def _stats_row(label: str, s: dict) -> str:
    avg_html = _fmt(s["avg"], "%") if s["avg"] is not None else '<span class="muted">-</span>'
    wr_html  = (f'<span class="{"pos" if s["win_rate"] >= 60 else ("neg" if s["win_rate"] < 40 else "")}">'
                f'{s["win_rate"]:.0f}%</span>'
                if s["win_rate"] is not None else '<span class="muted">-</span>')
    return (
        f"<tr><td>{_e(label)}</td>"
        f"<td>{s['n']}</td>"
        f"<td>{s['valid']}</td>"
        f"<td>{wr_html}</td>"
        f"<td>{avg_html}</td></tr>"
    )


def generate_html(rows: list[dict], start: date, today: date) -> str:
    total_s  = _stats(rows)
    buy_s    = _stats([r for r in rows if r["grade"] == "BUY_REVIEW"])
    watch_s  = _stats([r for r in rows if r["grade"] == "WATCH_ONLY"])
    inter_s  = _stats([r for r in rows if r["in_inter"]])

    def _wr_str(s):
        if s["win_rate"] is None: return '<span class="muted">-</span>'
        c = "pos" if s["win_rate"] >= 60 else ("neg" if s["win_rate"] < 40 else "")
        return f'<span class="{c}">{s["win_rate"]:.0f}%</span>'

    # 요약 카드
    cards_html = "".join([
        _card("총 후보", str(total_s["n"])),
        _card("D+1 승률",
              f'{total_s["win_rate"]:.0f}%' if total_s["win_rate"] is not None else "-",
              "pos" if (total_s["win_rate"] or 0) >= 50 else "neg"),
        _card("D+1 평균 수익률",
              f'{"+{:.2f}".format(total_s["avg"]) if (total_s["avg"] or 0) > 0 else "{:.2f}".format(total_s["avg"] or 0)}%'
              if total_s["avg"] is not None else "-",
              "pos" if (total_s["avg"] or 0) > 0 else "neg"),
        _card("매수검토 승률",
              f'{buy_s["win_rate"]:.0f}%' if buy_s["win_rate"] is not None else "-",
              "pos" if (buy_s["win_rate"] or 0) >= 50 else "neg"),
        _card("교집합 승률",
              f'{inter_s["win_rate"]:.0f}%' if inter_s["win_rate"] is not None else "-",
              "pos" if (inter_s["win_rate"] or 0) >= 50 else "neg"),
    ])

    # 패턴별 통계
    pat_rows_html = ""
    for pt in _PATTERN_ORDER:
        sub = [r for r in rows if r["pattern"] == pt]
        if not sub:
            continue
        s = _stats(sub)
        pat_rows_html += _stats_row(pt, s)

    # 등급별 통계
    grade_rows_html = (
        _stats_row("매수검토 (BUY_REVIEW)", buy_s)
        + _stats_row("관찰 (WATCH_ONLY)", watch_s)
        + _stats_row("교집합 ★", inter_s)
    )

    # 날짜별 + 종목별 상세
    detail_html = ""
    dates = sorted(set(r["signal_date"] for r in rows))
    for ds in dates:
        day_rows = [r for r in rows if r["signal_date"] == ds]
        detail_html += f'<tr class="date-header"><td colspan="10">{_e(ds)} ({len(day_rows)}종목)</td></tr>'
        for r in day_rows:
            tv_eok = f'{r["trading_value"]/1e8:.0f}억' if r["trading_value"] > 0 else "-"
            detail_html += (
                f"<tr>"
                f'<td class="name-col">{_e(r["name"])}'
                f'{"&nbsp;<span class=\'badge b-inter\'>★</span>" if r["in_inter"] else ""}'
                f'<br><small class="muted">{_e(r["code"])}·{_e(r["market"])}</small></td>'
                f"<td>{_grade_badge(r['grade'])}</td>"
                f"<td>{_pattern_badge(r['pattern'])}</td>"
                f'<td class="{"pos" if r["change_pct"]>0 else "neg"}">'
                f'{"+" if r["change_pct"]>0 else ""}{r["change_pct"]:.2f}%</td>'
                f"<td>{tv_eok}</td>"
                f"<td>{_fmt(r['d1_open_pct'], '%')}</td>"
                f"<td>{_fmt(r['d1_close_pct'], '%')}</td>"
                f"<td>{_fmt(r['d2_close_pct'], '%')}</td>"
                f"<td>{_fmt(r['mfe'], '%')}</td>"
                f"<td>{_fmt(r['mae'], '%')}</td>"
                f"</tr>"
            )

    stats_thead = (
        "<tr><th>구분</th><th>후보 수</th><th>수익률 확인</th>"
        "<th>D+1 승률</th><th>D+1 평균</th></tr>"
    )
    detail_thead = (
        "<tr><th>종목</th><th>등급</th><th>패턴</th><th>당일등락</th><th>거래대금</th>"
        "<th>D+1 시가</th><th>D+1 종가</th><th>D+2 종가</th><th>MFE</th><th>MAE</th></tr>"
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>주간 백테스트 {start} ~ {today}</title>
<style>{_CSS}</style>
</head>
<body><div class="wrap">
<h1>📊 주간 시스템 백테스트</h1>
<div class="sub">{start} ~ {today} &nbsp;·&nbsp; 종가베팅 D+1/D+2 수익률</div>
<div class="cards">{cards_html}</div>

<h2>등급·패턴별 성과 (D+1 종가 기준)</h2>
<div class="tbl-wrap"><table>
{stats_thead}
{grade_rows_html}
<tr style="border-top:2px solid var(--border)"></tr>
{pat_rows_html}
</table></div>

<h2>종목별 상세</h2>
<div class="tbl-wrap"><table>
{detail_thead}
{detail_html}
</table></div>

<p style="font-size:11px;color:var(--muted);margin-top:16px">
생성: {today} &nbsp;·&nbsp; 진입가 = entry_reference_price (regular_close 우선) &nbsp;·&nbsp; 수익률 미확인 = 가격 조회 중
</p>
</div></body></html>"""


# ── 히스토리 저장 ────────────────────────────────────────────────────

def _save_history(start: date, rows: list[dict]) -> None:
    s = _stats(rows)
    buy_s = _stats([r for r in rows if r["grade"] == "BUY_REVIEW"])
    record = {
        "week_start":       start.isoformat(),
        "n":                s["n"],
        "d1_win_rate":      s["win_rate"],
        "d1_avg":           s["avg"],
        "buy_review_n":     buy_s["n"],
        "buy_review_win_rate": buy_s["win_rate"],
        "buy_review_avg":   buy_s["avg"],
    }
    history = []
    if _HIST_FILE.exists():
        try:
            history = json.loads(_HIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 같은 주 기록은 덮어쓰기
    history = [h for h in history if h.get("week_start") != start.isoformat()]
    history.append(record)
    _HIST_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"히스토리 저장: {_HIST_FILE}")


# ── 텔레그램 알림 ────────────────────────────────────────────────────

def _fp(v) -> str:
    if v is None:
        return "-"
    sign = "+" if float(v) > 0 else ""
    return f"{sign}{v:.1f}%"


def _fw(s: dict) -> str:
    if s["win_rate"] is None:
        return "-"
    return f"{s['win_rate']:.0f}%"


def notify_telegram(rows: list[dict], start: date, today: date, html_filename: str) -> None:
    try:
        from scripts.notifier import send_message
    except Exception as e:
        logger.warning(f"텔레그램 import 실패 (발송 skip): {e}")
        return

    total_s = _stats(rows)
    buy_s   = _stats([r for r in rows if r["grade"] == "BUY_REVIEW"])
    watch_s = _stats([r for r in rows if r["grade"] == "WATCH_ONLY"])
    inter_s = _stats([r for r in rows if r["in_inter"]])

    # 패턴별 (후보 있는 것만)
    pat_lines = []
    for pt in _PATTERN_ORDER:
        sub = [r for r in rows if r["pattern"] == pt]
        if not sub:
            continue
        s = _stats(sub)
        pat_lines.append(f"  {pt}: {s['n']}종목 | {_fw(s)} | {_fp(s['avg'])}")

    # 종목별 한 줄 요약 (날짜·이름·D+1)
    stock_lines = []
    for ds in sorted(set(r["signal_date"] for r in rows)):
        day_rows = [r for r in rows if r["signal_date"] == ds]
        stock_lines.append(f"[{ds}]")
        for r in day_rows:
            inter_mark = "★" if r["in_inter"] else " "
            d1 = _fp(r.get("d1_close_pct"))
            stock_lines.append(
                f"  {inter_mark}{r['name'][:7]} "
                f"({r['pattern'][:4]}) D+1 {d1}"
            )

    # 대시보드 링크
    link = ""
    if GITHUB_PAGES_BASE_URL:
        base = GITHUB_PAGES_BASE_URL.rstrip("/")
        link = f"\n🔗 대시보드\n{base}/reports/weekly_backtest/{html_filename}"

    msg = (
        f"📊 주간 백테스트 ({start} ~ {today})\n\n"
        f"총 {total_s['n']}종목 · D+1 승률 {_fw(total_s)} · 평균 {_fp(total_s['avg'])}\n\n"
        f"■ 등급별\n"
        f"  매수검토: {buy_s['n']}종목 | {_fw(buy_s)} | {_fp(buy_s['avg'])}\n"
        f"  관찰: {watch_s['n']}종목 | {_fw(watch_s)} | {_fp(watch_s['avg'])}\n"
        f"  ★ 교집합: {inter_s['n']}종목 | {_fw(inter_s)} | {_fp(inter_s['avg'])}\n\n"
        f"■ 패턴별\n" + "\n".join(pat_lines) + "\n\n"
        f"■ 종목별\n" + "\n".join(stock_lines)
        + link
    )

    ok = send_message(msg)
    logger.info(f"텔레그램 발송: {'성공' if ok else '실패'}")


# ── 진입점 ───────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("--friday", help="기준 금요일 (YYYY-MM-DD). 기본: 가장 최근 금요일")
    parser.add_argument("--open", action="store_true", help="생성 후 브라우저 열기")
    parser.add_argument("--no-notify", action="store_true", help="텔레그램 발송 skip")
    args = parser.parse_args()

    today = date.today()
    start = (
        date.fromisoformat(args.friday) if args.friday else _period_start(today)
    )

    logger.info(f"주간 백테스트: {start} ~ {today}")
    rows = collect(start, today)

    if not rows:
        logger.error("수집된 후보 없음. 신호 파일 확인 필요.")
        sys.exit(1)

    html = generate_html(rows, start, today)
    out_name = f"weekly_backtest_{start}.html"
    out_path = _OUT_DIR / out_name
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"저장: {out_path}")

    _save_history(start, rows)

    if not args.no_notify:
        notify_telegram(rows, start, today, out_name)

    if args.open:
        try:
            subprocess.Popen(["start", "", str(out_path)], shell=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
