# scripts/trade_analyzer.py
"""
HTS(영웅문S#) 매매내역 CSV → 원칙 준수 분석 리포트

사용법:
  python -m scripts.trade_analyzer [CSV경로] [--open] [--overwrite]

  CSV경로  : 생략하면 data/weekly_trading_review/ 에서 가장 최근 파일 자동 선택
  --open   : 생성 후 HTML을 기본 브라우저로 열기
  --overwrite : 같은 날짜 결과 덮어쓰기 (기본: _v2, _v3 suffix)
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import TRADE_ANALYZER_BASE_CAPITAL

logger = logging.getLogger(__name__)

# ── 경로 상수 ────────────────────────────────────────────────
_BASE        = Path(__file__).parent.parent
_TRADES_DIR  = _BASE / "data" / "weekly_trading_review"
_SIGNALS_DIR = _BASE / "data" / "signals"
_GAP_CSV     = _BASE / "data" / "gap_results" / "all.csv"
_REPORT_DIR  = _BASE / "reports" / "trade_reviews"
_HISTORY_DIR = _BASE / "data" / "trade_reviews"


# ── 위반 태그 정의 ────────────────────────────────────────────
TAG_DESC = {
    "NON_SIGNAL_TRADE":       "시스템 미신호 종목 매수",
    "SIGNAL_FILE_MISSING":    "신호 파일 없음 (확인불가)",
    "NON_INTERSECTION_TRADE": "교집합 미포함 종목 매수",
    "NOT_CLOSE_ENTRY":        "종가진입 원칙 위반 (시간/가격 불일치)",
    "D1_CHASE_ENTRY":         "D+1 장초 고점 추격매수",
    "REVERSE_AT_EXIT_ZONE":   "D+1 09:20~09:40 고점 역추격매수",
    "MISSED_D1_EXIT":         "D+1 익일 익절 기회 미활용",
    "AVERAGING_DOWN":         "물타기 (하락 후 추가매수)",
    "ADDITIONAL_BUY":         "추가매수 (물타기 외)",
    "RE_ENTRY":               "재진입 (당일 청산 후 재매수)",
    "OVERSIZED_POSITION":     "과대 포지션",
    "NXT_ENTRY":              "NXT 시간외 단일가 진입",
}

# 포지션 비중 경고 임계값 (%)
_POS_WARN  = 10
_POS_ALERT = 15
_POS_CRIT  = 30
_POS_MAX   = 40


# ── HTS CSV 파싱 ─────────────────────────────────────────────

def _load_hts_csv(path: Path) -> list[dict]:
    """영웅문S# 기간별 주문체결상세 CSV → 체결 거래 목록 반환.

    2행 쌍 구조:
      row1: 주식채널 | 주문번호 | 원주문번호 | 종목코드 | 주문유형 | 현금매수/매도K | 주문수량 | 주문단가 | ...
      row2: 날짜 | 종목명 | 접수 | 보통매매 | 체결수량 | 체결단가 | 취소/정정주문 | 영웅문S# | | 체결시각 | 시장
    취소/정정 주문은 제외.
    반환 필드: date, time, code, name, side, qty, price, market
    """
    try:
        raw = path.read_bytes()
        text = raw.decode("euc-kr", errors="replace")
    except Exception as e:
        raise RuntimeError(f"CSV 읽기 실패: {e}")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    # 헤더 2행 스킵, 빈 행 제거
    data_rows = [r for r in rows[2:] if any(c.strip() for c in r)]

    trades = []
    i = 0
    while i + 1 < len(data_rows):
        row1 = data_rows[i]
        row2 = data_rows[i + 1]
        i += 2

        if len(row1) < 8 or len(row2) < 10:
            continue

        # 취소/정정 여부: row2[6]
        cancel_flag = row2[6].strip() if len(row2) > 6 else ""
        if "취소" in cancel_flag or "정정" in cancel_flag:
            continue

        # 체결수량: row2[4]
        exec_qty_str = row2[4].strip().replace(",", "") if len(row2) > 4 else ""
        if not exec_qty_str or exec_qty_str == "0":
            continue

        try:
            exec_qty = int(exec_qty_str)
        except ValueError:
            continue

        # 날짜: row2[0] (2026/04/27 → 20260427)
        raw_date  = row2[0].strip().replace("/", "").replace("-", "")
        # 종목코드: row1[3] (leading apostrophe 제거)
        code      = row1[3].strip().lstrip("'").zfill(6)
        # 종목명: row2[1]
        name      = row2[1].strip()
        # 매수/매도 구분: row1[5] ('현금매수', '현금매도 K' 등)
        side_raw  = row1[5].strip()
        # 시장구분: row2[10] (KRX / NXT / SOR)
        market_s  = row2[10].strip() if len(row2) > 10 else ""
        # 체결시각: row2[9]
        exec_time = row2[9].strip() if len(row2) > 9 else ""
        # 체결단가: row2[5]
        exec_px_s = row2[5].strip().replace(",", "") if len(row2) > 5 else ""

        try:
            exec_px = float(exec_px_s)
        except ValueError:
            continue

        side = "buy" if "매수" in side_raw else "sell"

        trades.append({
            "date":   raw_date,
            "time":   exec_time,
            "code":   code,
            "name":   name,
            "side":   side,
            "qty":    exec_qty,
            "price":  exec_px,
            "market": market_s,
        })

    return trades


# ── 신호 캐시 ────────────────────────────────────────────────

class SignalCache:
    """signals.csv + gap_results/all.csv 를 메모리에 캐시."""

    def __init__(self):
        self._signals: dict[tuple[str, str], dict] = {}   # (date8, code) → row
        self._gap:     dict[tuple[str, str], dict] = {}   # (entry_date8, code) → row
        self._loaded_signal_dirs: set[str] = set()
        self._load_gap()
        self._load_signals()

    def _load_gap(self):
        if not _GAP_CSV.exists():
            return
        # utf-8-sig: BOM 자동 제거 (Excel 저장 CSV 대응)
        with open(_GAP_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                d = row.get("entry_date", "").strip()
                c = row.get("code", "").strip().zfill(6)
                if d and c:
                    self._gap[(d, c)] = row

    def _load_signals(self):
        for p in sorted(_SIGNALS_DIR.glob("*_signals.csv")):
            # 파일명 형식: 2026-04-28_1754_signals.csv → 20260428
            date8 = p.name[:10].replace("-", "")
            with open(p, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    code = row.get("종목코드", "").strip().zfill(6)
                    if code:
                        self._signals[(date8, code)] = row

    def find_signal(self, trade_date8: str, code: str, lookback: int = 3) -> dict | None:
        """trade_date 및 D-1~D-lookback 날짜에서 신호 검색.
        signals.csv 없으면 gap_results/all.csv로 폴백."""
        d = datetime.strptime(trade_date8, "%Y%m%d").date()
        for delta in range(lookback + 1):
            candidate = (d - timedelta(days=delta)).strftime("%Y%m%d")
            sig = self._signals.get((candidate, code))
            if sig:
                return {**sig, "_signal_date": candidate}
        # gap_results 폴백: 신호 CSV 없는 날짜도 사후 집계 데이터로 확인
        for delta in range(lookback + 1):
            candidate = (d - timedelta(days=delta)).strftime("%Y%m%d")
            if self._gap.get((candidate, code)):
                return {"_signal_date": candidate, "_from_gap": True}
        return None

    def signal_price(self, signal_date8: str, code: str) -> float | None:
        """신호일 종가 (gap_results 우선, fallback: signals.csv signal_price)."""
        gap = self._gap.get((signal_date8, code))
        if gap:
            try:
                return float(gap["entry_price"])
            except (ValueError, KeyError):
                pass
        sig = self._signals.get((signal_date8, code))
        if sig:
            try:
                px = float(sig.get("signal_price", 0) or 0)
                return px if px > 0 else None
            except (ValueError, TypeError):
                pass
        return None

    def is_inter(self, signal_date8: str, code: str) -> bool | None:
        sig = self._signals.get((signal_date8, code))
        if sig is not None:
            v = sig.get("in_inter", "").strip().lower()
            return v in ("true", "1", "yes")
        # gap_results 폴백
        gap = self._gap.get((signal_date8, code))
        if gap is not None:
            return gap.get("in_inter", "").strip() == "True"
        return None

    def has_signal_file(self, trade_date8: str, lookback: int = 3) -> bool:
        d = datetime.strptime(trade_date8, "%Y%m%d").date()
        for delta in range(lookback + 1):
            candidate = (d - timedelta(days=delta)).strftime("%Y%m%d")
            if any(True for k in self._signals if k[0] == candidate):
                return True
        return False

    def d1_open(self, signal_date8: str, code: str) -> float | None:
        gap = self._gap.get((signal_date8, code))
        if gap:
            try:
                return float(gap["exit_open"]) or None
            except (ValueError, KeyError, TypeError):
                pass
        return None


# ── 포지션 빌더 ──────────────────────────────────────────────

def _build_positions(trades: list[dict]) -> dict[str, list[dict]]:
    """종목코드별 거래 목록 그룹화."""
    result: dict[str, list[dict]] = {}
    for t in trades:
        result.setdefault(t["code"], []).append(t)
    return result


def _calc_pnl(buys: list[dict], sells: list[dict]) -> dict:
    """매수/매도 내역으로 평균단가·실현손익·미청산 계산."""
    total_buy_qty   = sum(t["qty"] for t in buys)
    total_buy_value = sum(t["qty"] * t["price"] for t in buys)
    total_sell_qty  = sum(t["qty"] for t in sells)
    total_sell_value = sum(t["qty"] * t["price"] for t in sells)
    avg_cost = total_buy_value / total_buy_qty if total_buy_qty else 0
    remaining = total_buy_qty - total_sell_qty
    realized = total_sell_value - avg_cost * total_sell_qty if avg_cost else 0
    realized_pct = realized / (avg_cost * total_sell_qty) * 100 if avg_cost and total_sell_qty else None
    return {
        "avg_cost":       round(avg_cost),
        "total_buy_qty":  total_buy_qty,
        "total_buy_value": total_buy_value,
        "total_sell_qty": total_sell_qty,
        "total_sell_value": total_sell_value,
        "remaining_qty":  remaining,
        "realized":       round(realized),
        "realized_pct":   round(realized_pct, 2) if realized_pct is not None else None,
    }


# ── 위반 태그 탐지 ────────────────────────────────────────────

def _detect_violations(
    code: str,
    name: str,
    code_trades: list[dict],
    cache: SignalCache,
    period_total_buy: float,
    base_capital: float,
) -> list[str]:
    """하나의 종목에 대한 위반 태그 리스트 반환."""
    tags: list[str] = []
    buys  = [t for t in code_trades if t["side"] == "buy"]
    sells = [t for t in code_trades if t["side"] == "sell"]
    if not buys:
        return tags

    first_buy = min(buys, key=lambda t: (t["date"], t["time"]))
    signal = cache.find_signal(first_buy["date"], code)

    if signal is None:
        if not cache.has_signal_file(first_buy["date"]):
            tags.append("SIGNAL_FILE_MISSING")
        else:
            tags.append("NON_SIGNAL_TRADE")
        # 이후 신호 기반 검사 불가
        _check_position_size(tags, buys, period_total_buy, base_capital)
        _check_additional_and_re_entry(tags, buys, sells)
        return tags

    sig_date = signal["_signal_date"]
    sig_price = cache.signal_price(sig_date, code)
    is_inter = cache.is_inter(sig_date, code)

    if is_inter is False:
        tags.append("NON_INTERSECTION_TRADE")

    _check_entry_timing(tags, buys, first_buy, sig_date, sig_price)
    _check_position_size(tags, buys, period_total_buy, base_capital)
    _check_additional_and_re_entry(tags, buys, sells)
    _check_missed_d1_exit(tags, sells, sig_date, code, cache)

    return tags


def _check_entry_timing(
    tags: list[str],
    buys: list[dict],
    first_buy: dict,
    sig_date: str,
    sig_price: float | None,
) -> None:
    """NOT_CLOSE_ENTRY / D1_CHASE_ENTRY / REVERSE_AT_EXIT_ZONE / NXT_ENTRY 판정."""
    for b in buys:
        t_date = b["date"]
        t_time = b["time"]  # "HH:MM:SS" or "HH:MM"
        t_px   = b["price"]
        market = b.get("market", "")

        # NXT 시간외 단일가
        if "NXT" in market.upper():
            if "NXT_ENTRY" not in tags:
                tags.append("NXT_ENTRY")

        if t_date == sig_date:
            # 종가진입 원칙: 14:50~15:30 사이 OR ±1.5% 이내
            h, m = _hm(t_time)
            in_window = (14, 50) <= (h, m) <= (15, 30)
            price_ok = sig_price and abs(t_px - sig_price) / sig_price <= 0.015 if sig_price else False
            if not in_window and not price_ok:
                if "NOT_CLOSE_ENTRY" not in tags:
                    tags.append("NOT_CLOSE_ENTRY")
        else:
            # 다음 거래일 매수
            h, m = _hm(t_time)
            # D+1 장초 추격: 08:00~10:00, 가격 ≥ sig_price × 1.03
            if (8, 0) <= (h, m) <= (10, 0):
                if sig_price and t_px >= sig_price * 1.03:
                    if "D1_CHASE_ENTRY" not in tags:
                        tags.append("D1_CHASE_ENTRY")
            # D+1 역추격: 09:20~09:40, 가격 ≥ sig_price × 1.03
            if (9, 20) <= (h, m) <= (9, 40):
                if sig_price and t_px >= sig_price * 1.03:
                    if "REVERSE_AT_EXIT_ZONE" not in tags:
                        tags.append("REVERSE_AT_EXIT_ZONE")


def _check_position_size(
    tags: list[str],
    buys: list[dict],
    period_total_buy: float,
    base_capital: float,
) -> None:
    """포지션 비중 경고 (OVERSIZED_POSITION)."""
    denom = base_capital if base_capital > 0 else period_total_buy
    if not denom:
        return
    stock_buy = sum(b["qty"] * b["price"] for b in buys)
    pct = stock_buy / denom * 100
    if pct >= _POS_WARN:
        tags.append("OVERSIZED_POSITION")


def _check_additional_and_re_entry(
    tags: list[str],
    buys: list[dict],
    sells: list[dict],
) -> None:
    """AVERAGING_DOWN / ADDITIONAL_BUY / RE_ENTRY."""
    if len(buys) < 2:
        return

    sorted_buys = sorted(buys, key=lambda t: (t["date"], t["time"]))
    sorted_sells = sorted(sells, key=lambda t: (t["date"], t["time"]))

    avg_cost = 0.0
    total_qty = 0
    for i, b in enumerate(sorted_buys):
        if i == 0:
            avg_cost = b["price"]
            total_qty = b["qty"]
            continue

        # 이 매수 직전에 전량 청산되었는지 확인 → RE_ENTRY
        cum_sell_before = sum(
            s["qty"] for s in sorted_sells
            if (s["date"], s["time"]) < (b["date"], b["time"])
        )
        if cum_sell_before >= total_qty:
            if "RE_ENTRY" not in tags:
                tags.append("RE_ENTRY")
            avg_cost = b["price"]
            total_qty = b["qty"]
            continue

        # 물타기: 추가 매수 가격 < 현재 평균단가
        if b["price"] < avg_cost:
            if "AVERAGING_DOWN" not in tags:
                tags.append("AVERAGING_DOWN")
        else:
            if "ADDITIONAL_BUY" not in tags:
                tags.append("ADDITIONAL_BUY")

        new_val   = avg_cost * total_qty + b["price"] * b["qty"]
        total_qty += b["qty"]
        avg_cost   = new_val / total_qty if total_qty else avg_cost


def _check_missed_d1_exit(
    tags: list[str],
    sells: list[dict],
    sig_date: str,
    code: str,
    cache: SignalCache,
) -> None:
    """D+1 장초 익절 기회 미활용 (MISSED_D1_EXIT)."""
    d1_open = cache.d1_open(sig_date, code)
    if d1_open is None:
        return
    sig_price = cache.signal_price(sig_date, code)
    if not sig_price or d1_open <= sig_price:
        return  # gap-up 아님

    # D+1에 매도가 없으면 기회 미활용
    d1 = (datetime.strptime(sig_date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    sold_d1 = any(s["date"] == d1 for s in sells)
    if not sold_d1:
        tags.append("MISSED_D1_EXIT")


def _hm(time_str: str) -> tuple[int, int]:
    parts = time_str.replace(".", ":").split(":")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return 0, 0


# ── 분석 실행 ─────────────────────────────────────────────────

def _analyze(trades: list[dict], cache: SignalCache) -> dict:
    """전체 거래 분석. 종목별 결과 + 요약 반환."""
    buys_all  = [t for t in trades if t["side"] == "buy"]
    period_total_buy = sum(t["qty"] * t["price"] for t in buys_all)

    base_capital = float(TRADE_ANALYZER_BASE_CAPITAL) if TRADE_ANALYZER_BASE_CAPITAL else 0
    denom = base_capital if base_capital > 0 else period_total_buy

    by_code = _build_positions(trades)
    results = []

    for code, code_trades in by_code.items():
        name = code_trades[0]["name"]
        buys  = [t for t in code_trades if t["side"] == "buy"]
        sells = [t for t in code_trades if t["side"] == "sell"]
        if not buys:
            continue

        pnl  = _calc_pnl(buys, sells)
        tags = _detect_violations(code, name, code_trades, cache, period_total_buy, base_capital)

        stock_buy  = pnl["total_buy_value"]
        weight_pct = round(stock_buy / denom * 100, 1) if denom else None

        # 포지션 비중 경고 레벨
        weight_level = ""
        if weight_pct is not None:
            if weight_pct >= _POS_MAX:
                weight_level = "최우선경고"
            elif weight_pct >= _POS_CRIT:
                weight_level = "심각"
            elif weight_pct >= _POS_ALERT:
                weight_level = "경고"
            elif weight_pct >= _POS_WARN:
                weight_level = "주의"

        first_buy = min(buys, key=lambda t: (t["date"], t["time"]))
        signal = cache.find_signal(first_buy["date"], code)
        sig_date = signal["_signal_date"] if signal else None
        sig_price = cache.signal_price(sig_date, code) if sig_date else None
        is_inter  = cache.is_inter(sig_date, code)    if sig_date else None

        results.append({
            "code":         code,
            "name":         name,
            "sig_date":     sig_date,
            "sig_price":    sig_price,
            "is_inter":     is_inter,
            "avg_cost":     pnl["avg_cost"],
            "total_buy_qty":    pnl["total_buy_qty"],
            "total_buy_value":  pnl["total_buy_value"],
            "total_sell_qty":   pnl["total_sell_qty"],
            "total_sell_value": pnl["total_sell_value"],
            "remaining_qty":    pnl["remaining_qty"],
            "realized":         pnl["realized"],
            "realized_pct":     pnl["realized_pct"],
            "weight_pct":       weight_pct,
            "weight_level":     weight_level,
            "tags":             tags,
            "buys":             buys,
            "sells":            sells,
        })

    total_realized  = sum(r["realized"] for r in results)
    total_buy_value = sum(r["total_buy_value"] for r in results)
    total_realized_pct = total_realized / total_buy_value * 100 if total_buy_value else None

    signal_trades    = [r for r in results if "NON_SIGNAL_TRADE" not in r["tags"] and "SIGNAL_FILE_MISSING" not in r["tags"]]
    inter_trades     = [r for r in signal_trades if r.get("is_inter")]
    violation_trades = [r for r in results if r["tags"]]
    tag_counts: dict[str, int] = {}
    for r in results:
        for t in r["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    compliant_n = len([r for r in results if not r["tags"]])
    total_n     = len(results)
    compliance_rate = round(compliant_n / total_n * 100, 1) if total_n else None

    return {
        "summary": {
            "total_stocks":       total_n,
            "compliant_stocks":   compliant_n,
            "compliance_rate":    compliance_rate,
            "total_buy_value":    total_buy_value,
            "total_realized":     total_realized,
            "total_realized_pct": round(total_realized_pct, 2) if total_realized_pct is not None else None,
            "signal_count":       len(signal_trades),
            "inter_count":        len(inter_trades),
            "tag_counts":         tag_counts,
        },
        "stocks": results,
    }


# ── HTML 리포트 생성 ──────────────────────────────────────────

_TAG_COLOR = {
    "NON_SIGNAL_TRADE":       "#e53935",
    "SIGNAL_FILE_MISSING":    "#9e9e9e",
    "NON_INTERSECTION_TRADE": "#fb8c00",
    "NOT_CLOSE_ENTRY":        "#e53935",
    "D1_CHASE_ENTRY":         "#e53935",
    "REVERSE_AT_EXIT_ZONE":   "#c62828",
    "MISSED_D1_EXIT":         "#fb8c00",
    "AVERAGING_DOWN":         "#e53935",
    "ADDITIONAL_BUY":         "#fdd835",
    "RE_ENTRY":               "#fb8c00",
    "OVERSIZED_POSITION":     "#8e24aa",
    "NXT_ENTRY":              "#0288d1",
}

_WEIGHT_COLOR = {
    "최우선경고": "#c62828",
    "심각":      "#e53935",
    "경고":      "#fb8c00",
    "주의":      "#fdd835",
}


def _fmt_krw(v: float) -> str:
    return f"{v:+,.0f}원" if v >= 0 else f"{v:,.0f}원"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:+.2f}%"


def _e(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tag_badge(tag: str) -> str:
    color = _TAG_COLOR.get(tag, "#666")
    desc  = TAG_DESC.get(tag, tag)
    return (
        f'<span style="display:inline-block;background:{color};color:#fff;'
        f'font-size:11px;padding:2px 6px;border-radius:3px;margin:2px 2px 2px 0;'
        f'white-space:nowrap" title="{_e(desc)}">{_e(tag)}</span>'
    )


def _generate_html(result: dict, csv_name: str, report_date: str) -> str:
    s   = result["summary"]
    stocks = result["stocks"]

    def _pct_color(v) -> str:
        if v is None:
            return ""
        return "color:#4caf50" if v >= 0 else "color:#ef5350"

    # 요약 카드
    cr = s.get("compliance_rate")
    cr_color = "#4caf50" if (cr or 0) >= 80 else ("#fb8c00" if (cr or 0) >= 60 else "#ef5350")
    rp = s.get("total_realized_pct")

    tag_rows = ""
    for tag, cnt in sorted(s.get("tag_counts", {}).items(), key=lambda x: -x[1]):
        color = _TAG_COLOR.get(tag, "#666")
        tag_rows += (
            f"<tr><td>{_tag_badge(tag)}</td>"
            f"<td style='text-align:center'>{cnt}</td>"
            f"<td style='color:#aaa;font-size:12px'>{_e(TAG_DESC.get(tag,''))}</td></tr>"
        )

    # 종목별 카드
    stock_cards = ""
    for r in stocks:
        tag_html = "".join(_tag_badge(t) for t in r["tags"]) or '<span style="color:#4caf50">✓ 위반없음</span>'
        wl = r.get("weight_level", "")
        wl_color = _WEIGHT_COLOR.get(wl, "")
        wl_html  = f' <span style="color:{wl_color};font-weight:700">[{_e(wl)}]</span>' if wl else ""

        is_inter_html = ""
        if r.get("is_inter") is True:
            is_inter_html = ' <span style="color:#29b6f6;font-size:11px">[교집합]</span>'
        elif r.get("is_inter") is False:
            is_inter_html = ' <span style="color:#aaa;font-size:11px">[비교집합]</span>'

        rp_val = r.get("realized_pct")
        rp_html = f'<span style="{_pct_color(rp_val)}">{_fmt_pct(rp_val)}</span>' if rp_val is not None else "-"

        buy_rows = "".join(
            f"<tr><td>{_e(b['date'])} {_e(b['time'])}</td>"
            f"<td>매수</td><td style='text-align:right'>{b['qty']:,}주</td>"
            f"<td style='text-align:right'>{b['price']:,.0f}원</td>"
            f"<td style='text-align:right'>{b['qty']*b['price']:,.0f}원</td>"
            f"<td style='color:#aaa;font-size:11px'>{_e(b.get('market',''))}</td></tr>"
            for b in r["buys"]
        )
        sell_rows = "".join(
            f"<tr><td>{_e(s2['date'])} {_e(s2['time'])}</td>"
            f"<td>매도</td><td style='text-align:right'>{s2['qty']:,}주</td>"
            f"<td style='text-align:right'>{s2['price']:,.0f}원</td>"
            f"<td style='text-align:right'>{s2['qty']*s2['price']:,.0f}원</td>"
            f"<td style='color:#aaa;font-size:11px'>{_e(s2.get('market',''))}</td></tr>"
            for s2 in r["sells"]
        )

        rem = r.get("remaining_qty", 0)
        rem_html = f'<span style="color:#fb8c00">{rem:,}주 미청산</span>' if rem else "전량 청산"

        stock_cards += f"""
