#!/usr/bin/env python3
"""
당일돌파형 홀딩 기간별 수익률 분석.
1750 시그널 전수 → yfinance D+1~D+5 종가 조회 → 홀딩 수익률 분포 HTML 리포트.

사용법:
    python -m scripts.holding_analysis
    python -m scripts.holding_analysis --open
"""

import sys
import argparse
import webbrowser
from datetime import datetime, timedelta, date as date_t
from pathlib import Path
from html import escape as _e

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import SIGNALS_DIR, REPORTS_DIR


# ── 시그널 로드 ───────────────────────────────────────────────────────

def _parse_date(fname: str) -> date_t | None:
    try:
        return datetime.strptime(fname[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_all_1750_signals() -> dict[date_t, pd.DataFrame]:
    """전체 기간 1750 시그널 로드. 날짜별 가장 늦은 파일 1개."""
    by_date: dict[date_t, list[Path]] = {}
    for f in sorted(SIGNALS_DIR.glob("*_1750_signals.csv")):
        d = _parse_date(f.name)
        if d:
            by_date.setdefault(d, []).append(f)
    result: dict[date_t, pd.DataFrame] = {}
    for d, files in by_date.items():
        f = sorted(files)[-1]
        try:
            df = pd.read_csv(f, encoding="utf-8-sig")
            df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
            result[d] = df
        except Exception:
            pass
    return result


def collect_p1_entries(signals: dict[date_t, pd.DataFrame]) -> list[dict]:
    """당일돌파형 포함 종목 추출. pattern_type_label에 '당일돌파형' 포함 기준."""
    entries = []
    for d, df in sorted(signals.items()):
        for _, row in df.iterrows():
            label = str(row.get("pattern_type_label", "") or "")
            if "당일돌파형" not in label:
                continue
            entry_price = float(row.get("entry_reference_price") or
                                row.get("signal_price") or 0)
            if entry_price <= 0:
                continue
            entries.append({
                "signal_date":  d,
                "code":         str(row.get("종목코드", "")).zfill(6),
                "name":         str(row.get("종목명", "")),
                "market":       str(row.get("시장", "KOSPI")),
                "entry_price":  entry_price,
                "change_pct":   float(row.get("등락률", 0) or 0),
                "tv_eok":       float(row.get("거래대금", 0) or 0) / 1e8,
                "in_inter":     bool(row.get("in_inter", False)),
                "sector":       str(row.get("sector", "") or ""),
                "news":         str(row.get("news_summary", "") or ""),
                "pattern_label": label,
            })
    return entries


# ── yfinance 조회 ────────────────────────────────────────────────────

def fetch_forward(code: str, market: str, signal_date: date_t) -> dict[str, float | None]:
    """D+1~D+5 시가/종가 조회. 반환: {d1_open, d1_close, d2_close, d3_close, d4_close, d5_close}"""
    try:
        import yfinance as yf
        suffix = ".KS" if market == "KOSPI" else ".KQ"
        start  = signal_date + timedelta(days=1)
        end    = signal_date + timedelta(days=10)   # 여유 있게
        hist   = yf.Ticker(code + suffix).history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
        )
        if hist.empty:
            return {}
        hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
        hist       = hist.sort_index()
        rows       = [(i.date(), row) for i, row in hist.iterrows()]
        result = {}
        if len(rows) >= 1:
            result["d1_open"]  = float(rows[0][1]["Open"])
            result["d1_close"] = float(rows[0][1]["Close"])
        if len(rows) >= 2: result["d2_close"] = float(rows[1][1]["Close"])
        if len(rows) >= 3: result["d3_close"] = float(rows[2][1]["Close"])
        if len(rows) >= 4: result["d4_close"] = float(rows[3][1]["Close"])
        if len(rows) >= 5: result["d5_close"] = float(rows[4][1]["Close"])
        return result
    except Exception:
        return {}


def _ret(price: float | None, entry: float) -> float | None:
    if price is None or entry <= 0:
        return None
    return (price - entry) / entry * 100


# ── 통계 헬퍼 ────────────────────────────────────────────────────────

def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "win": "-", "avg": "-", "med": "-", "min": "-", "max": "-"}
    n    = len(vals)
    wins = sum(1 for v in vals if v > 0)
    return {
        "n":   n,
        "win": f"{wins/n*100:.0f}%",
        "avg": f"{sum(vals)/n:+.2f}%",
        "med": f"{sorted(vals)[n//2]:+.2f}%",
        "min": f"{min(vals):+.2f}%",
        "max": f"{max(vals):+.2f}%",
    }


# ── HTML 렌더링 ──────────────────────────────────────────────────────

