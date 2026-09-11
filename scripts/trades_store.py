# scripts/trades_store.py
"""사용자 매매 입력 저장소 — 텔레그램 답장으로 남긴 매매 근거의 파싱·보관·조회.

입력 형식 (첫 줄만 고정, 둘째 줄부터 자유):
    매수 SK하이닉스 1812000 5주
    판 대형주 / 이유 대장·NXT·수급 / 깨는가 1768000 / 비중 900만
첫 단어가 매수·매도·무포가 아니면 메모로 저장한다(버리지 않는다).

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
SIDES = ("매수", "매도", "무포")

_FIRST = re.compile(
    r"^\s*(?P<side>매수|매도|무포)\s*(?P<name>[^\s\d/:：][^\s/]*)?\s*(?P<price>[\d,]+(?:\.\d+)?)?\s*(?:원)?\s*(?:(?P<qty>[\d,]+)\s*주)?"
)
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
    m = _FIRST.match(first)
    if m and m.group("side"):
        out["side"] = m.group("side")
        out["name"] = m.group("name") if out["side"] != "무포" else None
        out["price"] = _num(m.group("price"))
        out["qty"] = _num(m.group("qty"))
        body = first[m.end():] + "\n" + rest
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
        return f"{t} {row['side']} {row.get('name') or '?'}{px}{q}{tail}"
    if row.get("side") == "무포":
        return f"{t} 무포 기록"
    return f"{t} 메모 기록"
