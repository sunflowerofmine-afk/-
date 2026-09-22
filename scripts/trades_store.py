# scripts/trades_store.py
"""사용자 매매 입력 저장소 — 텔레그램 답장으로 남긴 매매 근거의 파싱·보관·조회.

입력 형식 (첫 줄만 고정, 둘째 줄부터 자유):
    매수 SK하이닉스 1812000 5주
    판 대형주 / 이유 대장·NXT·수급 / 깨는가 1768000 / 비중 900만
첫 단어가 매수·매도·무포가 아니면 메모로 저장한다(버리지 않는다). 단 `종목 가격 [수량]` 꼴은 매수로 적는다(09-21).
    후보 SK하이닉스 삼성전기 두산        ← 15시대에 고른 후보(있을 때만, 사용자가 직접). 사후 판단지 대조용

저장: `{TRADES_DIR}/YYYY-MM-DD.jsonl` — 메시지 시각(KST) 기준 날짜. **비공개 백업 레포에만** 둔다.
공개 봇 레포에는 사용자 매매 기록을 두지 않는다. 워크플로가 백업 레포를 clone한 뒤
TRADES_DIR=/tmp/backup/data/trades 로 넘긴다.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
SIDES = ("매수", "매도", "무포", "후보")
_CAND = re.compile(r"^\s*후보\s*[:：]?\s*(?P<names>.*)$")
_CAND_SEP = re.compile(r"[\s,、·/]+")

# 수량은 "6주" · "수량 6" · 가격 뒤 맨 숫자 "6" 전부 받는다 (09-17 첫 입력 "하이닉스 1758000 수량 6"이 안 잡혔던 형태).
_FIRST = re.compile(
    r"^\s*(?P<side>매수|매도|무포)\s*(?P<name>[^\s\d/:：][^\s/]*)?\s*(?P<price>[\d,]+(?:\.\d+)?)?\s*(?:원)?"
    r"\s*(?:(?:수량\s*[:：]?\s*)?(?P<qty>[\d,]+)\s*주?)?"
)
# 매수·매도 없이 가격처럼 보이는 4자리 이상 숫자가 있으면 "매매를 적으려다 형식이 빗나간 것"으로 보고 경고한다.
_PRICE_LIKE = re.compile(r"\d[\d,]{3,}")
# 첫 단어 없이 `종목 가격 [수량]`이면 매수로 적는다 — 사용자의 실제 입력 습관(09-17 "하이닉스 1758000 수량 6",
# 09-21 "매수한 건데 적용이 안 됐다"). 매도·무포는 반드시 명시. 회신에 "매수로 기록"이라고 붙여 바로 고칠 수 있게 한다.
_BARE = re.compile(
    r"^\s*(?P<name>[^\s\d/:：][^\s/]*)\s+(?P<price>[\d,]{4,}(?:\.\d+)?)\s*(?:원)?"
    r"\s*(?:(?:수량\s*[:：]?\s*)?(?P<qty>[\d,]+)\s*주?)?"
)
_NOT_STOCK = ("코스피", "코스닥", "나스닥", "지수", "선물", "환율", "달러", "유가", "비트")   # 시장 메모에 흔한 첫 단어
# "10주씩 두번" · "10주씩 2회" → 20주 (09-22 분할 매도 입력 습관). 한글 횟수는 열 번까지.
_SPLIT = re.compile(r"([\d,]+)\s*주\s*씩\s*(?:(\d+)|([한두세네다섯여섯일곱여덟아홉열]+))\s*(?:번|회|차례)")
_KO_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}


def _split_qty(first: str) -> float | None:
    m = _SPLIT.search(first)
    if not m:
        return None
    per = _num(m.group(1))
    times = int(m.group(2)) if m.group(2) else _KO_NUM.get(m.group(3))
    return per * times if (per and times) else None
_FIELDS = {
    "판":    re.compile(r"판\s*[:：]?\s*(대형주|개별주|무포)"),
    "깨는가": re.compile(r"깨는가\s*[:：]?\s*([\d,]+)"),
    "비중":  re.compile(r"비중\s*[:：]?\s*([\d,]+)\s*만?"),
    "이유":  re.compile(r"이유\s*[:：]?\s*([^/\n]+)"),
}


def trades_dir() -> Path:
    return Path(os.getenv("TRADES_DIR", "data/trades"))


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse(text: str) -> dict:
    """텍스트 → 필드. side가 None이면 메모."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    rest = "\n".join(lines[1:])
    out: dict = {"side": None, "name": None, "price": None, "qty": None, "raw": text.strip()}
    mc = _CAND.match(first)
    if mc:
        # 후보 줄 — 종목명만 나열. 보유 종목 계산(open_positions)에는 안 들어간다(name이 없으므로).
        out["side"] = "후보"
        tail = mc.group("names")
        cut = re.search(r"/|판\s*[:：]?\s*(?:대형주|개별주|무포)|깨는가|비중|이유", tail)   # 종목명은 첫 필드 앞까지
        names_part, fields_part = (tail[:cut.start()], tail[cut.start():]) if cut else (tail, "")
        out["names"] = [x for x in _CAND_SEP.split(names_part.strip()) if x]
        body = fields_part + "\n" + rest
        for key, rx in _FIELDS.items():
            mm = rx.search(body)
            if mm:
                val = mm.group(1).strip()
                out[key] = _num(val) if key in ("깨는가", "비중") else val
        return out
    m = _FIRST.match(first)
    mb = None if (m and m.group("side")) else _BARE.match(first)
    if mb and mb.group("name").startswith(_NOT_STOCK):
        mb = None
    if m and m.group("side"):
        out["side"] = m.group("side")
        out["name"] = m.group("name") if out["side"] != "무포" else None
        out["price"] = _num(m.group("price"))
        out["qty"] = _split_qty(first) or _num(m.group("qty"))
        body = first[m.end():] + "\n" + rest
    elif mb:
        out["side"], out["side_inferred"] = "매수", True
        out["name"], out["price"], out["qty"] = mb.group("name"), _num(mb.group("price")), (_split_qty(first) or _num(mb.group("qty")))
        body = first[mb.end():] + "\n" + rest
    else:
        body = text
    for key, rx in _FIELDS.items():
        mm = rx.search(body)
        if mm:
            val = mm.group(1).strip()
            out[key] = _num(val) if key in ("깨는가", "비중") else val
    return out