<div style="background:#1e1e1e;border:1px solid #333;border-radius:8px;padding:16px;margin-bottom:16px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <span style="font-size:16px;font-weight:700">{_e(r['name'])} ({_e(r['code'])}){is_inter_html}</span>
    <span style="font-size:14px">{rp_html}{wl_html}</span>
  </div>
  <div style="margin-bottom:8px">{tag_html}</div>
  <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:13px;color:#bbb;margin-bottom:8px">
    <span>신호일: {_e(r.get('sig_date') or '-')}</span>
    <span>신호가: {f"{r['sig_price']:,.0f}원" if r.get('sig_price') else '-'}</span>
    <span>평균단가: {r['avg_cost']:,.0f}원</span>
    <span>비중: {f"{r['weight_pct']:.1f}%" if r.get('weight_pct') is not None else '-'}</span>
    <span>잔여: {rem_html}</span>
    <span>실현손익: {_fmt_krw(r['realized'])}</span>
  </div>
  <details style="margin-top:4px">
    <summary style="cursor:pointer;color:#888;font-size:12px">체결내역 보기</summary>
    <table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:8px">
      <thead><tr style="color:#888">
        <th style="text-align:left">일시</th><th style="text-align:left">구분</th>
        <th style="text-align:right">수량</th><th style="text-align:right">가격</th>
        <th style="text-align:right">금액</th><th></th>
      </tr></thead>
      <tbody>{buy_rows}{sell_rows}</tbody>
    </table>
  </details>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>매매 원칙 분석 {report_date}</title>
