# scripts/d1d2_pattern_analysis.py
"""
신호 발생 후 D+1 눌림 → D+2 반등 패턴 분석.

패턴 정의:
  D0  : 신호 발생일 (regular_close_price 기준)
  D+1 : 눌림 — D+1 종가 < D0 신호가
  D+2 : 반등 — D+2 종가 > D+1 종가

사용법:
  python -m scripts.d1d2_pattern_analysis [--inter-only] [--open]
  --inter-only  : 교집합 종목만 분석
  --open        : 결과 HTML을 브라우저로 열기
"""

import argparse
import glob
import sys
import webbrowser
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

_BASE       = Path(__file__).parent.parent
_SIGNALS    = _BASE / "data" / "signals"
_REPORT_DIR = _BASE / "reports"
_OUT_HTML   = _REPORT_DIR / "d1d2_pattern_analysis.html"

# ── 신호 로드 (날짜별 최신 1파일, 종목 중복 제거) ──────────────

def _load_signals(inter_only: bool) -> pd.DataFrame:
    files = sorted(glob.glob(str(_SIGNALS / "*_signals.csv")))
    if not files:
        print("[오류] signals 파일 없음")
        sys.exit(1)

    # 날짜별 그룹화 → 1750 파일 우선, 없으면 최신
    by_date: dict[str, list[str]] = defaultdict(list)
    for f in files:
        date_part = Path(f).name[:10]
        by_date[date_part].append(f)

    frames = []
    for date_part, flist in sorted(by_date.items()):
        preferred = [f for f in flist if "1750" in f]
        chosen = preferred[0] if preferred else flist[-1]
        try:
            df = pd.read_csv(chosen, encoding="utf-8-sig", dtype={"종목코드": str})
        except Exception:
            try:
                df = pd.read_csv(chosen, encoding="cp949", dtype={"종목코드": str})
            except Exception:
                continue
        df["_sig_date"] = date_part
        frames.append(df)

    if not frames:
        print("[오류] 로드된 신호 없음")
        sys.exit(1)

    merged = pd.concat(frames, ignore_index=True)

    # 교집합 필터
    if inter_only and "in_inter" in merged.columns:
        merged = merged[merged["in_inter"].astype(str).str.lower().isin(["true", "1", "yes"])]

    # 기준가: regular_close_price → entry_reference_price → signal_price
    def _ref_price(row):
        for col in ["regular_close_price", "entry_reference_price", "signal_price"]:
            v = row.get(col)
            try:
                fv = float(v)
                if fv > 0:
                    return fv
            except (TypeError, ValueError):
                pass
        return None

    merged["_ref_price"] = merged.apply(_ref_price, axis=1)
    merged = merged[merged["_ref_price"].notna()]

    # 날짜+종목 중복 제거 (같은 날 여러 파일에 같은 종목이 있으면 첫 번째만)
    merged = merged.drop_duplicates(subset=["_sig_date", "종목코드"])

    print(f"[신호] 총 {len(merged)}개 (날짜: {merged['_sig_date'].nunique()}일)")
    return merged


# ── yfinance로 종가 히스토리 ───────────────────────────────────

def _fetch_price_history(codes: list[str]) -> dict[str, pd.Series]:
    """code → DatetimeIndex-indexed Close 시리즈."""
    try:
        import yfinance as yf
    except ImportError:
        print("[오류] yfinance 미설치: pip install yfinance")
        sys.exit(1)

    result: dict[str, pd.Series] = {}
    total = len(codes)
    for i, code in enumerate(codes, 1):
        if i % 10 == 0 or i == total:
            print(f"  가격 조회 중... {i}/{total}", end="\r")
        for suffix in [".KS", ".KQ"]:
            try:
                hist = yf.Ticker(f"{code}{suffix}").history(period="60d")
                if not hist.empty:
                    s = hist["Close"]
                    s.index = s.index.tz_localize(None).normalize()
                    result[code] = s
                    break
            except Exception:
                pass
    print()
    return result