_CSS = """
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;
  --text:#e6edf3;--muted:#8b949e;--green:#3fb950;--red:#f85149;
  --yellow:#d29922;--blue:#58a6ff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:13px;padding:20px}
.wrap{max-width:1200px;margin:0 auto}
h1{font-size:18px;font-weight:700;margin-bottom:4px}
.sub{color:var(--muted);font-size:12px;margin-bottom:24px}
h2{font-size:14px;font-weight:600;color:var(--blue);
  margin:28px 0 10px;border-bottom:1px solid var(--border);padding-bottom:6px}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:14px 18px;min-width:140px;flex:1}
.card-label{font-size:11px;color:var(--muted);margin-bottom:4px}
.card-val{font-size:20px;font-weight:700}
.tbl-wrap{overflow-x:auto;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--bg3);color:var(--muted);font-weight:500;
  padding:7px 10px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
.pos{color:var(--green);font-weight:600}
.neg{color:var(--red);font-weight:600}
.muted{color:var(--muted)}
.inter{color:#bc8cff;font-weight:700}
.badge{display:inline-block;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600}
.b-inter{background:#2a1c3a;color:#bc8cff}
.b-ok{background:#1c3a1c;color:var(--green)}
.notice{background:var(--bg2);border:1px solid var(--border);border-radius:6px;
  padding:10px 14px;font-size:12px;color:var(--muted);margin-bottom:20px;line-height:1.6}
"""

def _pct_td(v: float | None) -> str:
    if v is None:
        return '<td class="muted">-</td>'
    cls = "pos" if v > 0 else ("neg" if v < 0 else "muted")
    return f'<td class="{cls}">{v:+.2f}%</td>'


def _stat_row(label: str, s: dict, highlight: bool = False) -> str:
    style = ' style="background:#1a2030"' if highlight else ""
    def _v(k):
        v = s.get(k, "-")
        if isinstance(v, str) and v not in ("-",):
            try:
                num = float(v.replace("%","").replace("+",""))
                cls = "pos" if num > 0 else ("neg" if num < 0 else "muted")
                return f'<span class="{cls}">{v}</span>'
            except Exception:
                pass
        return f'<span class="muted">{v}</span>'
    return (
        f'<tr{style}>'
        f'<td><b>{_e(label)}</b></td>'
        f'<td>{s.get("n","0")}건</td>'
        f'<td>{s.get("win","-")}</td>'
        f'<td>{_v("avg")}</td>'
        f'<td>{_v("med")}</td>'
        f'<td>{_v("min")}</td>'
        f'<td>{_v("max")}</td>'
        f'</tr>'
    )