<style>
  body{{background:#121212;color:#e0e0e0;font-family:'Malgun Gothic',sans-serif;margin:0;padding:24px}}
  h1{{font-size:20px;margin-bottom:4px}}
  .card{{background:#1a1a1a;border-radius:8px;padding:16px;margin-bottom:16px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:6px 8px;border-bottom:1px solid #2a2a2a;text-align:left}}
  th{{color:#888;font-size:12px;font-weight:500}}
  a{{color:#29b6f6}}
</style>
</head>
<body>
<h1>📋 매매 원칙 분석 리포트</h1>
<p style="color:#888;font-size:13px">기준 파일: {_e(csv_name)} · 생성: {report_date}</p>

<div class="card">
  <div style="display:flex;gap:32px;flex-wrap:wrap">
    <div>
      <div style="font-size:12px;color:#888">원칙 준수율</div>
      <div style="font-size:32px;font-weight:700;color:{cr_color}">{f"{cr:.1f}%" if cr is not None else "-"}</div>
      <div style="font-size:12px;color:#aaa">{s['compliant_stocks']}/{s['total_stocks']} 종목</div>
    </div>
    <div>
      <div style="font-size:12px;color:#888">기간 실현 손익</div>
      <div style="font-size:28px;font-weight:700;{_pct_color(rp)}">{_fmt_krw(s['total_realized'])}</div>
      <div style="font-size:12px;color:#aaa">{_fmt_pct(rp)} (매수금액 기준)</div>
    </div>
    <div>
      <div style="font-size:12px;color:#888">신호 종목 / 교집합</div>
      <div style="font-size:24px;font-weight:700">{s['signal_count']} / {s['inter_count']}</div>
      <div style="font-size:12px;color:#aaa">총 {s['total_stocks']}개 종목</div>
    </div>
    <div>
      <div style="font-size:12px;color:#888">총 매수금액</div>
      <div style="font-size:20px;font-weight:600">{s['total_buy_value']:,.0f}원</div>
    </div>
  </div>
</div>

<div class="card">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">위반 태그 집계</div>
  {'<p style="color:#4caf50">위반 없음</p>' if not tag_rows else f'<table><thead><tr><th>태그</th><th>횟수</th><th>설명</th></tr></thead><tbody>{tag_rows}</tbody></table>'}
</div>

<div style="font-size:14px;font-weight:600;margin-bottom:8px">종목별 분석</div>
{stock_cards}
</body>
</html>"""


# ── 누적 이력 ────────────────────────────────────────────────

def _update_history(result: dict, report_date: str) -> None:
    """trade_history.json / compliance_history.json 갱신."""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    # compliance_history: 날짜별 준수율 + P&L
    hist_path = _HISTORY_DIR / "compliance_history.json"
    hist: list[dict] = []
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:
            hist = []

    s = result["summary"]
    entry = {
        "date":              report_date,
        "compliance_rate":   s.get("compliance_rate"),
        "total_realized":    s.get("total_realized"),
        "total_realized_pct": s.get("total_realized_pct"),
        "total_stocks":      s.get("total_stocks"),
        "tag_counts":        s.get("tag_counts"),
    }
    # 같은 날짜면 덮어쓰기
    hist = [h for h in hist if h.get("date") != report_date]
    hist.append(entry)
    hist.sort(key=lambda x: x.get("date", ""))
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

    # trade_history: 종목별 원시 데이터 append
    trade_hist_path = _HISTORY_DIR / "trade_history.json"
    all_trades: list[dict] = []
    if trade_hist_path.exists():
        try:
            all_trades = json.loads(trade_hist_path.read_text(encoding="utf-8"))
        except Exception:
            all_trades = []

    # 해당 날짜 항목 제거 후 재추가
    all_trades = [t for t in all_trades if t.get("report_date") != report_date]
    for r in result["stocks"]:
        all_trades.append({
            "report_date":   report_date,
            "code":          r["code"],
            "name":          r["name"],
            "sig_date":      r.get("sig_date"),
            "is_inter":      r.get("is_inter"),
            "realized":      r.get("realized"),
            "realized_pct":  r.get("realized_pct"),
            "weight_pct":    r.get("weight_pct"),
            "tags":          r["tags"],
        })
    all_trades.sort(key=lambda x: (x.get("report_date",""), x.get("code","")))
    trade_hist_path.write_text(json.dumps(all_trades, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_path(base: Path, stem: str, suffix: str, overwrite: bool) -> Path:
    p = base / f"{stem}{suffix}"
    if overwrite or not p.exists():
        return p
    i = 2
    while True:
        candidate = base / f"{stem}_v{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


# ── CLI ──────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="HTS 매매내역 원칙 준수 분석",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python -m scripts.trade_analyzer --latest --open\n"
            "  python -m scripts.trade_analyzer 경로/파일.csv --overwrite\n"
        ),
    )
    parser.add_argument("csv_path", nargs="?",  help="HTS CSV 경로 (생략 또는 --latest 시 data/weekly_trading_review/ 최신 파일)")
    parser.add_argument("--latest",    action="store_true", help="data/weekly_trading_review/ 에서 가장 최근 CSV 자동 선택")
    parser.add_argument("--open",      action="store_true", help="결과 HTML 브라우저로 열기")
    parser.add_argument("--overwrite", action="store_true", help="오늘 날짜 기존 결과 파일 덮어쓰기")
    args = parser.parse_args()

    # CSV 경로 결정
    if args.csv_path and not args.latest:
        csv_path = Path(args.csv_path)
    else:
        _TRADES_DIR.mkdir(parents=True, exist_ok=True)
        candidates = sorted(_TRADES_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print(f"[오류] {_TRADES_DIR} 에 CSV 파일이 없습니다.")
            print(f"       HTS에서 내보낸 CSV를 해당 폴더에 복사 후 다시 실행하세요.")
            sys.exit(1)
        csv_path = candidates[0]
        print(f"[자동선택] {csv_path.name}")

    print(f"[분석] {csv_path.name}")
    trades = _load_hts_csv(csv_path)
    if not trades:
        print("[오류] 유효한 체결 내역이 없습니다.")
        sys.exit(1)
    print(f"  체결 {len(trades)}건 로드")

    cache  = SignalCache()
    result = _analyze(trades, cache)

    s = result["summary"]
    print(f"  종목 {s['total_stocks']}개 | 준수율 {s.get('compliance_rate','?')}%"
          f" | 손익 {s['total_realized']:+,.0f}원 ({_fmt_pct(s.get('total_realized_pct'))})")

    # 출력 경로
    report_date = date.today().strftime("%Y-%m-%d")
    stem        = f"trade_review_{report_date}"
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    default_html = _REPORT_DIR  / f"{stem}.html"
    default_json = _HISTORY_DIR / f"{stem}.json"
    if not args.overwrite and (default_html.exists() or default_json.exists()):
        print(f"  [주의] 오늘({report_date}) 리포트가 이미 존재합니다.")
        print(f"         새 버전으로 저장됩니다. 덮어쓰려면 --overwrite 를 사용하세요.")

    html_path = _safe_path(_REPORT_DIR, stem, ".html", args.overwrite)
    json_path = _safe_path(_HISTORY_DIR, stem, ".json", args.overwrite)

    html = _generate_html(result, csv_path.name, report_date)
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    _update_history(result, report_date)

    print(f"  HTML → {html_path}")
    print(f"  JSON → {json_path}")

    if args.open:
        webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    main()