def record(update_id: int, message_id: int, ts_utc: int, text: str, edited: bool = False,
           store: Path | None = None) -> dict:
    """한 메시지를 저장. 같은 message_id가 이미 있으면 교체(수정 메시지)."""
    store = store or trades_dir()
    store.mkdir(parents=True, exist_ok=True)
    when = datetime.fromtimestamp(ts_utc, tz=KST)
    row = {"update_id": update_id, "message_id": message_id, "time": when.strftime("%Y-%m-%d %H:%M:%S"),
           "edited": edited, **parse(text)}
    path = store / f"{when.date().isoformat()}.jsonl"
    rows = load_day(path)
    rows = [r for r in rows if r.get("message_id") != message_id]
    rows.append(row)
    rows.sort(key=lambda r: r.get("time", ""))
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return row


def load_day(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return rows


def load_recent(days: int = 15, store: Path | None = None, until: date | None = None) -> list[dict]:
    store = store or trades_dir()
    until = until or datetime.now(KST).date()
    rows: list[dict] = []
    for i in range(days, -1, -1):
        d = until - timedelta(days=i)
        rows.extend(load_day(store / f"{d.isoformat()}.jsonl"))
    rows.sort(key=lambda r: r.get("time", ""))
    return rows


def open_positions(days: int = 15, store: Path | None = None) -> list[dict]:
    """종목별로 마지막 매도 이후의 매수만 남긴다. 반환은 매수 행(가장 최근 것) 목록."""
    pos: dict[str, dict] = {}
    for r in load_recent(days, store):
        name = r.get("name")
        if not name:
            continue
        if r.get("side") == "매수":
            pos[name] = r
        elif r.get("side") == "매도":
            pos.pop(name, None)
    return list(pos.values())


def format_ack(row: dict) -> str:
    """수거 확인 문구 — 사용자가 봇 채팅에서 바로 볼 한 줄."""
    t = row.get("time", "")[11:16]
    if row.get("side") in ("매수", "매도"):
        px = f" {row['price']:,.0f}" if row.get("price") else ""
        q = f" {row['qty']:,.0f}주" if row.get("qty") else ""
        extra = []
        if row.get("깨는가"):
            extra.append(f"깨는가 {row['깨는가']:,.0f}")
        if row.get("비중"):
            extra.append(f"비중 {row['비중']:,.0f}만")
        tail = f" · {' · '.join(extra)}" if extra else ""
        side = "매수(첫 단어 없어 매수로 기록 — 매도면 '매도'를 앞에)" if row.get("side_inferred") else row["side"]
        return f"{t} {side} {row.get('name') or '?'}{px}{q}{tail}"
    if row.get("side") == "무포":
        return f"{t} 무포 기록"
    if row.get("side") == "후보":
        names = row.get("names") or []
        return f"{t} 후보 기록: {' · '.join(names)}" if names else f"{t} 후보 기록 (종목명 없음)"
    if looks_like_trade(row.get("raw") or ""):
        return f"{t} 메모 기록 ⚠ 매수/매도가 없어 보유 종목에 안 잡힘 — 예: 매수 하이닉스 1758000 6주"
    return f"{t} 메모 기록"


def looks_like_trade(text: str) -> bool:
    """첫 줄에 매수·매도·무포는 없는데 가격 같은 숫자가 있으면 True — 형식 안내가 필요한 입력."""
    first = next((ln for ln in text.strip().splitlines() if ln.strip()), "")
    if not first:
        return False
    m = _FIRST.match(first)
    if m and m.group("side"):
        return False
    return bool(_PRICE_LIKE.search(first))