# ── D+1, D+2 종가 조회 ────────────────────────────────────────

def _next_biz_close(series: pd.Series, from_date: pd.Timestamp, n: int) -> float | None:
    """from_date 이후 n번째 거래일 종가."""
    future = series[series.index > from_date]
    if len(future) >= n:
        return float(future.iloc[n - 1])
    return None


# ── 패턴 분류 ─────────────────────────────────────────────────

def _classify(d0_price, d1_close, d2_close) -> str:
    if d1_close is None or d2_close is None:
        return "데이터부족"
    d1_down = d1_close < d0_price
    d2_up   = d2_close > d1_close
    d2_rec  = d2_close > d0_price   # D0 완전회복

    if d1_down and d2_up and d2_rec:
        return "D1눌림+D2완전회복"
    if d1_down and d2_up:
        return "D1눌림+D2반등"
    if d1_down and not d2_up:
        return "D1눌림+D2추가하락"
    if not d1_down and d2_up:
        return "D1상승+D2상승"
    if not d1_down and not d2_up:
        return "D1상승+D2하락"
    return "기타"

# 패턴 순서 (표시용)
_PATTERN_ORDER = [
    "D1눌림+D2완전회복",
    "D1눌림+D2반등",
    "D1눌림+D2추가하락",
    "D1상승+D2상승",
    "D1상승+D2하락",
    "데이터부족",
]

_PATTERN_COLOR = {
    "D1눌림+D2완전회복":   "#4caf50",
    "D1눌림+D2반등":       "#8bc34a",
    "D1눌림+D2추가하락":   "#ef5350",
    "D1상승+D2상승":       "#29b6f6",
    "D1상승+D2하락":       "#fb8c00",
    "데이터부족":          "#555",
}

_PATTERN_DESC = {
    "D1눌림+D2완전회복":   "D+1 종가 < D0신호가 → D+2 종가 > D0신호가 (완전회복)",
    "D1눌림+D2반등":       "D+1 종가 < D0신호가 → D+2 종가 > D+1종가 (D0미회복)",
    "D1눌림+D2추가하락":   "D+1 종가 < D0신호가 → D+2도 추가 하락",
    "D1상승+D2상승":       "D+1 종가 ≥ D0신호가 → D+2도 상승 지속",
    "D1상승+D2하락":       "D+1 종가 ≥ D0신호가 → D+2 하락",
    "데이터부족":          "D+1 또는 D+2 가격 조회 불가",
}


# ── HTML 생성 ─────────────────────────────────────────────────

