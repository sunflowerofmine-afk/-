# scripts/morning_briefing.py
"""아침 브리핑 — 07:40 KST 발송.

2026-09-11부터 **봇 후보가 아니라 사용자가 텔레그램에 남긴 보유 종목**을 다룬다.
  1. 미국장 지수 + 반도체(SOX·마이크론·샌디스크·하이닉스 프랑크푸르트) — 투톱 갭의 선행 지표
  2. 보유 종목(매수 입력 중 매도 입력이 없는 것): 매수가 · 사용자가 적은 깨는가 · 비중
  3. 08:00 이후 실행이면 NXT 프리장 가격과 매수가 대비 %
"어제 복기하기" 구글폼 링크는 삭제했다 — 복기는 매매 입력 + 판단지로 한다.

돌팬티 3강 00:09:28 "프리장에서 8시에서 8시 50분까지 종가베팅 대응 필수". 07:40인 이유는
그 창이 열리기 전에 청산 계획을 세워 두기 위해서다(00:08:39 "예상 시초가를 전혀 알 수가 없다").
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts import trades_store as ts
from scripts.notifier import send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
_SEMI = {"마이크론": "MU", "샌디스크": "SNDK", "하이닉스(FRA)": "HY9H.F"}


def _sign(v: float) -> str:
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def _is_pre_open() -> bool:
    """NXT 프리마켓(08:00) 개장 전인가."""
    return datetime.now().hour < 8


def _us_section() -> str:
    """미국장 지수 표 + 반도체 개별 3종. 실패하면 빈 문자열."""
    try:
        from scripts.us_briefing import _index_table
        from scripts.fetch_us_market import fetch_indices
        indices = fetch_indices()
        if not indices:
            return ""
        parts = [f"<b>🌏 미국장</b>\n<pre>{_index_table(indices)}</pre>"]
        semi = _semi_moves()
        if semi:
            parts.append("반도체 " + " · ".join(f"{k} {_sign(v)}" for k, v in semi.items()))
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"미국장 블록 생성 실패 (무시): {e}")
        return ""


def _semi_moves() -> dict[str, float]:
    """마이크론·샌디스크·하이닉스 프랑크푸르트 전일 등락. 룰북 역설계: 투톱 갭과 순위상관 +0.55에서 0.61."""
    out: dict[str, float] = {}
    try:
        import yfinance as yf
        for label, sym in _SEMI.items():
            h = yf.Ticker(sym).history(period="7d").dropna(subset=["Close"])
            if len(h) >= 2:
                out[label] = round((float(h["Close"].iloc[-1]) / float(h["Close"].iloc[-2]) - 1) * 100, 2)
    except Exception as e:
        logger.debug(f"반도체 개별 조회 실패: {e}")
    return out


def _code_by_name(names: list[str]) -> dict[str, str]:
    """종목명 → 코드 (KRX 전 종목 1회 조회). 실패하면 빈 dict."""
    try:
        from scripts.naver_api import fetch_stock_default
        rows = fetch_stock_default("KRX", "ALL", "marketSum")
        m = {str(r.get("itemname")): str(r.get("itemcode")) for r in rows}
        return {n: m[n] for n in names if n in m}
    except Exception as e:
        logger.debug(f"종목코드 조회 실패: {e}")
        return {}


def _nxt_now(code: str) -> tuple[float | None, str]:
    """08:00 이후 NXT 현재가와 세션 이름. 없으면 (None, '')."""
    try:
        from scripts.fetch_morning_nxt import _fetch_quote
        q = _fetch_quote(code)
        sess = str(q.get("session") or "")
        label = "프리장" if "PRE" in sess else ("저녁장" if "AFTER" in sess else "메인")
        return q.get("nxt_price"), label
    except Exception as e:
        logger.debug(f"[{code}] NXT 가격 조회 실패: {e}")
        return None, ""


def _positions_section(pre_open: bool) -> str:
    pos = ts.open_positions(days=15)
    if not pos:
        return ("<b>보유 종목</b> 입력 없음\n"
                "매수했으면 이 채팅에 <code>매수 종목 가격 수량</code> 한 줄을 남겨 두면 여기 뜹니다")
    codes = {} if pre_open else _code_by_name([p["name"] for p in pos])
    lines = [f"<b>보유 종목</b> {len(pos)}건 (입력 기준)"]
    for p in pos:
        name = p["name"]
        entry = p.get("price")
        head = f"• <b>{name}</b>"
        if entry:
            head += f" 매수가 {entry:,.0f}"
        if p.get("qty"):
            head += f" · {p['qty']:,.0f}주"
        if p.get("비중"):
            head += f" · {p['비중']:,.0f}만"
        lines.append(head)
        if p.get("깨는가") and entry:
            k = p["깨는가"]
            lines.append(f"  깨는가 {k:,.0f} ({(k / entry - 1) * 100:+.1f}%)")
        if p.get("이유"):
            lines.append(f"  이유 {p['이유']}")
        if not pre_open and entry and codes.get(name):
            px, sess = _nxt_now(codes[name])
            if px:
                lines.append(f"  NXT {sess} {px:,.0f} ({(px / entry - 1) * 100:+.2f}%)")
            else:
                lines.append("  NXT 가격 없음(미대상 또는 미체결)")
        lines.append(f"  입력 {p.get('time', '')[5:16]}")
    if pre_open:
        lines.append("\n⏱ <b>08:00부터 08:50이 대응 창</b> — 개장가를 보고 청산 시각·손절가를 정할 것")
    return "\n".join(lines)


def build_message() -> str:
    now = datetime.now()
    date_str = f"{now.month:02d}/{now.day:02d} ({_DAY_KO[now.weekday()]})"
    pre_open = _is_pre_open()
    parts = [f"<b>🌅 아침 브리핑 | {date_str}</b>"]
    us = _us_section()
    if us:
        parts.append(us)
    parts.append(_positions_section(pre_open))
    return "\n\n".join(parts)


def main():
    logger.info("아침 브리핑 시작")
    from scripts.market_calendar import is_trading_day
    if not is_trading_day(datetime.now().date()):
        logger.info("비거래일 — 브리핑 생략 (입력 수거는 워크플로가 따로 한다)")
        return
    msg = build_message()
    logger.info(f"메시지 미리보기:\n{msg}")
    ok = send_message(msg)
    logger.info(f"발송 {'성공' if ok else '실패'}")


if __name__ == "__main__":
    main()
