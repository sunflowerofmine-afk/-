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
    # 신호/진입 분류
    "NON_SIGNAL_TRADE":       "시스템 미신호 종목 매수",
    "SIGNAL_FILE_MISSING":    "신호 파일 없음 (확인불가)",
    "NON_INTERSECTION_TRADE": "교집합 미포함 종목 매수",
    "NOT_CLOSE_ENTRY":        "종가진입 원칙 위반 (정규장 시간/가격 불일치)",
    "D1_CHASE_ENTRY":         "D+1 장초 고점 추격매수",
    "REVERSE_AT_EXIT_ZONE":   "D+1 09:20~09:40 고점 역추격매수",
    "AVERAGING_DOWN":         "물타기 (하락 후 추가매수)",
    "ADDITIONAL_BUY":         "추가매수 (물타기 외, 정보용)",
    "RE_ENTRY":               "재진입 (당일 청산 후 재매수)",
    # NXT 분류 (AFTER_1750_NXT_ENTRY는 시간 정보 태그 — 단독 위반 아님)
    "NXT_ENTRY":              "NXT 시간외 단일가 체결 발생 (정보용)",
    "AFTER_1750_NXT_ENTRY":   "17:50 이후 NXT 체결 시간 태그 (단독 위반 아님)",
    "CONDITIONAL_NXT_ENTRY":  "조건부 허용 NXT 진입 (교집합+기준가±1.5%+포지션OK)",
    "NXT_ENTRY_CAUTION":      "NXT 주의 진입 (기준가 대비 +1.5~3%)",
    "NXT_CHASE_ENTRY":        "NXT 추격 진입 (기준가 대비 +3% 초과)",
    "PRICE_REFERENCE_MISSING": "기준 가격 확인불가 (signals/gap_results 없음, 정보용)",
    # 포지션 규칙
    "OVERSIZED_POSITION":          "과대 포지션 (30% 초과)",
    "POSITION_RULE_OK":            "포지션 비중 정상 (30% 이하, 정보용)",
    "POSITION_RULE_BROKEN":        "포지션 비중 위반 (30% 초과)",
    "MAX_POSITION_COUNT_EXCEEDED": "동시 보유 종목 3개 초과",
    # D+1 청산 규칙
    "D1_EXIT_RULE_TARGET": "D+1 09:30 청산 목표 대상 (정보용)",
    "D1_EXIT_ON_TIME":     "D+1 09:20~09:40 내 청산 완료 (준수, 정보용)",
    "D1_EXIT_DELAYED":     "D+1 09:40 이후 청산 (지연)",
    "D1_EXIT_MISSED":      "D+1 미청산 (보유 지속)",
    # 갭하락 손절
    "GAP_DOWN_STOP_REQUIRED":      "D+1 시가 갭하락 -3% 이하 — 손절 대상 (정보용)",
    "GAP_DOWN_STOP_DONE":          "갭하락 손절 이행 (정보용)",
    "GAP_DOWN_STOP_MISSED":        "갭하락 손절 미이행 (강한 위반)",
    "POST_GAPDOWN_AVERAGING_DOWN": "갭하락 후 추가매수 (강한 위반)",
    # 연장 보유
    "EXTENDED_HOLD_ALLOWED":       "연장 보유 조건 충족 (갭하락 없음+수익권, 정보용)",
    "EXTENDED_HOLD_NOT_ALLOWED":   "연장 보유 조건 미충족 (정보용)",
    "UNAUTHORIZED_EXTENDED_HOLD":  "허용 조건 없는 연장 보유 (위반)",
    "EXTENDED_HOLD_PROFIT":        "연장 보유 후 수익 실현 (정보용)",
    "EXTENDED_HOLD_LOSS":          "연장 보유 후 손실 실현 (정보용)",
    "EXTENDED_HOLD_REVIEW_NEEDED": "연장 보유 판단 불가 — D+1 시가 데이터 없음 (정보용)",
}

# 진입 구간 → 한글 레이블
_ENTRY_TYPE_LABEL = {
    "REGULAR_CLOSE_ENTRY":  "정규장 종가 진입",
    "AFTER_1750_NXT_ENTRY": "17:50 이후 NXT 진입",
    "D1_CHASE_ENTRY":       "D+1 장초 추격 진입",
    "REVERSE_AT_EXIT_ZONE": "D+1 역추격 진입",
    "NOT_CLOSE_ENTRY":      "시간 위반 진입",
    "UNKNOWN":              "확인불가",
}

# 포지션 비중 경고 임계값 (%) — 표시용 레벨
_POS_WARN      = 10
_POS_ALERT     = 15
_POS_CRIT      = 30
_POS_MAX       = 40
# 위반 기준: 30% 초과이면 OVERSIZED_POSITION
_POS_VIOLATION = 30


# ── HTS CSV 파싱 ─────────────────────────────────────────────

