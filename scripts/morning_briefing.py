# scripts/morning_briefing.py
"""아침 브리핑 — 07:40 KST 발송.

2026-09-11부터 **봇 후보가 아니라 사용자가 텔레그램에 남긴 보유 종목**을 다룬다.
  1. 미국장 지수 + 반도체(SOX·마이크론·샌디스크·SK하이닉스 ADR(SKHY)) — 투톱 갭의 선행 지표
  2. 보유 종목(매수 입력 중 매도 입력이 없는 것): 매수가 · 사용자가 적은 깨는가 · 비중
  3. 08:00 이후 실행이면 NXT 프리장 가격과 매수가 대비 %
  4. 코스피200 야간선물(KIS) + 굵직한 이벤트 — 밤사이 빅테크·반도체 실적과 다음 브리핑 전까지의 실적·지표 일정(2026-11-03)
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
_SEMI = {"마이크론": "MU", "샌디스크": "SNDK", "SKHY(ADR)": "SKHY"}


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
            parts.append("반도체(미국 전일 등락) " + " · ".join(f"{k} {_sign(v)}" for k, v in semi.items()))
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"미국장 블록 생성 실패 (무시): {e}")
        return ""


def _semi_moves() -> dict[str, float]:
    """마이크론·샌디스크·SK하이닉스 미국 ADR(SKHY) 전일 등락. 2026-10-13 프랑크푸르트(HY9H.F)에서 교체 — 고수들이 보는 지표가 SKHY. 룰북 역설계: 투톱 갭과 순위상관 +0.55에서 0.61."""
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


def _night_section() -> str:
    """코스피200 야간선물. 실패도 한 줄로 알린다(빈칸이면 하락인지 조회 실패인지 구분이 안 된다)."""
    from scripts.fetch_events import night_futures
    nf = night_futures()
    if not nf:
        return "<b>🌙 코스피200 야간선물</b> 조회 실패"
    return (f"<b>🌙 코스피200 야간선물</b> {nf['last']:,.2f} (<b>{_sign(nf['pct'])}</b>)\n"
            f"정규장 종가 {nf['day_close']:,.2f} 대비 · 거래량 {nf['vol']:,}계약")


def _kst_txt(t) -> str:
    return f"{t.month:02d}/{t.day:02d}({_DAY_KO[t.weekday()]}) {t:%H:%M}"


def _events_section() -> str:
    """밤사이 실적 결과 + 다음 브리핑(다음 거래일 07:40) 전까지 일정. 없으면 빈 문자열."""
    try:
        from datetime import timedelta
        from scripts.fetch_events import KST, earnings, macro_events
        from scripts.market_calendar import get_next_trading_day, get_prev_trading_day
        now = datetime.now(KST)
        def at0740(d8):
            return datetime.strptime(d8, "%Y%m%d").replace(hour=7, minute=40, tzinfo=KST)
        today8 = now.strftime("%Y%m%d")
        since = at0740(get_prev_trading_day(today8) or (now - timedelta(days=1)).strftime("%Y%m%d"))
        until = at0740(get_next_trading_day(today8) or (now + timedelta(days=1)).strftime("%Y%m%d"))
        done, todo = earnings(since, until)
        macro = macro_events(until)
    except Exception as e:
        logger.warning(f"이벤트 블록 생성 실패 (무시): {e}")
        return ""
    if not (done or todo or macro):
        return "" if macro is not None else "<b>📌 굵직한 이벤트</b> 지표 일정 조회 실패"
    lines = ["<b>📌 굵직한 이벤트</b>"]
    if done:
        lines.append("실적 발표(지난 브리핑 이후)")
        for e in done:
            est = f"예상 {e['est']:.2f}" if e["est"] is not None else "예상 없음"
            eps = f"EPS {e['rep']:.2f} / {est}" if e["rep"] is not None else f"EPS 미집계({est})"
            mv = f" · {e['move_label']} <b>{_sign(e['move'])}</b>" if e.get("move") is not None else ""
            lines.append(f"• {e['name']} {eps}{mv}")
    sched = [(e["when"], f"{e['name']} 실적({'장 전' if e['bmo'] else '장 마감 후'}"
                         + (f", 예상 EPS {e['est']:.2f})" if e["est"] is not None else ")")) for e in todo]
    sched += [(m["when"], f"[{m['country']}] {m['title']}"
                          + (f" 예상 {m['forecast']} · 이전 {m['previous']}" if m["forecast"] else "")) for m in macro or []]
    if sched:
        lines.append("예정(다음 브리핑 전까지, 한국 시각)")
        lines += [f"• {_kst_txt(t)} {txt}" for t, txt in sorted(sched, key=lambda x: x[0])]
    if macro is None:
        lines.append("※ 지표 일정 조회 실패 — 실적 일정만 표시")
    return "\n".join(lines)


def _code_by_name(names: list[str]) -> dict[str, str]:
    """종목명 → 코드 (KRX 전 종목 1회 조회). 실패하면 빈 dict."""
    try:
        from scripts.naver_api import fetch_stock_default
        rows = fetch_stock_default("KRX", "ALL", "marketSum")
        m = {str(r.get("itemname")): str(r.get("itemcode")) for r in rows}
        out = {}
        for n in names:
            if n in m:
                out[n] = m[n]
            else:   # "하이닉스"처럼 줄여 쓴 이름 — 시총 순으로 처음 걸리는 종목(SK하이닉스)
                hit = next((c for full, c in m.items() if n in full), None)
                if hit:
                    out[n] = hit
        return out
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
            lines.append(f"  깨는가 {k:,.0f} (매수가 대비 {(k / entry - 1) * 100:+.1f}%)")
        if p.get("이유"):
            lines.append(f"  이유 {p['이유']}")
        if not pre_open and entry and codes.get(name):
            px, sess = _nxt_now(codes[name])
            if px:
                lines.append(f"  NXT {sess} 현재가 {px:,.0f} (매수가 대비 {(px / entry - 1) * 100:+.2f}%)")
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
    parts.append(_night_section())
    ev = _events_section()
    if ev:
        parts.append(ev)
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