def _e(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _pct(v) -> str:
    return f"{v:+.2f}%" if v is not None else "-"

def _krw(v) -> str:
    return f"{v:,.0f}원" if v is not None else "-"


def _generate_html(records: list[dict], inter_only: bool) -> str:
    df = pd.DataFrame(records)
    total = len(df)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 패턴 집계 ──────────────────────────────────────────────
    counts = df["pattern"].value_counts().to_dict()
    pattern_rows = ""
    for pat in _PATTERN_ORDER:
        n   = counts.get(pat, 0)
        pct = n / total * 100 if total else 0
        col = _PATTERN_COLOR.get(pat, "#888")
        bar = f'<div style="background:{col};height:14px;width:{pct:.1f}%;min-width:2px;border-radius:3px;display:inline-block"></div>'
        pattern_rows += (
            f"<tr>"
            f"<td><span style='display:inline-block;background:{col};color:#fff;font-size:11px;"
            f"padding:2px 8px;border-radius:3px'>{_e(pat)}</span></td>"
            f"<td style='text-align:center;font-weight:700;color:{col}'>{n}</td>"
            f"<td style='text-align:center'>{pct:.1f}%</td>"
            f"<td style='min-width:120px'>{bar}</td>"
            f"<td style='color:#555;font-size:11px'>{_e(_PATTERN_DESC.get(pat,''))}</td>"
            f"</tr>"
        )

    # 핵심 수치
    d1_down_n   = counts.get("D1눌림+D2완전회복", 0) + counts.get("D1눌림+D2반등", 0) + counts.get("D1눌림+D2추가하락", 0)
    d1d2_up_n   = counts.get("D1눌림+D2완전회복", 0) + counts.get("D1눌림+D2반등", 0)
    d1d2_full_n = counts.get("D1눌림+D2완전회복", 0)
    valid_n     = total - counts.get("데이터부족", 0)

    d1_down_rate   = d1_down_n   / valid_n * 100 if valid_n else 0
    d1d2_up_rate   = d1d2_up_n   / valid_n * 100 if valid_n else 0
    d1d2_full_rate = d1d2_full_n / valid_n * 100 if valid_n else 0
    # D+1 눌림 중 D+2 반등 비율
    d2_bounce_cond = d1d2_up_n / d1_down_n * 100 if d1_down_n else 0

    # ── 수익률 통계 (패턴별) ───────────────────────────────────
    pnl_rows = ""
    for pat in [p for p in _PATTERN_ORDER if p != "데이터부족"]:
        sub = df[df["pattern"] == pat]
        if sub.empty:
            continue
        d1_chgs = sub["d1_chg_pct"].dropna()
        d2_chgs = sub["d2_chg_pct"].dropna()
        d2_from_d0 = sub["d2_vs_d0_pct"].dropna()
        col = _PATTERN_COLOR.get(pat, "#888")

        def _stat(s):
            if s.empty:
                return "-"
            return f"평균{s.mean():+.1f}% / 중앙{s.median():+.1f}%"

        pnl_rows += (
            f"<tr>"
            f"<td><span style='display:inline-block;background:{col};color:#fff;font-size:10px;"
            f"padding:1px 6px;border-radius:3px'>{_e(pat)}</span></td>"
            f"<td style='text-align:center'>{len(sub)}</td>"
            f"<td style='text-align:center;font-size:12px'>{_stat(d1_chgs)}</td>"
            f"<td style='text-align:center;font-size:12px'>{_stat(d2_chgs)}</td>"
            f"<td style='text-align:center;font-size:12px'>{_stat(d2_from_d0)}</td>"
            f"</tr>"
        )

    # ── 종목별 상세 ────────────────────────────────────────────
    detail_rows = ""
    for _, row in df.sort_values(["_sig_date", "pattern"]).iterrows():
        pat = row["pattern"]
        col = _PATTERN_COLOR.get(pat, "#888")
        inter_mark = "✓" if str(row.get("in_inter", "")).lower() in ("true", "1", "yes") else ""
        d1_chg = row.get("d1_chg_pct")
        d2_chg = row.get("d2_chg_pct")
        d2_d0  = row.get("d2_vs_d0_pct")

        def _col_pct(v):
            if v is None:
                return "<td style='color:#555'>-</td>"
            c = "#4caf50" if v > 0 else ("#ef5350" if v < 0 else "#888")
            return f"<td style='text-align:right;color:{c}'>{v:+.2f}%</td>"

        detail_rows += (
            f"<tr>"
            f"<td style='color:#888;font-size:11px'>{_e(row['_sig_date'])}</td>"
            f"<td>{_e(row.get('종목명',''))}</td>"
            f"<td style='color:#666;font-size:11px'>{_e(row.get('종목코드',''))}</td>"
            f"<td style='text-align:center;color:#29b6f6;font-size:11px'>{inter_mark}</td>"
            f"<td style='text-align:right'>{_krw(row.get('_ref_price'))}</td>"
            f"<td style='text-align:right;color:#aaa;font-size:11px'>{_krw(row.get('d1_close'))}</td>"
            f"<td style='text-align:right;color:#aaa;font-size:11px'>{_krw(row.get('d2_close'))}</td>"
            f"{_col_pct(d1_chg)}"
            f"{_col_pct(d2_chg)}"
            f"{_col_pct(d2_d0)}"
            f"<td><span style='background:{col};color:#fff;font-size:10px;padding:1px 6px;"
            f"border-radius:3px'>{_e(pat)}</span></td>"
            f"</tr>"
        )

    inter_note = " (교집합 종목만)" if inter_only else ""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>D+1 눌림 → D+2 반등 패턴 분석</title>
<style>
  body{{background:#121212;color:#e0e0e0;font-family:'Malgun Gothic',sans-serif;margin:0;padding:24px;max-width:1200px}}
  h1{{font-size:20px;margin-bottom:4px}}
  .card{{background:#1a1a1a;border-radius:8px;padding:16px;margin-bottom:16px}}
  .section-title{{font-size:14px;font-weight:600;margin-bottom:12px}}
  .label{{font-size:11px;color:#888;margin-bottom:4px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:6px 8px;border-bottom:1px solid #2a2a2a;text-align:left}}
  th{{color:#888;font-size:11px;font-weight:500}}
  .kv{{display:flex;gap:40px;flex-wrap:wrap}}
  .kv-item{{min-width:120px}}
  .big{{font-size:28px;font-weight:700}}
  .sub{{font-size:11px;color:#555;margin-top:2px}}
</style>
</head>
<body>
<h1>📊 신호 후 D+1 눌림 → D+2 반등 패턴 분석{_e(inter_note)}</h1>
<p style="color:#555;font-size:12px;margin-bottom:20px">생성: {now_str} | 분석 종목: {total}개 (유효: {valid_n}개)</p>

<div class="card">
  <div class="kv">
    <div class="kv-item">
      <div class="label">D+1 눌림 발생률</div>
      <div class="big" style="color:#fb8c00">{d1_down_rate:.1f}%</div>
      <div class="sub">{d1_down_n}/{valid_n}개 — D+1 종가 < D0 신호가</div>
    </div>
    <div class="kv-item">
      <div class="label">D+1 눌림 후 D+2 반등</div>
      <div class="big" style="color:#8bc34a">{d2_bounce_cond:.1f}%</div>
      <div class="sub">눌림 {d1_down_n}개 중 {d1d2_up_n}개 반등</div>
    </div>
    <div class="kv-item">
      <div class="label">D1눌림+D2완전회복</div>
      <div class="big" style="color:#4caf50">{d1d2_full_rate:.1f}%</div>
      <div class="sub">{d1d2_full_n}/{valid_n}개 — D+2 종가 > D0신호가</div>
    </div>
    <div class="kv-item">
      <div class="label">D1눌림+D2반등(전체대비)</div>
      <div class="big" style="color:#8bc34a">{d1d2_up_rate:.1f}%</div>
      <div class="sub">{d1d2_up_n}/{valid_n}개 — 눌렸다가 D+2 회복</div>
    </div>
  </div>
</div>

<div class="card">
  <div class="section-title">패턴별 분포</div>
  <table>
    <thead><tr><th>패턴</th><th style="text-align:center">건수</th><th style="text-align:center">비율</th><th>비율 바</th><th>정의</th></tr></thead>
    <tbody>{pattern_rows}</tbody>
  </table>
</div>

<div class="card">
  <div class="section-title">패턴별 수익률 통계 (D+1·D+2 변화율)</div>
  <table>
    <thead><tr>
      <th>패턴</th><th style="text-align:center">건수</th>
      <th style="text-align:center">D+1 변화율 (vs D0)</th>
      <th style="text-align:center">D+2 변화율 (vs D+1)</th>
      <th style="text-align:center">D+2 vs D0</th>
    </tr></thead>
    <tbody>{pnl_rows}</tbody>
  </table>
  <div style="font-size:11px;color:#555;margin-top:6px">D+1 변화율 = (D+1종가 - D0신호가) / D0신호가 × 100</div>
</div>

<div class="card">
  <div class="section-title">종목별 상세</div>
  <table>
    <thead><tr>
      <th>신호일</th><th>종목명</th><th>코드</th>
      <th style="text-align:center">교집합</th>
      <th style="text-align:right">D0 신호가</th>
      <th style="text-align:right">D+1 종가</th>
      <th style="text-align:right">D+2 종가</th>
      <th style="text-align:right">D+1변화</th>
      <th style="text-align:right">D+2변화</th>
      <th style="text-align:right">D+2vsD0</th>
      <th>패턴</th>
    </tr></thead>
    <tbody>{detail_rows}</tbody>
  </table>
</div>
</body>
</html>"""


# ── 메인 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="D+1 눌림 → D+2 반등 패턴 분석")
    parser.add_argument("--inter-only", action="store_true", help="교집합 종목만 분석")
    parser.add_argument("--open",       action="store_true", help="결과 HTML 브라우저로 열기")
    args = parser.parse_args()

    signals = _load_signals(args.inter_only)

    # 유니크 종목 코드 수집
    codes = signals["종목코드"].unique().tolist()
    print(f"[가격조회] 종목 {len(codes)}개 yfinance 조회 시작...")
    price_hist = _fetch_price_history(codes)
    print(f"  조회 성공: {len(price_hist)}/{len(codes)}개")

    # 분석
    records = []
    missing_price = 0
    for _, row in signals.iterrows():
        code      = str(row["종목코드"]).zfill(6)
        d0_price  = row["_ref_price"]
        sig_date  = pd.Timestamp(row["_sig_date"])

        hist = price_hist.get(code)
        d1_close = _next_biz_close(hist, sig_date, 1) if hist is not None else None
        d2_close = _next_biz_close(hist, sig_date, 2) if hist is not None else None

        if d1_close is None or d2_close is None:
            missing_price += 1

        d1_chg = (d1_close - d0_price) / d0_price * 100 if d1_close else None
        d2_chg = (d2_close - d1_close) / d1_close * 100 if (d1_close and d2_close) else None
        d2_d0  = (d2_close - d0_price) / d0_price * 100 if d2_close else None

        records.append({
            "_sig_date":   row["_sig_date"],
            "종목코드":    code,
            "종목명":      row.get("종목명", ""),
            "in_inter":    row.get("in_inter", ""),
            "_ref_price":  d0_price,
            "d1_close":    d1_close,
            "d2_close":    d2_close,
            "d1_chg_pct":  round(d1_chg, 2) if d1_chg is not None else None,
            "d2_chg_pct":  round(d2_chg, 2) if d2_chg is not None else None,
            "d2_vs_d0_pct":round(d2_d0,  2) if d2_d0  is not None else None,
            "pattern":     _classify(d0_price, d1_close, d2_close),
        })

    if missing_price:
        print(f"  [주의] 가격 조회 실패: {missing_price}개 → '데이터부족' 분류")

    # 결과 출력
    df = pd.DataFrame(records)
    valid = df[df["pattern"] != "데이터부족"]
    total_v = len(valid)

    print("\n" + "=" * 50)
    print(f"분석 결과 (유효 {total_v}개 / 전체 {len(df)}개)")
    print("=" * 50)
    for pat in _PATTERN_ORDER:
        n   = (df["pattern"] == pat).sum()
        pct = n / total_v * 100 if total_v else 0
        print(f"  {pat:<22} {n:>3}건  {pct:>5.1f}%")

    d1_down = valid[valid["d1_chg_pct"] < 0]
    d1d2_up = d1_down[d1_down["d2_chg_pct"] > 0]
    print(f"\n핵심: D+1 눌림 발생 {len(d1_down)}건 중 D+2 반등 {len(d1d2_up)}건 "
          f"({len(d1d2_up)/len(d1_down)*100:.1f}%)" if len(d1_down) else "")

    # CSV 저장
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _REPORT_DIR / "d1d2_pattern_analysis.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\nCSV → {csv_path}")

    # HTML 저장
    html = _generate_html(records, args.inter_only)
    _OUT_HTML.write_text(html, encoding="utf-8")
    print(f"HTML → {_OUT_HTML}")

    if args.open:
        webbrowser.open(_OUT_HTML.as_uri())


if __name__ == "__main__":
    main()