def build_html(entries: list[dict], today: date_t) -> str:
    total = len(entries)
    inter_n = sum(1 for e in entries if e["in_inter"])

    # ── 출구별 수익률 수집 ──────────────────────────────
    col_keys = ["d1_open", "d1_close", "d2_close", "d3_close", "d4_close", "d5_close"]
    col_labels = ["D+1 시가(갭)", "D+1 종가", "D+2 종가", "D+3 종가", "D+4 종가", "D+5 종가"]

    all_rets: dict[str, list[float]] = {k: [] for k in col_keys}
    inter_rets: dict[str, list[float]] = {k: [] for k in col_keys}
    non_inter_rets: dict[str, list[float]] = {k: [] for k in col_keys}

    # 등락률 구간 분류
    tier_rets: dict[str, dict[str, list[float]]] = {
        "10~15%": {k: [] for k in col_keys},
        "15~20%": {k: [] for k in col_keys},
        "20%+":   {k: [] for k in col_keys},
    }

    for e in entries:
        chg = e["change_pct"]
        if 10 <= chg < 15:   tier = "10~15%"
        elif 15 <= chg < 20: tier = "15~20%"
        elif chg >= 20:      tier = "20%+"
        else:                tier = None

        for k in col_keys:
            r = e.get(f"ret_{k}")
            if r is None:
                continue
            all_rets[k].append(r)
            if e["in_inter"]:
                inter_rets[k].append(r)
            else:
                non_inter_rets[k].append(r)
            if tier:
                tier_rets[tier][k].append(r)

    # ── 요약 카드 ──────────────────────────────────────
    d1_vals = all_rets["d1_close"]
    d2_vals = all_rets["d2_close"]
    d1_win  = f"{sum(1 for v in d1_vals if v>0)/len(d1_vals)*100:.0f}%" if d1_vals else "-"
    d2_win  = f"{sum(1 for v in d2_vals if v>0)/len(d2_vals)*100:.0f}%" if d2_vals else "-"
    d1_avg  = f"{sum(d1_vals)/len(d1_vals):+.2f}%" if d1_vals else "-"
    d2_avg  = f"{sum(d2_vals)/len(d2_vals):+.2f}%" if d2_vals else "-"

    cards = (
        f'<div class="cards">'
        f'<div class="card"><div class="card-label">분석 대상</div>'
        f'<div class="card-val">{total}건</div></div>'
        f'<div class="card"><div class="card-label">교집합</div>'
        f'<div class="card-val inter">{inter_n}건</div></div>'
        f'<div class="card"><div class="card-label">D+1 종가 승률</div>'
        f'<div class="card-val">{d1_win}</div></div>'
        f'<div class="card"><div class="card-label">D+1 종가 평균</div>'
        f'<div class="card-val">{d1_avg}</div></div>'
        f'<div class="card"><div class="card-label">D+2 종가 승률</div>'
        f'<div class="card-val">{d2_win}</div></div>'
        f'<div class="card"><div class="card-label">D+2 종가 평균</div>'
        f'<div class="card-val">{d2_avg}</div></div>'
        f'</div>'
    )

    # ── 출구별 통계 테이블 ─────────────────────────────
    stat_header = (
        '<table><thead><tr>'
        '<th>출구 시점</th><th>건수</th><th>승률</th>'
        '<th>평균</th><th>중위</th><th>최소</th><th>최대</th>'
        '</tr></thead><tbody>'
    )
    stat_rows = ""
    for k, lbl in zip(col_keys, col_labels):
        s = _stats(all_rets[k])
        highlight = k in ("d1_close", "d2_close")
        stat_rows += _stat_row(lbl, s, highlight)
    stat_table = stat_header + stat_rows + "</tbody></table>"

    # ── 교집합 vs 비교집합 ─────────────────────────────
    seg_header = (
        '<table><thead><tr>'
        '<th>구분</th><th>출구</th><th>건수</th><th>승률</th>'
        '<th>평균</th><th>중위</th><th>최소</th><th>최대</th>'
        '</tr></thead><tbody>'
    )
    seg_rows = ""
    for label, rets in [("★교집합", inter_rets), ("비교집합", non_inter_rets)]:
        for k, lbl in [("d1_close","D+1"), ("d2_close","D+2"), ("d3_close","D+3")]:
            s = _stats(rets[k])
            seg_rows += (
                f'<tr><td><b>{_e(label)}</b></td><td>{_e(lbl)}</td>'
                f'<td>{s.get("n","0")}건</td><td>{s.get("win","-")}</td>'
                f'<td>{s.get("avg","-")}</td><td>{s.get("med","-")}</td>'
                f'<td>{s.get("min","-")}</td><td>{s.get("max","-")}</td></tr>'
            )
    seg_table = seg_header + seg_rows + "</tbody></table>"

    # ── 당일 등락률 구간별 ─────────────────────────────
    tier_header = (
        '<table><thead><tr>'
        '<th>당일 등락률</th><th>출구</th><th>건수</th><th>승률</th>'
        '<th>평균</th><th>중위</th><th>최소</th><th>최대</th>'
        '</tr></thead><tbody>'
    )
    tier_rows = ""
    for tier_label in ["10~15%", "15~20%", "20%+"]:
        for k, lbl in [("d1_close","D+1"), ("d2_close","D+2"), ("d3_close","D+3")]:
            s = _stats(tier_rets[tier_label][k])
            tier_rows += (
                f'<tr><td><b>{_e(tier_label)}</b></td><td>{_e(lbl)}</td>'
                f'<td>{s.get("n","0")}건</td><td>{s.get("win","-")}</td>'
                f'<td>{s.get("avg","-")}</td><td>{s.get("med","-")}</td>'
                f'<td>{s.get("min","-")}</td><td>{s.get("max","-")}</td></tr>'
            )
    tier_table = tier_header + tier_rows + "</tbody></table>"

    # ── 종목별 상세 테이블 ─────────────────────────────
    detail_rows = ""
    for e in sorted(entries, key=lambda x: x["signal_date"], reverse=True):
        inter_badge = '<span class="badge b-inter">★</span> ' if e["in_inter"] else ""
        detail_rows += (
            f'<tr>'
            f'<td>{e["signal_date"]}</td>'
            f'<td>{inter_badge}{_e(e["name"])}<br>'
            f'<span class="muted">{_e(e["code"])} · {_e(e["market"])}</span></td>'
            f'<td><span class="{"pos" if e["change_pct"]>0 else "neg"}">'
            f'{e["change_pct"]:+.1f}%</span></td>'
            f'<td>{e["tv_eok"]:.0f}억</td>'
            + _pct_td(e.get("ret_d1_open"))
            + _pct_td(e.get("ret_d1_close"))
            + _pct_td(e.get("ret_d2_close"))
            + _pct_td(e.get("ret_d3_close"))
            + _pct_td(e.get("ret_d4_close"))
            + _pct_td(e.get("ret_d5_close"))
            + f'<td class="muted" style="font-size:11px">{_e(e["sector"][:12] if e["sector"] else "")}</td>'
            f'</tr>'
        )

    detail_table = (
        '<table><thead><tr>'
        '<th>날짜</th><th>종목</th><th>당일등락</th><th>거래대금</th>'
        '<th>D+1시가</th><th>D+1종가</th><th>D+2종가</th>'
        '<th>D+3종가</th><th>D+4종가</th><th>D+5종가</th><th>섹터</th>'
        '</tr></thead>'
        f'<tbody>{detail_rows}</tbody></table>'
    )

    notice = (
        '<div class="notice">'
        '⚠️ 진입가 = entry_reference_price (장마감 종가 기준) · '
        '수익률 = (D+N 종가 − 진입가) / 진입가 · '
        f'분석 기준일: {today} · yfinance 데이터(배당수정가 기준) · '
        '모수 부족으로 통계적 결론보다 방향성 참고용'
        '</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>당일돌파형 홀딩 분석</title>
<style>{_CSS}</style>
</head><body><div class="wrap">
<h1>📊 당일돌파형 홀딩 기간별 수익률 분석</h1>
<div class="sub">1750 시그널 전수 · D+1 종가 청산 vs 홀딩 연장 비교</div>
{notice}
{cards}

<h2>출구 시점별 전체 통계</h2>
<div class="tbl-wrap">{stat_table}</div>

<h2>교집합 vs 비교집합 비교 (D+1~D+3)</h2>
<div class="tbl-wrap">{seg_table}</div>

<h2>당일 등락률 구간별 (D+1~D+3)</h2>
<div class="tbl-wrap">{tier_table}</div>

<h2>종목별 상세</h2>
<div class="tbl-wrap">{detail_table}</div>

<p class="muted" style="margin-top:16px;font-size:11px">
생성: {today}
</p>
</div></body></html>"""


# ── 메인 ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="생성 후 브라우저 열기")
    args = parser.parse_args()

    today = datetime.now().date()
    print("=== 당일돌파형 홀딩 분석 ===")

    print("1. 시그널 로드 중...")
    signals = load_all_1750_signals()
    print(f"   {len(signals)}개 날짜 로드됨")

    print("2. 당일돌파형 추출 중...")
    entries = collect_p1_entries(signals)
    print(f"   {len(entries)}건 추출")

    print("3. yfinance D+1~D+5 가격 조회 중...")
    for i, e in enumerate(entries, 1):
        fwd = fetch_forward(e["code"], e["market"], e["signal_date"])
        entry = e["entry_price"]
        e["ret_d1_open"]  = _ret(fwd.get("d1_open"),  entry)
        e["ret_d1_close"] = _ret(fwd.get("d1_close"), entry)
        e["ret_d2_close"] = _ret(fwd.get("d2_close"), entry)
        e["ret_d3_close"] = _ret(fwd.get("d3_close"), entry)
        e["ret_d4_close"] = _ret(fwd.get("d4_close"), entry)
        e["ret_d5_close"] = _ret(fwd.get("d5_close"), entry)
        if i % 5 == 0 or i == len(entries):
            print(f"   {i}/{len(entries)} 완료", end="\r")
    print()

    print("4. HTML 리포트 생성 중...")
    html = build_html(entries, today)
    out  = REPORTS_DIR / f"holding_analysis_{today}.html"
    out.write_text(html, encoding="utf-8")
    print(f"   저장: {out}")

    if args.open:
        webbrowser.open(out.as_uri())

    # 콘솔 요약
    col_keys   = ["d1_open", "d1_close", "d2_close", "d3_close", "d4_close", "d5_close"]
    col_labels = ["D+1시가★", "D+1종가", "D+2종가", "D+3종가", "D+4종가", "D+5종가"]
    print("\n── 출구별 통계 ─────────────────────────────")
    print(f"{'출구':<10} {'건수':>4} {'승률':>6} {'평균':>8} {'중위':>8}")
    print("-" * 45)
    for k, lbl in zip(col_keys, col_labels):
        vals = [e[f"ret_{k}"] for e in entries if e.get(f"ret_{k}") is not None]
        if not vals:
            print(f"{lbl:<10} {'0':>4} {'-':>6} {'-':>8} {'-':>8}")
            continue
        n    = len(vals)
        win  = sum(1 for v in vals if v > 0) / n * 100
        avg  = sum(vals) / n
        med  = sorted(vals)[n // 2]
        print(f"{lbl:<10} {n:>4} {win:>5.0f}% {avg:>+7.2f}% {med:>+7.2f}%")


if __name__ == "__main__":
    main()