def _load_hts_csv(path: Path) -> list[dict]:
    """영웅문S# 기간별 주문체결상세 CSV → 체결 거래 목록 반환.

    2행 쌍 구조:
      row1: 주식채널|주문번호|원주문번호|종목코드|주문유형|현금매수/매도K|주문수량|주문단가
      row2: 날짜|종목명|접수|보통매매|체결수량|체결단가|취소/정정|영웅문S#||체결시각|시장
    취소/정정 주문 제외.
    반환 필드: date, time, code, name, side, qty, price, market
    """
    try:
        raw = path.read_bytes()
        text = raw.decode("euc-kr", errors="replace")
    except Exception as e:
        raise RuntimeError(f"CSV 읽기 실패: {e}")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    data_rows = [r for r in rows[2:] if any(c.strip() for c in r)]

    trades = []
    i = 0
    while i + 1 < len(data_rows):
        row1 = data_rows[i]
        row2 = data_rows[i + 1]
        i += 2

        if len(row1) < 8 or len(row2) < 10:
            continue

        cancel_flag = row2[6].strip() if len(row2) > 6 else ""
        if "취소" in cancel_flag or "정정" in cancel_flag:
            continue

        exec_qty_str = row2[4].strip().replace(",", "") if len(row2) > 4 else ""
        if not exec_qty_str or exec_qty_str == "0":
            continue

        try:
            exec_qty = int(exec_qty_str)
        except ValueError:
            continue

        raw_date  = row2[0].strip().replace("/", "").replace("-", "")
        code      = row1[3].strip().lstrip("'").zfill(6)
        name      = row2[1].strip()
        side_raw  = row1[5].strip()
        market_s  = row2[10].strip() if len(row2) > 10 else ""
        exec_time = row2[9].strip() if len(row2) > 9 else ""
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
        self._signals: dict[tuple[str, str], dict] = {}
        self._gap:     dict[tuple[str, str], dict] = {}
        self._load_gap()
        self._load_signals()

    def _load_gap(self):
        if not _GAP_CSV.exists():
            return
        with open(_GAP_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                d = row.get("entry_date", "").strip()
                c = row.get("code", "").strip().zfill(6)
                if d and c:
                    self._gap[(d, c)] = row

    def _load_signals(self):
        for p in sorted(_SIGNALS_DIR.glob("*_signals.csv")):
            date8 = p.name[:10].replace("-", "")
            with open(p, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    code = row.get("종목코드", "").strip().zfill(6)
                    if code:
                        self._signals[(date8, code)] = row

    def find_signal(self, trade_date8: str, code: str, lookback: int = 3) -> dict | None:
        d = datetime.strptime(trade_date8, "%Y%m%d").date()
        for delta in range(lookback + 1):
            candidate = (d - timedelta(days=delta)).strftime("%Y%m%d")
            sig = self._signals.get((candidate, code))
            if sig:
                return {**sig, "_signal_date": candidate}
        for delta in range(lookback + 1):
            candidate = (d - timedelta(days=delta)).strftime("%Y%m%d")
            if self._gap.get((candidate, code)):
                return {"_signal_date": candidate, "_from_gap": True}
        return None

    def signal_price_with_source(self, signal_date8: str, code: str) -> tuple[float | None, str]:
        """기준 가격을 우선순위대로 탐색해 (가격, 출처) 반환.
        1순위: signals signal_price
        2순위: signals entry_reference_price
        3순위: signals regular_close_price
        4순위: gap_results entry_price
        """
        sig = self._signals.get((signal_date8, code))
        if sig is not None:
            for field, label in [
                ("signal_price",          "signal_price"),
                ("entry_reference_price", "entry_reference_price"),
                ("regular_close_price",   "regular_close_price"),
            ]:
                try:
                    v = float(sig.get(field) or 0)
                    if v > 0:
                        return v, label
                except (ValueError, TypeError):
                    pass
        gap = self._gap.get((signal_date8, code))
        if gap is not None:
            try:
                v = float(gap.get("entry_price") or 0)
                if v > 0:
                    return v, "gap_results entry_price"
            except (ValueError, KeyError, TypeError):
                pass
        return None, "없음"

    def signal_price(self, signal_date8: str, code: str) -> float | None:
        price, _ = self.signal_price_with_source(signal_date8, code)
        return price

    def is_inter(self, signal_date8: str, code: str) -> bool | None:
        sig = self._signals.get((signal_date8, code))
        if sig is not None:
            v = sig.get("in_inter", "").strip().lower()
            if v in ("true", "1", "yes"):
                return True
            if v in ("false", "0", "no"):
                return False
            # in_inter 컬럼 없거나 빈 값이면 gap_results로 폴백
        gap = self._gap.get((signal_date8, code))
        if gap is not None:
            gv = gap.get("in_inter", "").strip()
            if gv:
                return gv == "True"
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
    result: dict[str, list[dict]] = {}
    for t in trades:
        result.setdefault(t["code"], []).append(t)
    return result


def _calc_pnl(buys: list[dict], sells: list[dict]) -> dict:
    total_buy_qty    = sum(t["qty"] for t in buys)
    total_buy_value  = sum(t["qty"] * t["price"] for t in buys)
    total_sell_qty   = sum(t["qty"] for t in sells)
    total_sell_value = sum(t["qty"] * t["price"] for t in sells)
    avg_cost = total_buy_value / total_buy_qty if total_buy_qty else 0
    remaining = total_buy_qty - total_sell_qty
    realized = total_sell_value - avg_cost * total_sell_qty if avg_cost else 0
    realized_pct = realized / (avg_cost * total_sell_qty) * 100 if avg_cost and total_sell_qty else None
    return {
        "avg_cost":         round(avg_cost),
        "total_buy_qty":    total_buy_qty,
        "total_buy_value":  total_buy_value,
        "total_sell_qty":   total_sell_qty,
        "total_sell_value": total_sell_value,
        "remaining_qty":    remaining,
        "realized":         round(realized),
        "realized_pct":     round(realized_pct, 2) if realized_pct is not None else None,
    }


# ── 위반 태그 탐지 ────────────────────────────────────────────

def _detect_violations(
    code: str,
    name: str,
    code_trades: list[dict],
    cache: SignalCache,
    period_total_buy: float,
    base_capital: float,
) -> tuple[list[str], str, float | None, str, bool]:
    """종목 위반 태그 리스트 + 진입 구간 반환.

    Returns: (tags, entry_type, entry_price_vs_signal_pct, sig_price_source, d1_exit_target)
    """
    tags: list[str] = []
    buys  = [t for t in code_trades if t["side"] == "buy"]
    sells = [t for t in code_trades if t["side"] == "sell"]
    if not buys:
        return tags, "UNKNOWN", None, "없음", False

    _total_buy_qty = sum(b["qty"] for b in buys)
    _total_buy_val = sum(b["qty"] * b["price"] for b in buys)
    avg_cost       = _total_buy_val / _total_buy_qty if _total_buy_qty else 0.0

    first_buy = min(buys, key=lambda t: (t["date"], t["time"]))
    signal = cache.find_signal(first_buy["date"], code)

    if signal is None:
        if not cache.has_signal_file(first_buy["date"]):
            tags.append("SIGNAL_FILE_MISSING")
        else:
            tags.append("NON_SIGNAL_TRADE")
        _check_position_size(tags, buys, period_total_buy, base_capital)
        _check_additional_and_re_entry(tags, buys, sells)
        return tags, "UNKNOWN", None, "없음", False

    sig_date = signal["_signal_date"]
    sig_price, sig_price_source = cache.signal_price_with_source(sig_date, code)
    is_inter = cache.is_inter(sig_date, code)

    if sig_price is None:
        tags.append("PRICE_REFERENCE_MISSING")

    if is_inter is False:
        tags.append("NON_INTERSECTION_TRADE")

    entry_type, price_pct = _check_entry_timing(tags, buys, first_buy, sig_date, sig_price, signal)
    _check_position_size(tags, buys, period_total_buy, base_capital)
    _check_additional_and_re_entry(tags, buys, sells)
    d1_exit_target = _check_d1_exit_and_stop(tags, buys, sells, sig_date, code, cache, avg_cost, entry_type)
    _classify_nxt_entry(tags, entry_type, price_pct, is_inter)

    return tags, entry_type, price_pct, sig_price_source, d1_exit_target


def _check_entry_timing(
    tags: list[str],
    buys: list[dict],
    first_buy: dict,
    sig_date: str,
    sig_price: float | None,
    signal: dict,
) -> tuple[str, float | None]:
    """진입 구간 판정 + 타이밍 관련 태그(NXT_ENTRY, NOT_CLOSE_ENTRY, D1_*, REVERSE_*) 추가.

    entry_type은 첫 매수 기준으로만 결정.
    NOT_CLOSE_ENTRY는 sig_date + 15:30 이전 정규장 구간 이탈 시에만 적용.
    15:30 이후 시간외/NXT 진입은 AFTER_1750_NXT_ENTRY 계열로 처리.

    Returns: (entry_type, entry_price_vs_signal_pct)
    """
    entry_type = "UNKNOWN"
    price_pct: float | None = None

    fb_px     = first_buy["price"]
    fb_date   = first_buy["date"]
    fb_time   = first_buy["time"]
    fb_market = first_buy.get("market", "")
    fb_h, fb_m = _hm(fb_time)

    if sig_price and sig_price > 0:
        price_pct = round((fb_px - sig_price) / sig_price * 100, 2)

    # ── entry_type: first_buy 기준 ──────────────────────────
    if fb_date == sig_date:
        if (fb_h, fb_m) < (15, 30):
            # 정규장 시간대 (15:30 이전)
            in_window = (14, 50) <= (fb_h, fb_m) <= (15, 30)
            price_ok  = sig_price and abs(fb_px - sig_price) / sig_price <= 0.015
            entry_type = "REGULAR_CLOSE_ENTRY" if (in_window or price_ok) else "NOT_CLOSE_ENTRY"
        else:
            # 15:30 이후 시간외 구간
            is_nxt = "NXT" in fb_market.upper()
            if is_nxt and (fb_h, fb_m) >= (17, 50) and signal is not None:
                entry_type = "AFTER_1750_NXT_ENTRY"
            # 15:30~17:50 또는 비NXT 시간외 → UNKNOWN
    elif fb_date > sig_date:
        # D+1 이후 진입 — REVERSE_AT_EXIT_ZONE이 D1_CHASE_ENTRY의 부분집합이므로 먼저 체크
        if (9, 20) <= (fb_h, fb_m) <= (9, 40) and sig_price and fb_px >= sig_price * 1.03:
            entry_type = "REVERSE_AT_EXIT_ZONE"
        elif (8, 0) <= (fb_h, fb_m) <= (10, 0) and sig_price and fb_px >= sig_price * 1.03:
            entry_type = "D1_CHASE_ENTRY"

    # ── 전체 매수 순회: NXT_ENTRY / NOT_CLOSE_ENTRY / D1_* 태그 ──
    for b in buys:
        t_date   = b["date"]
        t_time   = b["time"]
        t_px     = b["price"]
        market   = b.get("market", "")
        h, m     = _hm(t_time)

        # NXT 체결 여부 (정보용 — 단독으로 위반 아님)
        if "NXT" in market.upper() and "NXT_ENTRY" not in tags:
            tags.append("NXT_ENTRY")

        if t_date == sig_date:
            if (h, m) < (15, 30):
                # 정규장 구간 이탈만 NOT_CLOSE_ENTRY
                in_window = (14, 50) <= (h, m) <= (15, 30)
                price_ok  = sig_price and abs(t_px - sig_price) / sig_price <= 0.015
                if not in_window and not price_ok and "NOT_CLOSE_ENTRY" not in tags:
                    tags.append("NOT_CLOSE_ENTRY")
            # 15:30 이후는 _classify_nxt_entry()에서 처리
        else:
            # D+1 이후 추격 태그
            if (9, 20) <= (h, m) <= (9, 40):
                if sig_price and t_px >= sig_price * 1.03 and "REVERSE_AT_EXIT_ZONE" not in tags:
                    tags.append("REVERSE_AT_EXIT_ZONE")
            if (8, 0) <= (h, m) <= (10, 0):
                if sig_price and t_px >= sig_price * 1.03 and "D1_CHASE_ENTRY" not in tags:
                    tags.append("D1_CHASE_ENTRY")

    return entry_type, price_pct


def _classify_nxt_entry(
    tags: list[str],
    entry_type: str,
    price_pct: float | None,
    is_inter: bool | None,
) -> None:
    """17:50 NXT 진입을 가격·조건 기준으로 세분류.

    AFTER_1750_NXT_ENTRY 태그를 추가하고,
    가격/조건에 따라 CONDITIONAL_NXT_ENTRY / NXT_ENTRY_CAUTION / NXT_CHASE_ENTRY 중 하나 추가.
    """
    if entry_type != "AFTER_1750_NXT_ENTRY":
        return
    tags.append("AFTER_1750_NXT_ENTRY")
    if price_pct is None:
        return
    if price_pct > 3.0:
        tags.append("NXT_CHASE_ENTRY")
    elif price_pct > 1.5:
        tags.append("NXT_ENTRY_CAUTION")
    elif (
        is_inter is True
        and "AVERAGING_DOWN" not in tags
        and "OVERSIZED_POSITION" not in tags
    ):
        tags.append("CONDITIONAL_NXT_ENTRY")


def _check_position_size(
    tags: list[str],
    buys: list[dict],
    period_total_buy: float,
    base_capital: float,
) -> None:
    denom = base_capital if base_capital > 0 else period_total_buy
    if not denom:
        return
    stock_buy = sum(b["qty"] * b["price"] for b in buys)
    pct = stock_buy / denom * 100
    if pct > _POS_VIOLATION:
        tags.append("OVERSIZED_POSITION")
        tags.append("POSITION_RULE_BROKEN")
    else:
        tags.append("POSITION_RULE_OK")


def _check_additional_and_re_entry(
    tags: list[str],
    buys: list[dict],
    sells: list[dict],
) -> None:
    if len(buys) < 2:
        return

    sorted_buys  = sorted(buys,  key=lambda t: (t["date"], t["time"]))
    sorted_sells = sorted(sells, key=lambda t: (t["date"], t["time"]))

    avg_cost  = 0.0
    total_qty = 0
    for i, b in enumerate(sorted_buys):
        if i == 0:
            avg_cost  = b["price"]
            total_qty = b["qty"]
            continue

        cum_sell_before = sum(
            s["qty"] for s in sorted_sells
            if (s["date"], s["time"]) < (b["date"], b["time"])
        )
        if cum_sell_before >= total_qty:
            if "RE_ENTRY" not in tags:
                tags.append("RE_ENTRY")
            avg_cost  = b["price"]
            total_qty = b["qty"]
            continue

        if b["price"] < avg_cost:
            if "AVERAGING_DOWN" not in tags:
                tags.append("AVERAGING_DOWN")
        else:
            if "ADDITIONAL_BUY" not in tags:
                tags.append("ADDITIONAL_BUY")

        new_val    = avg_cost * total_qty + b["price"] * b["qty"]
        total_qty += b["qty"]
        avg_cost   = new_val / total_qty if total_qty else avg_cost


def _check_d1_exit_and_stop(
    tags: list[str],
    buys: list[dict],
    sells: list[dict],
    sig_date: str,
    code: str,
    cache: "SignalCache",
    avg_cost: float,
    entry_type: str,
) -> bool:
    """D+1 청산 타이밍 + 갭하락 손절 + 연장 보유 태그 추가.

    D1_CHASE_ENTRY / REVERSE_AT_EXIT_ZONE / UNKNOWN 진입은 적용 제외.
    Returns True if D1_EXIT_RULE_TARGET 태그가 설정된 경우.
    """
    if entry_type in ("D1_CHASE_ENTRY", "REVERSE_AT_EXIT_ZONE", "UNKNOWN"):
        return False

    sig_price = cache.signal_price(sig_date, code)
    d1_open   = cache.d1_open(sig_date, code)
    d1        = (datetime.strptime(sig_date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    d1_sells  = [s for s in sells if s["date"] == d1]

    tags.append("D1_EXIT_RULE_TARGET")

    # 갭하락 -3% 체크 (gap_results 데이터 있을 때만)
    gap_down_required = False
    if d1_open is not None and sig_price and sig_price > 0:
        gap_pct = (d1_open - sig_price) / sig_price * 100
        if gap_pct <= -3.0:
            tags.append("GAP_DOWN_STOP_REQUIRED")
            gap_down_required = True

    # 갭하락 상황에서 D+1 추가매수 (강한 위반)
    if gap_down_required:
        if any(b["date"] >= d1 for b in buys):
            tags.append("POST_GAPDOWN_AVERAGING_DOWN")

    if not d1_sells:
        # D+1 매도 없음
        tags.append("D1_EXIT_MISSED")
        if gap_down_required:
            tags.append("GAP_DOWN_STOP_MISSED")
            tags.append("UNAUTHORIZED_EXTENDED_HOLD")
        else:
            if d1_open is None:
                tags.append("EXTENDED_HOLD_REVIEW_NEEDED")
            elif avg_cost and d1_open > avg_cost:
                tags.append("EXTENDED_HOLD_ALLOWED")
            else:
                tags.append("EXTENDED_HOLD_NOT_ALLOWED")
                tags.append("UNAUTHORIZED_EXTENDED_HOLD")
    else:
        first_sell_time = min(d1_sells, key=lambda s: s["time"])["time"]
        h, m = _hm(first_sell_time)
        in_window = (9, 20) <= (h, m) <= (9, 40)

        if in_window:
            tags.append("D1_EXIT_ON_TIME")
            if gap_down_required:
                tags.append("GAP_DOWN_STOP_DONE")
        else:
            tags.append("D1_EXIT_DELAYED")
            if gap_down_required:
                tags.append("GAP_DOWN_STOP_MISSED")
            if d1_open is None:
                tags.append("EXTENDED_HOLD_REVIEW_NEEDED")
            elif gap_down_required:
                tags.append("UNAUTHORIZED_EXTENDED_HOLD")
            elif avg_cost and d1_open > avg_cost:
                tags.append("EXTENDED_HOLD_ALLOWED")
                d1_sell_val = sum(s["qty"] * s["price"] for s in d1_sells)
                d1_sell_qty = sum(s["qty"] for s in d1_sells)
                d1_pnl      = d1_sell_val - avg_cost * d1_sell_qty
                if d1_pnl >= 0:
                    tags.append("EXTENDED_HOLD_PROFIT")
                else:
                    tags.append("EXTENDED_HOLD_LOSS")
            else:
                tags.append("EXTENDED_HOLD_NOT_ALLOWED")
                tags.append("UNAUTHORIZED_EXTENDED_HOLD")

    return True


def _hm(time_str: str) -> tuple[int, int]:
    parts = time_str.replace(".", ":").split(":")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return 0, 0


# ── 분석 실행 ─────────────────────────────────────────────────

def _analyze(trades: list[dict], cache: SignalCache) -> dict:
    buys_all         = [t for t in trades if t["side"] == "buy"]
    period_total_buy = sum(t["qty"] * t["price"] for t in buys_all)
    base_capital     = float(TRADE_ANALYZER_BASE_CAPITAL) if TRADE_ANALYZER_BASE_CAPITAL else 0
    denom            = base_capital if base_capital > 0 else period_total_buy

    by_code = _build_positions(trades)
    results = []

    for code, code_trades in by_code.items():
        name  = code_trades[0]["name"]
        buys  = [t for t in code_trades if t["side"] == "buy"]
        sells = [t for t in code_trades if t["side"] == "sell"]
        if not buys:
            continue

        pnl  = _calc_pnl(buys, sells)
        tags, entry_type, price_pct, sig_price_source, d1_exit_target = _detect_violations(
            code, name, code_trades, cache, period_total_buy, base_capital
        )

        stock_buy   = pnl["total_buy_value"]
        weight_pct  = round(stock_buy / denom * 100, 1) if denom else None
        weight_level = ""
        if weight_pct is not None:
            if weight_pct >= _POS_MAX:     weight_level = "최우선경고"
            elif weight_pct >= _POS_CRIT:  weight_level = "심각"
            elif weight_pct >= _POS_ALERT: weight_level = "경고"
            elif weight_pct >= _POS_WARN:  weight_level = "주의"

        first_buy = min(buys, key=lambda t: (t["date"], t["time"]))
        signal    = cache.find_signal(first_buy["date"], code)
        sig_date  = signal["_signal_date"] if signal else None
        sig_price = cache.signal_price(sig_date, code) if sig_date else None
        is_inter  = cache.is_inter(sig_date, code)     if sig_date else None

        results.append({
            "code":                      code,
            "name":                      name,
            "sig_date":                  sig_date,
            "sig_price":                 sig_price,
            "is_inter":                  is_inter,
            "avg_cost":                  pnl["avg_cost"],
            "total_buy_qty":             pnl["total_buy_qty"],
            "total_buy_value":           pnl["total_buy_value"],
            "total_sell_qty":            pnl["total_sell_qty"],
            "total_sell_value":          pnl["total_sell_value"],
            "remaining_qty":             pnl["remaining_qty"],
            "realized":                  pnl["realized"],
            "realized_pct":              pnl["realized_pct"],
            "weight_pct":                weight_pct,
            "weight_level":              weight_level,
            "tags":                      tags,
            "entry_type":                entry_type,
            "entry_price_vs_signal_pct": price_pct,
            "sig_price_source":          sig_price_source,
            "d1_exit_target":            "D1_EXIT_RULE_TARGET" in tags,
            "buys":                      buys,
            "sells":                     sells,
        })

    total_realized      = sum(r["realized"] for r in results)
    total_buy_value     = sum(r["total_buy_value"] for r in results)
    total_realized_pct  = total_realized / total_buy_value * 100 if total_buy_value else None

    signal_trades = [
        r for r in results
        if "NON_SIGNAL_TRADE" not in r["tags"] and "SIGNAL_FILE_MISSING" not in r["tags"]
    ]
    inter_trades = [r for r in signal_trades if r.get("is_inter")]

    tag_counts: dict[str, int] = {}
    for r in results:
        for t in r["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    total_n         = len(results)
    compliant_n     = len([r for r in results if not r["tags"]])
    compliance_rate = round(compliant_n / total_n * 100, 1) if total_n else None

    # ── 항목별 준수율 ─────────────────────────────────────────
    def _item_rate(n, d):
        return round(n / d * 100, 1) if d else None

    _sig_confirmed = [r for r in results if "NON_SIGNAL_TRADE" not in r["tags"] and "SIGNAL_FILE_MISSING" not in r["tags"]]
    _price_avail   = [r for r in _sig_confirmed if r.get("sig_price") is not None]
    _d1_targets    = [r for r in results if "D1_EXIT_RULE_TARGET" in r["tags"]]

    _bot_n   = len([r for r in results if "NON_SIGNAL_TRADE" not in r["tags"]])
    _inter_n = len([r for r in _sig_confirmed if "NON_INTERSECTION_TRADE" not in r["tags"]])
    _close_n = len([r for r in _price_avail if r.get("entry_type") == "REGULAR_CLOSE_ENTRY"])
    _d1_n    = len([r for r in _d1_targets if "D1_EXIT_ON_TIME" in r["tags"]])
    _avg_n   = len([r for r in results if "AVERAGING_DOWN" not in r["tags"]])
    _pos_n   = len([r for r in results if "OVERSIZED_POSITION" not in r["tags"]])

    item_compliance = {
        "bot_signal_rate":   _item_rate(_bot_n, total_n),
        "bot_signal_n":      _bot_n,
        "bot_signal_denom":  total_n,
        "inter_rate":        _item_rate(_inter_n, len(_sig_confirmed)),
        "inter_n":           _inter_n,
        "inter_denom":       len(_sig_confirmed),
        "close_entry_rate":  _item_rate(_close_n, len(_price_avail)),
        "close_entry_n":     _close_n,
        "close_entry_denom": len(_price_avail),
        "d1_exit_rate":      _item_rate(_d1_n, len(_d1_targets)),
        "d1_exit_n":         _d1_n,
        "d1_exit_denom":     len(_d1_targets),
        "avg_down_rate":     _item_rate(_avg_n, total_n),
        "avg_down_n":        _avg_n,
        "avg_down_denom":    total_n,
        "pos_limit_rate":    _item_rate(_pos_n, total_n),
        "pos_limit_n":       _pos_n,
        "pos_limit_denom":   total_n,
    }

    # ── MAX_POSITION_COUNT_EXCEEDED (기간 레벨 체크) ─────────
    _pos_periods: list[tuple[str, str, str]] = []
    for _r in results:
        _rb = _r.get("buys", [])
        _rs = _r.get("sells", [])
        if _rb:
            _od = min(b["date"] for b in _rb)
            _cd = max(s["date"] for s in _rs) if _rs else "99991231"
            _pos_periods.append((_r["code"], _od, _cd))
    _check_dates: set[str] = set()
    for _, _od, _cd in _pos_periods:
        _check_dates.add(_od)
        if _cd != "99991231":
            _check_dates.add(_cd)
    _exceeded_codes: set[str] = set()
    for _chk in _check_dates:
        _sim = [c for c, od, cd in _pos_periods if od <= _chk <= cd]
        if len(_sim) > 3:
            _exceeded_codes.update(_sim)
    for _r in results:
        if _r["code"] in _exceeded_codes and "MAX_POSITION_COUNT_EXCEEDED" not in _r["tags"]:
            _r["tags"].append("MAX_POSITION_COUNT_EXCEEDED")

    # ── 교훈 (태그별 손실 집계) ───────────────────────────────
    _INFO_TAGS = {
        "NXT_ENTRY", "ADDITIONAL_BUY", "PRICE_REFERENCE_MISSING",
        "AFTER_1750_NXT_ENTRY",
        "D1_EXIT_RULE_TARGET", "D1_EXIT_ON_TIME",
        "GAP_DOWN_STOP_REQUIRED", "GAP_DOWN_STOP_DONE",
        "EXTENDED_HOLD_ALLOWED", "EXTENDED_HOLD_PROFIT", "EXTENDED_HOLD_LOSS",
        "EXTENDED_HOLD_NOT_ALLOWED", "EXTENDED_HOLD_REVIEW_NEEDED",
        "POSITION_RULE_OK",
    }
    _tag_pnl: dict[str, float] = {}
    for r in results:
        for t in r["tags"]:
            if t not in _INFO_TAGS:
                _tag_pnl[t] = _tag_pnl.get(t, 0) + (r.get("realized") or 0)
    _violation_tag_counts = {t: v for t, v in tag_counts.items() if t not in _INFO_TAGS}
    lesson: dict = {}
    if _tag_pnl:
        _loss_items = [(t, v) for t, v in _tag_pnl.items() if v < 0]
        if _loss_items:
            wt, wv = min(_loss_items, key=lambda x: x[1])
            lesson["worst_loss_tag"]    = wt
            lesson["worst_loss_amount"] = wv
    if _violation_tag_counts:
        mft = max(_violation_tag_counts, key=_violation_tag_counts.get)
        lesson["most_frequent_tag"] = mft
        lesson["most_frequent_n"]   = _violation_tag_counts[mft]

    # 진입 방식별 집계
    entry_counts: dict[str, int] = {k: 0 for k in _ENTRY_TYPE_LABEL}
    entry_pnl:    dict[str, int] = {k: 0 for k in _ENTRY_TYPE_LABEL}
    for r in results:
        et = r.get("entry_type", "UNKNOWN")
        entry_counts[et] = entry_counts.get(et, 0) + 1
        entry_pnl[et]    = entry_pnl.get(et, 0) + r.get("realized", 0)

    def _cnt(et): return entry_counts.get(et, 0)
    def _tag_cnt(tag): return sum(1 for r in results if tag in r["tags"])
    def _rate(n): return round(n / total_n * 100, 1) if total_n else 0

    entry_stats = {
        "regular_close_n":      _cnt("REGULAR_CLOSE_ENTRY"),
        "after_1750_nxt_n":     _cnt("AFTER_1750_NXT_ENTRY"),
        "conditional_nxt_n":    _tag_cnt("CONDITIONAL_NXT_ENTRY"),
        "nxt_caution_n":        _tag_cnt("NXT_ENTRY_CAUTION"),
        "nxt_chase_n":          _tag_cnt("NXT_CHASE_ENTRY"),
        "d1_chase_n":           _cnt("D1_CHASE_ENTRY"),
        "reverse_n":            _cnt("REVERSE_AT_EXIT_ZONE"),
        "unknown_n":            _cnt("UNKNOWN"),
        "regular_close_rate":   _rate(_cnt("REGULAR_CLOSE_ENTRY")),
        "conditional_nxt_rate": _rate(_tag_cnt("CONDITIONAL_NXT_ENTRY")),
        "nxt_chase_rate":       _rate(_tag_cnt("NXT_CHASE_ENTRY") + _tag_cnt("NXT_ENTRY_CAUTION")),
        "d1_chase_rate":        _rate(_cnt("D1_CHASE_ENTRY") + _cnt("REVERSE_AT_EXIT_ZONE")),
        "entry_pnl":            {k: v for k, v in entry_pnl.items() if v != 0},
    }

    _rule_summary = {
        "position_rule": {
            "max_positions":              3,
            "max_position_pct":           30,
            "intersection_only":          True,
            "non_intersection_allowed_pct": 0,
            "d1_chase_allowed_pct":       0,
            "averaging_down_allowed_pct": 0,
        },
        "nxt_rule": {
            "reference_price_priority": [
                "signal_price", "entry_reference_price",
                "regular_close_price", "gap_results_entry_price",
            ],
            "conditional_limit_pct":           1.5,
            "chase_limit_pct":                 3.0,
            "after_1750_is_violation_by_itself": False,
        },
        "exit_rule": {
            "default_d1_exit_window":    "09:20-09:40",
            "reference_time":            "09:30",
            "gap_down_stop_pct":         -3.0,
            "extended_hold_allowed":     True,
            "extended_hold_intraday_only": True,
        },
        "kim_hyungjun_rule": {
            "trade_signal":          False,
            "hold_signal":           False,
            "observation_tag_only":  True,
            "validation_period_weeks": "4-8",
        },
    }

    return {
        "rule_summary": _rule_summary,
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
            "entry_stats":        entry_stats,
            "item_compliance":    item_compliance,
            "lesson":             lesson,
        },
        "stocks": results,
    }


# ── 누적 손익 / 4주 추세 헬퍼 ────────────────────────────────

_LESSON_TEXT = {
    "AVERAGING_DOWN":              "물타기 금지 — 손실 중 추가매수 절대 금지",
    "D1_CHASE_ENTRY":              "D+1 아침 추격 금지",
    "REVERSE_AT_EXIT_ZONE":        "D+1 09:20~09:40 역추격 금지",
    "NXT_CHASE_ENTRY":             "17:50 NXT 추격 금지 (기준가 대비 +3% 초과)",
    "NXT_ENTRY_CAUTION":           "17:50 NXT 주의 — 기준가 대비 +1.5~3% 범위 자제",
    "OVERSIZED_POSITION":          "포지션 한도 준수 — 단일 종목 30% 이하 (교집합 종목만)",
    "NON_INTERSECTION_TRADE":      "교집합 우선 원칙 — 비교집합 종목 진입 금지",
    "NON_SIGNAL_TRADE":            "봇 신호 우선 원칙 — 비신호 종목 진입 금지",
    "NOT_CLOSE_ENTRY":             "종가 진입 원칙 — 14:50~15:30 시간창 내 진입",
    "D1_EXIT_MISSED":              "D+1 09:20~09:40 청산 원칙 이행",
    "D1_EXIT_DELAYED":             "D+1 09:40 이후 청산 — 연장 조건 없으면 09:30 내 청산",
    "GAP_DOWN_STOP_MISSED":        "갭하락 -3% 손절 이행 — 갭하락 시 즉시 손절",
    "POST_GAPDOWN_AVERAGING_DOWN": "갭하락 후 추가매수 절대 금지",
    "UNAUTHORIZED_EXTENDED_HOLD":  "연장 보유 조건 없으면 D+1 09:20~09:40 청산 이행",
    "MAX_POSITION_COUNT_EXCEEDED": "동시 보유 3종목 이하 유지",
}


def _calc_cumulative_entry_pnl() -> dict[str, int]:
    """trade_history.json에서 진입 방식별 누적 실현손익 합산."""
    out = {
        "REGULAR_CLOSE_ENTRY": 0,
        "CONDITIONAL_NXT":     0,
        "NXT_CHASE":           0,
        "D1_CHASE":            0,
        "UNKNOWN":             0,
    }
    trade_hist_path = _HISTORY_DIR / "trade_history.json"
    if not trade_hist_path.exists():
        return out
    try:
        all_trades = json.loads(trade_hist_path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for t in all_trades:
        et   = t.get("entry_type", "UNKNOWN")
        tags = t.get("tags") or []
        pnl  = t.get("realized") or 0
        if et == "REGULAR_CLOSE_ENTRY":
            out["REGULAR_CLOSE_ENTRY"] += pnl
        elif "CONDITIONAL_NXT_ENTRY" in tags:
            out["CONDITIONAL_NXT"] += pnl
        elif "NXT_CHASE_ENTRY" in tags or "NXT_ENTRY_CAUTION" in tags:
            out["NXT_CHASE"] += pnl
        elif et in ("D1_CHASE_ENTRY", "REVERSE_AT_EXIT_ZONE"):
            out["D1_CHASE"] += pnl
        else:
            out["UNKNOWN"] += pnl
    return out


def _load_4week_trend() -> list[dict]:
    """compliance_history.json에서 최근 4주(최대 4개) 반환."""
    hist_path = _HISTORY_DIR / "compliance_history.json"
    if not hist_path.exists():
        return []
    try:
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
        return hist[-4:] if hist else []
    except Exception:
        return []


# ── HTML 리포트 생성 ──────────────────────────────────────────

_TAG_COLOR = {
    # 신호/진입
    "NON_SIGNAL_TRADE":       "#e53935",
    "SIGNAL_FILE_MISSING":    "#9e9e9e",
    "NON_INTERSECTION_TRADE": "#fb8c00",
    "NOT_CLOSE_ENTRY":        "#e53935",
    "D1_CHASE_ENTRY":         "#e53935",
    "REVERSE_AT_EXIT_ZONE":   "#c62828",
    "AVERAGING_DOWN":         "#e53935",
    "ADDITIONAL_BUY":         "#546e7a",
    "RE_ENTRY":               "#fb8c00",
    # NXT
    "NXT_ENTRY":              "#546e7a",
    "AFTER_1750_NXT_ENTRY":   "#0288d1",
    "CONDITIONAL_NXT_ENTRY":  "#00897b",
    "NXT_ENTRY_CAUTION":      "#f57c00",
    "NXT_CHASE_ENTRY":        "#c62828",
    "PRICE_REFERENCE_MISSING": "#607d8b",
    # 포지션
    "OVERSIZED_POSITION":          "#8e24aa",
    "POSITION_RULE_OK":            "#4caf50",
    "POSITION_RULE_BROKEN":        "#8e24aa",
    "MAX_POSITION_COUNT_EXCEEDED": "#c62828",
    # D+1 청산
    "D1_EXIT_RULE_TARGET": "#546e7a",
    "D1_EXIT_ON_TIME":     "#4caf50",
    "D1_EXIT_DELAYED":     "#fb8c00",
    "D1_EXIT_MISSED":      "#e53935",
    # 갭하락 손절
    "GAP_DOWN_STOP_REQUIRED":      "#f57c00",
    "GAP_DOWN_STOP_DONE":          "#4caf50",
    "GAP_DOWN_STOP_MISSED":        "#c62828",
    "POST_GAPDOWN_AVERAGING_DOWN": "#c62828",
    # 연장 보유
    "EXTENDED_HOLD_ALLOWED":       "#00897b",
    "EXTENDED_HOLD_NOT_ALLOWED":   "#607d8b",
    "UNAUTHORIZED_EXTENDED_HOLD":  "#e53935",
    "EXTENDED_HOLD_PROFIT":        "#4caf50",
    "EXTENDED_HOLD_LOSS":          "#ef5350",
    "EXTENDED_HOLD_REVIEW_NEEDED": "#607d8b",
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


def _generate_html(
    result: dict,
    csv_name: str,
    report_date: str,
    cumulative_entry_pnl: dict | None = None,
    trend_data: list | None = None,
) -> str:
    s      = result["summary"]
    stocks = result["stocks"]
    es     = s.get("entry_stats", {})

    def _pct_color(v) -> str:
        if v is None:
            return ""
        return "color:#4caf50" if v >= 0 else "color:#ef5350"

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

    # ── 진입 방식 요약 카드 ──────────────────────────────────
    cum = cumulative_entry_pnl or {}

    def _ep(et_key):
        v = es.get("entry_pnl", {}).get(et_key, 0)
        color = "#4caf50" if v >= 0 else "#ef5350"
        return f'<span style="color:{color}">{_fmt_krw(v)}</span>' if v else "-"

    def _cum(cum_key):
        v = cum.get(cum_key, 0)
        if not v:
            return "-"
        color = "#4caf50" if v >= 0 else "#ef5350"
        return f'<span style="color:{color};font-size:11px">{_fmt_krw(v)}</span>'

    entry_summary_rows = [
        ("정규장 종가 진입",        es.get("regular_close_n", 0),   es.get("regular_close_rate", 0),   _ep("REGULAR_CLOSE_ENTRY"),  _cum("REGULAR_CLOSE_ENTRY")),
        ("17:50 이후 NXT (조건부)", es.get("conditional_nxt_n", 0), es.get("conditional_nxt_rate", 0), _ep("AFTER_1750_NXT_ENTRY"),  _cum("CONDITIONAL_NXT")),
        ("추격성 NXT 진입",         es.get("nxt_caution_n", 0) + es.get("nxt_chase_n", 0), es.get("nxt_chase_rate", 0), "-", _cum("NXT_CHASE")),
        ("D+1 장초 추격 진입",      es.get("d1_chase_n", 0) + es.get("reverse_n", 0),      es.get("d1_chase_rate", 0),  _ep("D1_CHASE_ENTRY"),  _cum("D1_CHASE")),
        ("확인불가",                es.get("unknown_n", 0),         0,                                 _ep("UNKNOWN"),              _cum("UNKNOWN")),
    ]
    entry_summary_html = "".join(
        f"<tr><td>{_e(label)}</td>"
        f"<td style='text-align:center'>{cnt}건</td>"
        f"<td style='text-align:center;color:#aaa'>{rate:.1f}%</td>"
        f"<td style='text-align:right'>{pnl_html}</td>"
        f"<td style='text-align:right'>{cum_html}</td></tr>"
        for label, cnt, rate, pnl_html, cum_html in entry_summary_rows
    )

    # ── 종목별 카드 ───────────────────────────────────────────
    stock_cards = ""
    for r in stocks:
        tag_html = (
            "".join(_tag_badge(t) for t in r["tags"])
            or '<span style="color:#4caf50">✓ 위반없음</span>'
        )
        wl       = r.get("weight_level", "")
        wl_color = _WEIGHT_COLOR.get(wl, "")
        wl_html  = f' <span style="color:{wl_color};font-weight:700">[{_e(wl)}]</span>' if wl else ""

        is_inter_html = ""
        if r.get("is_inter") is True:
            is_inter_html = ' <span style="color:#29b6f6;font-size:11px">[교집합]</span>'
        elif r.get("is_inter") is False:
            is_inter_html = ' <span style="color:#aaa;font-size:11px">[비교집합]</span>'

        rp_val  = r.get("realized_pct")
        rp_html = f'<span style="{_pct_color(rp_val)}">{_fmt_pct(rp_val)}</span>' if rp_val is not None else "-"

        # 진입 구간 + NXT 평가
        et         = r.get("entry_type", "UNKNOWN")
        et_label   = _ENTRY_TYPE_LABEL.get(et, et)
        pct_val    = r.get("entry_price_vs_signal_pct")
        pct_html   = f'{pct_val:+.2f}%' if pct_val is not None else "-"
        price_src  = r.get("sig_price_source", "")
        price_src_html = f'<span>기준가 출처: <span style="color:#90caf9">{_e(price_src)}</span></span>' if price_src else ""

        nxt_tags = [t for t in r["tags"] if t in (
            "AFTER_1750_NXT_ENTRY", "CONDITIONAL_NXT_ENTRY", "NXT_ENTRY_CAUTION", "NXT_CHASE_ENTRY"
        )]
        nxt_eval_html = ("".join(_tag_badge(t) for t in nxt_tags)) if nxt_tags else ""

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

        rem      = r.get("remaining_qty", 0)
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
  <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:12px;color:#888;margin-bottom:6px">
    <span>진입구간: <span style="color:#ccc">{_e(et_label)}</span></span>
    <span>기준가 대비: <span style="color:#ccc">{_e(pct_html)}</span></span>
    {price_src_html}
    {"<span>17:50 NXT 평가: " + nxt_eval_html + "</span>" if nxt_eval_html else ""}
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

    # ── 교훈 섹션 ────────────────────────────────────────────
    lesson     = s.get("lesson", {})
    worst_tag  = lesson.get("worst_loss_tag")
    mfreq_tag  = lesson.get("most_frequent_tag")
    if worst_tag:
        _lesson_principle = _LESSON_TEXT.get(worst_tag, worst_tag)
        _mfreq_row = (
            f"<div style='font-size:12px;color:#888;margin-bottom:8px'>빈도 최다: "
            f"<b>{_e(mfreq_tag)}</b> ({lesson.get('most_frequent_n', 0)}건)</div>"
            if mfreq_tag and mfreq_tag != worst_tag else ""
        )
        lesson_html = f"""
<div class="card" style="border-left:4px solid #ef5350">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">이번 주 핵심 교훈</div>
  <div style="font-size:13px;color:#bbb;margin-bottom:4px">
    손실의 가장 큰 원인:
    <span style="color:#ef5350;font-weight:700">{_e(worst_tag)}</span>
    <span style="color:#888;font-size:12px">({_fmt_krw(lesson.get('worst_loss_amount', 0))})</span>
  </div>
  {_mfreq_row}
  <div style="font-size:14px;color:#fff;font-weight:600">
    다음 주 집중 원칙: {_e(_lesson_principle)}
  </div>
</div>"""
    else:
        lesson_html = ""

    # ── 항목별 준수율 섹션 ─────────────────────────────────
    ic = s.get("item_compliance", {})

    def _ic_cell(rate, n, denom):
        if denom == 0:
            return "<td colspan='2' style='color:#888;text-align:center;font-size:12px'>대상 없음</td>"
        if rate is None:
            return "<td colspan='2' style='color:#888;text-align:center;font-size:12px'>확인불가</td>"
        color = "#4caf50" if rate >= 80 else ("#fb8c00" if rate >= 60 else "#ef5350")
        return (f"<td style='text-align:center;color:{color};font-weight:600'>{rate:.1f}%</td>"
                f"<td style='text-align:center;color:#aaa;font-size:12px'>{n}/{denom}</td>")

    item_compliance_html = f"""
<div class="card">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">
    항목별 준수율
    <span style="font-size:11px;color:#555;font-weight:400">(개선 추세 확인용)</span>
  </div>
  <table>
    <thead><tr><th>항목</th><th style="text-align:center">준수율</th><th style="text-align:center">건수</th></tr></thead>
    <tbody>
      <tr><td>봇 신호 준수</td>{_ic_cell(ic.get('bot_signal_rate'), ic.get('bot_signal_n', 0), ic.get('bot_signal_denom', 0))}</tr>
      <tr><td>교집합 우선</td>{_ic_cell(ic.get('inter_rate'), ic.get('inter_n', 0), ic.get('inter_denom', 0))}</tr>
      <tr><td>종가 진입</td>{_ic_cell(ic.get('close_entry_rate'), ic.get('close_entry_n', 0), ic.get('close_entry_denom', 0))}</tr>
      <tr><td>D+1 09:30 청산</td>{_ic_cell(ic.get('d1_exit_rate'), ic.get('d1_exit_n', 0), ic.get('d1_exit_denom', 0))}</tr>
      <tr><td>물타기 금지</td>{_ic_cell(ic.get('avg_down_rate'), ic.get('avg_down_n', 0), ic.get('avg_down_denom', 0))}</tr>
      <tr><td>포지션 한도</td>{_ic_cell(ic.get('pos_limit_rate'), ic.get('pos_limit_n', 0), ic.get('pos_limit_denom', 0))}</tr>
    </tbody>
  </table>
  <div style="font-size:11px;color:#555;margin-top:6px">
    엄격 기준: 위반 태그가 하나라도 있으면 미준수 |
    항목별 준수율은 개선 추세 확인용입니다.
  </div>
</div>"""

    # ── 4주 추세 섹션 ────────────────────────────────────────
    _td = trend_data or []
    if len(_td) < 2:
        trend_html = """
<div class="card">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">최근 4주 추세</div>
  <div style="color:#888;font-size:13px">
    4주 데이터 부족 — 매주 리뷰를 추가할수록 자동 누적됩니다.
  </div>
</div>"""
    else:
        _trend_rows = ""
        for td_entry in _td:
            _d  = td_entry.get("date", "")
            _cr = td_entry.get("compliance_rate")
            _rl = td_entry.get("total_realized", 0) or 0
            _tc = td_entry.get("tag_counts") or {}
            _avg_n2 = _tc.get("AVERAGING_DOWN", 0)
            _d1_n2  = _tc.get("D1_CHASE_ENTRY", 0) + _tc.get("REVERSE_AT_EXIT_ZONE", 0)
            _nc_n   = _tc.get("NXT_CHASE_ENTRY", 0)
            _pos_n2 = _tc.get("OVERSIZED_POSITION", 0)
            _cr_col = "#4caf50" if (_cr or 0) >= 80 else ("#fb8c00" if (_cr or 0) >= 60 else "#ef5350")
            _rl_col = "color:#4caf50" if _rl >= 0 else "color:#ef5350"
            _trend_rows += (
                f"<tr><td>{_e(_d)}</td>"
                f"<td style='text-align:center;color:{_cr_col};font-weight:600'>"
                f"{f'{_cr:.1f}%' if _cr is not None else '-'}</td>"
                f"<td style='text-align:right;{_rl_col}'>{_fmt_krw(_rl)}</td>"
                f"<td style='text-align:center'>{_avg_n2}</td>"
                f"<td style='text-align:center'>{_d1_n2}</td>"
                f"<td style='text-align:center'>{_nc_n}</td>"
                f"<td style='text-align:center'>{_pos_n2}</td></tr>"
            )
        trend_html = f"""
<div class="card">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">최근 4주 추세</div>
  <table>
    <thead><tr>
      <th>날짜</th>
      <th style="text-align:center">엄격 준수율</th>
      <th style="text-align:right">실현손익</th>
      <th style="text-align:center">물타기</th>
      <th style="text-align:center">D1추격</th>
      <th style="text-align:center">NXT추격</th>
      <th style="text-align:center">과대포지션</th>
    </tr></thead>
    <tbody>{_trend_rows}</tbody>
  </table>
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
<h1>매매 원칙 분석 리포트</h1>
<p style="color:#888;font-size:13px">기준 파일: {_e(csv_name)} · 생성: {report_date}</p>

{lesson_html}

<div class="card">
  <div style="display:flex;gap:32px;flex-wrap:wrap">
    <div>
      <div style="font-size:12px;color:#888">엄격 준수율</div>
      <div style="font-size:32px;font-weight:700;color:{cr_color}">{f"{cr:.1f}%" if cr is not None else "-"}</div>
      <div style="font-size:12px;color:#aaa">{s['compliant_stocks']}/{s['total_stocks']} 종목 (위반 태그 없음)</div>
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

{item_compliance_html}

{trend_html}

<div class="card">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">위반 태그 집계</div>
  {'<p style="color:#4caf50">위반 없음</p>' if not tag_rows else f'<table><thead><tr><th>태그</th><th>횟수</th><th>설명</th></tr></thead><tbody>{tag_rows}</tbody></table>'}
</div>

<div class="card">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">진입 방식별 손익</div>
  <table>
    <thead><tr>
      <th>진입 유형</th>
      <th style="text-align:center">종목 수</th>
      <th style="text-align:center">비율</th>
      <th style="text-align:right">이번 기간</th>
      <th style="text-align:right;color:#666">전체 누적</th>
    </tr></thead>
    <tbody>{entry_summary_html}</tbody>
  </table>
  <div style="font-size:11px;color:#555;margin-top:8px">
    ※ 조건부 NXT는 준수율 위반으로 집계되나 별도 추적 | 누적 손익은 trade_history.json 전체 기준
  </div>
</div>

<div style="font-size:14px;font-weight:600;margin-bottom:8px">종목별 분석</div>
{stock_cards}
</body>
</html>"""


# ── 누적 이력 ────────────────────────────────────────────────

def _update_history(result: dict, report_date: str) -> None:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    # compliance_history
    hist_path = _HISTORY_DIR / "compliance_history.json"
    hist: list[dict] = []
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:
            hist = []

    s  = result["summary"]
    es = s.get("entry_stats", {})

    # 분석 기간 끝날짜 — 같은 CSV를 다른 날 재분석해도 history 중복 방지
    sig_dates = [st.get("sig_date", "") for st in result.get("stocks", []) if st.get("sig_date")]
    raw_end   = max(sig_dates) if sig_dates else ""
    period_end = (f"{raw_end[:4]}-{raw_end[4:6]}-{raw_end[6:]}"
                  if len(raw_end) == 8 else report_date)

    entry = {
        "date":                  report_date,
        "period_end":            period_end,
        "compliance_rate":       s.get("compliance_rate"),
        "total_realized":        s.get("total_realized"),
        "total_realized_pct":    s.get("total_realized_pct"),
        "total_stocks":          s.get("total_stocks"),
        "tag_counts":            s.get("tag_counts"),
        "regular_close_rate":    es.get("regular_close_rate"),
        "conditional_nxt_rate":  es.get("conditional_nxt_rate"),
        "nxt_chase_rate":        es.get("nxt_chase_rate"),
        "d1_chase_rate":         es.get("d1_chase_rate"),
        "item_compliance":       s.get("item_compliance", {}),
    }
    # period_end 또는 date 중 하나라도 일치하면 제거 (재분석/재실행 모두 대응)
    hist = [h for h in hist
            if h.get("period_end", h.get("date")) != period_end
            and h.get("date") != report_date]
    hist.append(entry)
    hist.sort(key=lambda x: x.get("period_end", x.get("date", "")))
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

    # trade_history
    trade_hist_path = _HISTORY_DIR / "trade_history.json"
    all_trades: list[dict] = []
    if trade_hist_path.exists():
        try:
            all_trades = json.loads(trade_hist_path.read_text(encoding="utf-8"))
        except Exception:
            all_trades = []

    all_trades = [t for t in all_trades if t.get("report_date") != report_date]
    for r in result["stocks"]:
        tags = r.get("tags", [])
        all_trades.append({
            "report_date":               report_date,
            "code":                      r["code"],
            "name":                      r["name"],
            "sig_date":                  r.get("sig_date"),
            "is_inter":                  r.get("is_inter"),
            "realized":                  r.get("realized"),
            "realized_pct":              r.get("realized_pct"),
            "weight_pct":                r.get("weight_pct"),
            "tags":                      tags,
            "entry_type":                r.get("entry_type"),
            "is_regular_close_entry":    r.get("entry_type") == "REGULAR_CLOSE_ENTRY",
            "is_after_1750_nxt_entry":   r.get("entry_type") == "AFTER_1750_NXT_ENTRY",
            "is_conditional_nxt_entry":  "CONDITIONAL_NXT_ENTRY" in tags,
            "is_nxt_chase_entry":        "NXT_CHASE_ENTRY" in tags,
            "is_d1_chase_entry":         r.get("entry_type") in ("D1_CHASE_ENTRY", "REVERSE_AT_EXIT_ZONE"),
            "entry_price_vs_signal_pct": r.get("entry_price_vs_signal_pct"),
            "sig_price_source":          r.get("sig_price_source", ""),
            "entry_type_pnl":            r.get("realized"),
        })
    all_trades.sort(key=lambda x: (x.get("report_date", ""), x.get("code", "")))
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
    parser.add_argument("csv_path", nargs="?",  help="HTS CSV 경로 (생략 또는 --latest 시 자동 선택)")
    parser.add_argument("--latest",    action="store_true", help="data/weekly_trading_review/ 최신 CSV 자동 선택")
    parser.add_argument("--open",      action="store_true", help="결과 HTML 브라우저로 열기")
    parser.add_argument("--overwrite", action="store_true", help="오늘 날짜 기존 결과 덮어쓰기")
    args = parser.parse_args()

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

    s  = result["summary"]
    es = s.get("entry_stats", {})
    print(f"  종목 {s['total_stocks']}개 | 준수율 {s.get('compliance_rate','?')}%"
          f" | 손익 {s['total_realized']:+,.0f}원 ({_fmt_pct(s.get('total_realized_pct'))})")
    print(f"  정규종가 {es.get('regular_close_n',0)}건"
          f" | 조건부NXT {es.get('conditional_nxt_n',0)}건"
          f" | 추격NXT {es.get('nxt_chase_n',0)+es.get('nxt_caution_n',0)}건"
          f" | D1추격 {es.get('d1_chase_n',0)+es.get('reverse_n',0)}건")

    report_date = date.today().strftime("%Y-%m-%d")
    stem        = f"trade_review_{report_date}"
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    default_html = _REPORT_DIR  / f"{stem}.html"
    default_json = _HISTORY_DIR / f"{stem}.json"
    if not args.overwrite and (default_html.exists() or default_json.exists()):
        print(f"  [주의] 오늘({report_date}) 리포트가 이미 존재합니다. 새 버전으로 저장됩니다.")

    html_path = _safe_path(_REPORT_DIR,  stem, ".html", args.overwrite)
    json_path = _safe_path(_HISTORY_DIR, stem, ".json", args.overwrite)

    _update_history(result, report_date)  # trade_history 먼저 저장해야 cumulative 계산 포함됨
    cum_pnl    = _calc_cumulative_entry_pnl()
    trend_data = _load_4week_trend()
    html = _generate_html(result, csv_path.name, report_date, cum_pnl, trend_data)
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"  HTML → {html_path}")
    print(f"  JSON → {json_path}")

    if args.open:
        webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    main()
