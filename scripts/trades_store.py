# scripts/trades_store.py
"""사용자 매매 입력 저장소 — 텔레그램 답장으로 남긴 매매 근거의 파싱·보관·조회.

입력 형식 (첫 줄만 고정, 둘째 줄부터 자유):
    매수 SK하이닉스 1812000 5주
    판 대형주 / 이유 대장·NXT·수급 / 깨는가 1768000 / 비중 900만
첫 단어가 매수·매도·무포가 아니면 메모로 저장한다(버리지 않는다). 단 `종목 가격 [수량]` 꼴은 매수로 적는다(09-21).
    후보 SK하이닉스 삼성전기 두산        ← 15시대에 고른 후보(있을 때만, 사용자가 직접). 사후 판단지 대조용
    판 대형주 / 투탑이 대금 흡수 / 비중 1000   ← 14:30 판 판정, 종목 보기 전(2026-09-30). 일지 "내 사전 판단" 대신
    연습 매수 SK하이닉스 1277000 2주    ← 주문 없는 판단 연습(2026-10-25). practice=True로 저장, 보유 종목에 안 잡힌다
    매수 SK하이닉스 1277000 2주 손절 1240000   ← 주문 규칙 ①(2026-10-27): 주문 전 한 줄에 손절가. "손절"은 "깨는가"와 같은 칸

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
SIDES = ("매수", "매도", "무포", "후보", "판")
_CAND = re.compile(r"^\s*후보\s*[:：]?\s*(?P<names>.*)$")
_CAND_SEP = re.compile(r"[\s,、·/]+")
# "판단 보류" 같은 메모는 안 잡히게 — 판 뒤에 대형주·개별주·무포가 바로 와야 한다
# "판"을 빼고 "개별주 / …"로 시작해도 판 줄로 읽는다(2026-10-30 입력이 메모로 저장됨). "무포"만은 매매 줄과 겹쳐 "판"이 붙을 때만.
_PAN = re.compile(r"^\s*(?:판\s*[:：]?\s*(대형주|개별주|무포)|(대형주|개별주)(?=\s*[/|,:：]|\s*$))")   # "대형주 쏠림이…" 같은 문장은 판이 아니다 — 구분자가 바로 와야
_MUPO_WORD = re.compile(r"(?:^|[\s/|,])무포(?=[\s/|,.]|$)")
_PRIORITY = {"매수": 0, "매도": 0, "무포": 1, "판": 2, "후보": 3}
# 운용 중단 중 주문 없는 판단 연습(2026-10-25) — 실전 기록·보유 종목과 섞이지 않게 앞에 "연습"을 붙인다
_PRACTICE = re.compile(r"^\s*연습\s*[:：]?\s*")

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
_NAME_SIDE = re.compile(r"^\s*(?P<name>[^\s\d/:：]+)\s+(?P<side>매수|매도)\s+(?=[\d,]{4,})")
_NOT_STOCK = ("코스피", "코스닥", "나스닥", "지수", "선물", "환율", "달러", "유가", "비트")   # 시장 메모에 흔한 첫 단어
# "10주씩 두번" · "10주씩 2회" → 20주 (09-22 분할 매도 입력 습관). 한글 횟수는 열 번까지.
_SPLIT = re.compile(r"([\d,]+)\s*주\s*씩\s*(?:(\d+)|([한두세네다섯여섯일곱여덟아홉열]+))\s*(?:번|회|차례)")
# "148만원"·"148.3만" → 1480000·1483000 (2026-10-28 입력 "하이닉스 185만원 2주"가 가격 185원으로 저장됨). "비중 300만"은 그대로 둔다.
_MANWON = re.compile(r"(?<![\d.])(?<!비중)(?<!비중 )(\d+(?:\.\d+)?)\s*만\s*원?")
# "손절 -2%"·"손절가 2%" — 가격 대비 %로 적으면 손절가를 계산해 넣는다(같은 날 입력 "손절가 -2%"가 빠졌다)
_STOP_PCT = re.compile(r"(?:손절가?|깨는가)\s*[:：]?\s*-?\s*(\d+(?:\.\d+)?)\s*%")
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
    # 손절가는 가격만 — "손절 2번 당함" 같은 문장 속 횟수가 잡히지 않게 4자 이상 + 뒤에 번·회·%·일이 오지 않을 때(GPT 검토 10-27)
    "깨는가": re.compile(r"(?:깨는가|손절가?)(?:\s*가격)?\s*[:：]?\s*(\d[\d,]{3,})(?!\s*(?:번|회|%|일))"),
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
    """텍스트 → 필드. side가 None이면 메모.

    첫 줄이 형식에 안 맞아도 **문맥으로** 읽는다(2026-11-02 사용자 "별도 형식이 아니더라도 문맥상 알맞은걸로 저장"):
    첫 줄이 질문·메모여도 다른 줄에 `매수 …`·`무포 …`·`판 …`이 있으면 그 줄로 분류한다(매수·매도 > 무포 > 판 > 후보).
    단 첫 단어 없는 `종목 가격` 꼴은 첫 줄에서만 매수로 본다 — 메모 속 숫자를 매수로 잘못 적지 않게.
    판 줄 안에 "무포"가 있으면 결정=무포로 남긴다. 문맥으로 읽은 것은 from_context=True, 회신에 그렇게 밝힌다."""
    out = _parse_one(text)
    best = None
    for ln in text.strip().splitlines()[1:]:
        r = _parse_one(ln)
        if r.get("side") in ("매수", "매도") and not (r.get("name") and r.get("price")):
            continue   # 아래 줄의 매수·매도는 종목과 가격이 있을 때만 — "매수는 내일 검토" 같은 문장 제외(GPT 검토 11-02)
        if r.get("side") and not r.get("side_inferred") and (best is None or _PRIORITY[r["side"]] < _PRIORITY[best["side"]]):
            best = r
    cur = _PRIORITY.get(out.get("side"), 9)
    if best and _PRIORITY[best["side"]] < cur:
        # 첫 줄이 판·메모여도 아래 줄에 매수·매도가 있으면 그 줄이 기록의 본체 — 첫 줄의 판 값은 남긴다
        keep = {k: out[k] for k in ("판", "이유", "비중", "names", "깨는가") if out.get(k) and not best.get(k)}
        out = {**best, **keep, "raw": text.strip(), "from_context": True, **({"practice": True} if out.get("practice") else {})}
    first = (text.strip().splitlines() or [""])[0]
    if out.get("side") == "판" and out.get("판") != "무포" and _MUPO_WORD.search(first):
        out["결정"] = "무포"   # 판 줄 자체에 무포가 있을 때만 — 아래 줄의 "어제는 무포" 같은 설명은 결정이 아니다
    if re.search(r"^\s*연습", text, re.M):
        out["practice"] = True   # 어느 줄이든 "연습"으로 시작하면 메시지 전체가 연습 기록
    return out


def _parse_one(text: str) -> dict:
    mp = _PRACTICE.match(text)
    if mp:
        out = parse(text[mp.end():])
        out["practice"], out["raw"] = True, text.strip()
        return out
    raw_text = text
    text = _MANWON.sub(lambda m: str(round(float(m.group(1)) * 10000)), text)
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    if re.match(r"^\s*(매수|매도)\s*/", first):
        first = first.replace("/", " ")   # "매수/종목/가격/수량" 입력 습관(2026-10-25)
    ms = _NAME_SIDE.match(first)
    if ms and not ms.group("name").startswith(_NOT_STOCK):
        first = f"{ms.group('side')} {ms.group('name')} {first[ms.end():]}"   # "하이닉스 매도 1423000 2주" 어순(2026-11-03 입력이 메모로 저장됨)
    rest = "\n".join(lines[1:])
    out: dict = {"side": None, "name": None, "price": None, "qty": None, "raw": raw_text.strip()}
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
    if _PAN.match(first):
        # 판 줄 — 14:30 판단 기록. 종목명이 없으니 보유 종목 계산엔 안 들어간다. 첫 필드 뒤 자유 문장은 이유로.
        out["side"] = "판"
        for key, rx in _FIELDS.items():
            mm = rx.search(text)
            if mm:
                val = mm.group(1).strip()
                out[key] = _num(val) if key in ("깨는가", "비중") else val
        mpan = _PAN.match(first)
        out.setdefault("판", mpan.group(1) or mpan.group(2))
        if "이유" not in out:
            parts = [x.strip() for x in first[_PAN.match(first).end():].split("/")]
            free = [x for x in parts if x and not re.match(r"(비중|깨는가)\s", x)]
            if free:
                out["이유"] = " / ".join(free)
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
    mp = _STOP_PCT.search(body)
    if mp and out.get("price") and not out.get("깨는가"):
        out["손절_pct"] = float(mp.group(1))
        out["깨는가"] = round(out["price"] * (1 - out["손절_pct"] / 100))
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
    return _positions(load_recent(days, store))


def _same_name(a: str, b: str) -> bool:
    """"하이닉스"와 "SK하이닉스"는 같은 종목. 끝이 같을 때만 — 삼성전자우는 삼성전자가 아니고, 오타는 합치지 않는다(삼성전기·삼성전자)."""
    return a.endswith(b) or b.endswith(a)


def _positions(rows: list[dict]) -> list[dict]:
    pos: dict[str, dict] = {}
    for r in rows:
        name = r.get("name")
        if not name or r.get("practice"):
            continue
        if r.get("side") == "매수":
            pos[name] = r
        elif r.get("side") == "매도":
            for k in [k for k in pos if _same_name(k, name)]:
                del pos[k]
    return list(pos.values())


def sell_warning(row: dict, store: Path | None = None) -> str:
    """매도 입력이 보유 종목과 안 맞으면 회신에 붙일 경고. 2026-11-03 "히이닉스 매도 142300원"이 보유 종목에 남아 있던 일."""
    t = row.get("time", "")
    pos = _positions([r for r in load_recent(15, store) if r.get("time", "") <= t and r.get("update_id") != row.get("update_id")])
    hit = next((p for p in pos if _same_name(p["name"], row.get("name") or "")), None)
    if not hit:
        held = " · ".join(p["name"] for p in pos) or "없음"
        return f"⚠ 보유 목록에 없는 종목 — 입력된 보유: {held}. 종목명 확인"
    entry, px = hit.get("price"), row.get("price")
    if entry and px and not 0.5 <= px / entry <= 1.5:
        return f"⚠ 매수가 {entry:,.0f}와 {(px / entry - 1) * 100:+.0f}% 차이 — 가격 확인"
    return ""


def format_ack(row: dict) -> str:
    """수거 확인 문구 — 사용자가 봇 채팅에서 바로 볼 한 줄."""
    t = row.get("time", "")[11:16] + (" 연습" if row.get("practice") else "")
    if row.get("side") in ("매수", "매도"):
        px = f" {row['price']:,.0f}" if row.get("price") else ""
        q = f" {row['qty']:,.0f}주" if row.get("qty") else ""
        extra = []
        if row.get("깨는가"):
            extra.append(f"손절 {row['깨는가']:,.0f}")
        if row.get("비중"):
            extra.append(f"비중 {row['비중']:,.0f}만")
        if row.get("side") == "매수" and not row.get("깨는가"):
            extra.append("⚠ 손절가 없음 — 규칙 ① 예: 손절 1240000")
        if row.get("warn"):
            extra.append(row["warn"])
        tail = f" · {' · '.join(extra)}" if extra else ""
        side = "매수(첫 단어 없어 매수로 기록 — 매도면 '매도'를 앞에)" if row.get("side_inferred") else row["side"]
        return f"{t} {side} {row.get('name') or '?'}{px}{q}{tail}" + (" (문맥으로 읽음)" if row.get("from_context") else "")
    if row.get("side") == "무포":
        return f"{t} 무포 기록" + (" (문맥으로 읽음)" if row.get("from_context") else "")
    if row.get("side") == "판":
        w = f" · 비중 {row['비중']:,.0f}만" if row.get("비중") else ""
        d = " → 무포" if row.get("결정") == "무포" else ""
        return f"{t} 판 기록: {row.get('판')}{d}{w}" + (" (문맥으로 읽음)" if row.get("from_context") else "")
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
    if (m and m.group("side")) or _PAN.match(first):
        return False
    return bool(_PRICE_LIKE.search(first))
