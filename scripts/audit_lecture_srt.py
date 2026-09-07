"""강의 자막 감사 — 요약을 쓰기 전에 원문에서 놓치기 쉬운 것을 먼저 뽑는다.

배경: 2026-09-08까지 돌팬티 강의 정리에서 같은 유형의 누락이 12건 나왔다.
전부 "원칙 발화는 남기고 그와 어긋나는 것은 떨어뜨리는" 방향이었다.
사람이 주의해서 막을 수 있는 종류가 아니라, 요약이라는 작업 자체가 만드는
필터라서 요약 전에 기계로 먼저 건져 올린다.

    python -m scripts.audit_lecture_srt "D:/돌팬티강의/1강.srt"
    python -m scripts.audit_lecture_srt "D:/돌팬티강의/1강.srt" --out 감사.md

뽑는 것 4가지:
  ① 부인·한정 발화 — "종가베팅으로 접근한 케이스는 아니고요" 한 문장이 사례
     성격을 뒤집는데 요약에서 가장 잘 사라진다.
  ② 수치 조건 + 앞뒤 문맥 — "최소 800억"이 매수 하한인지 뉴스 체크 범위인지는
     앞 문장을 봐야 갈린다. 숫자만 떼면 수식 범위를 잃는다.
  ③ 규범 발화 ↔ 행동 발화 시간순 대조 — "자제할 필요가 있어요"(01:11)와
     "그래서 좀 사긴 했습니다"(01:22)가 나란히 보여야 한다.
  ④ 표기 흔들림 — 같은 말을 자동자막이 두 가지로 적은 것(기간 수급 / 기관 수급).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# ── 추출 규칙 ────────────────────────────────────────────────

# ① 부인·한정. 사례의 성격을 규정하거나 앞말을 뒤집는 표현.
DENIAL = [
    r"아니고요", r"아닙니다", r"아니었", r"아니라", r"아닌데", r"은 아니",
    r"않습니다", r"않았", r"않아요", r"않는", r"하지는 않", r"지는 못",
    r"안 하", r"안 했", r"안 사", r"안 샀", r"못 했", r"못 샀",
    r"말고", r"대신에", r"그런데", r"하지만", r"다만", r"물론",
    r"경우는 아", r"건 아니",
]

# ③ 규범 발화 — "이렇게 해야 한다" 계열
NORM = [
    r"해야 (?:되|됩|한|합)", r"하셔야", r"하시길", r"추천", r"권장",
    r"중요합니다", r"중요하다", r"필수", r"원칙", r"금지",
    r"마세요", r"마시", r"안 됩니다", r"하면 안", r"자제",
    r"기준은", r"최소", r"이상은", r"이하는",
]

# ③ 행동 발화 — "실제로 이렇게 했다" 계열
ACTION = [
    r"샀|사긴|매수를 (?:했|진행)|들어갔|진입을 했",
    r"팔았|매도를 (?:했|진행)|정리를 했|정리했|익절",
    r"손절(?:을|이)? ?(?:했|진행|나갔|쳤)",
    r"들고 (?:왔|있|갔)|보유(?:를)? ?(?:했|하고)",
    r"비중을 (?:늘|줄|낮|올)",
    r"안 샀|매매를 안 했|무포",
]

NUM = re.compile(r"\d[\d,\.]*\s*(?:억|만원|원|%|퍼센트|조|주|배|시|분|일)")

_HANGUL_TOKEN = re.compile(r"[가-힣]{2,5}")


def _compile(pats: list[str]) -> re.Pattern:
    return re.compile("|".join(pats))


RE_DENIAL, RE_NORM, RE_ACTION = _compile(DENIAL), _compile(NORM), _compile(ACTION)


# ── 파싱 ─────────────────────────────────────────────────────


def parse(path: Path) -> list[tuple[str, str]]:
    """(타임코드, 발화) 목록. .srt와 타임코드가 들어간 .txt 둘 다 받는다."""
    raw = path.read_bytes().decode("utf-8-sig", errors="replace")
    recs: list[tuple[str, str]] = []
    for blk in re.split(r"\r?\n\r?\n", raw.strip()):
        lines = [x.strip() for x in blk.strip().split("\n") if x.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            recs.append((lines[1].split(" --> ")[0][:8], " ".join(lines[2:])))
        elif len(lines) >= 2 and "-->" in lines[0]:
            recs.append((lines[0].split(" --> ")[0][:8], " ".join(lines[1:])))
    return recs


def find_repeats(recs: list[tuple[str, str]], min_n: int = 3) -> list[str]:
    """환각 반복 구간. 반복 횟수를 중요도 근거로 쓰지 않도록 먼저 밝힌다."""
    c = Counter(tx for _, tx in recs)
    out = []
    for tx, n in c.most_common():
        if n < min_n or len(tx) < 4:
            continue
        stamps = [ts for ts, t in recs if t == tx]
        out.append(f"{n}회  {stamps[0]}~{stamps[-1]}  {tx[:70]}")
    return out[:10]


# 지시어·감탄사로 시작하는 쌍은 표기 흔들림이 아니라 말버릇이다(그런/이런, 그거/이거).
_DEIXIS = set("그이저요얘걔뭐")

# 뒷말을 셀 때 조사를 떼지 않으면 '수급이·수급을·수급의'가 서로 다른 말이 되어
# 같은 슬롯이 쪼개진다. 실제로 이 때문에 기간/기관 쌍의 집중도가 79%에서 44%로
# 희석돼 상위 15위 밖으로 밀렸다.
_PARTICLE = "이가을를은는의도에로와과만요서"


def _slot(tok: str) -> str:
    """뒷말 비교용 정규화 — 3글자 이상이면 끝의 조사 한 글자를 뗀다."""
    return tok[:-1] if len(tok) >= 3 and tok[-1] in _PARTICLE else tok


def find_variants(all_recs: list[list[tuple[str, str]]]) -> list[str]:
    """한 글자만 다른 채 둘 다 나오는 토큰쌍 — 자동자막 표기 흔들림 후보.

    두 가지를 걸러야 쓸 만해진다.
    1. **뒤따르는 단어가 겹칠 때만** 남긴다. 같은 자리에 들어가는 말이어야
       같은 말의 두 표기일 수 있다(기간 수급 / 기관 수급).
    2. 지시어로 시작하는 쌍은 뺀다(그런/이런은 말버릇이지 오인식이 아니다).

    ★ **여러 파일을 합쳐서 센다.** 한 파일만 보면 못 잡는다 — 9강에는 "기관 수급"이
    한 번도 안 나와서 쌍이 성립하지 않는다. 실제로 이 오인식은 강의 7개를 합쳐
    세고서야 드러났다(기간 44 / 기관 11, 같은 파일 안에 공존).
    """
    freq: Counter[str] = Counter()
    nxt: dict[str, Counter[str]] = {}
    for recs in all_recs:
        for _, tx in recs:
            toks = _HANGUL_TOKEN.findall(tx)
            freq.update(toks)
            for a, b in zip(toks, toks[1:]):
                nxt.setdefault(a, Counter())[_slot(b)] += 1

    common = sorted(w for w, n in freq.items() if n >= 2)
    scored: list[tuple[float, str]] = []
    for i, a in enumerate(common):
        for b in common[i + 1:]:
            if len(a) != len(b) or a[0] in _DEIXIS and b[0] in _DEIXIS:
                continue
            if sum(x != y for x, y in zip(a, b)) != 1:
                continue
            shared = set(nxt.get(a, ())) & set(nxt.get(b, ()))
            if not shared:
                continue
            # 빈도가 아니라 **한 뒷말에 얼마나 몰려 있는가**가 신호다.
            # '있고/하고'는 뒷말이 수백 개로 흩어져 점수가 0에 수렴하고,
            # '기간/기관'은 둘 다 '수급'에 몰려 있어 살아남는다.
            best, bp = None, 0.0
            for w in shared:
                pa = nxt[a][w] / freq[a]
                pb = nxt[b][w] / freq[b]
                if min(pa, pb) > bp:
                    best, bp = w, min(pa, pb)
            if best is None or bp < 0.15:
                continue
            scored.append((
                bp * min(freq[a], freq[b]),
                f"{a}({freq[a]}회) ↔ {b}({freq[b]}회)"
                f"   공통 뒷말 '{best}'에 {bp * 100:.0f}% 이상 집중",
            ))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:15]]


def _ctx(recs, i, before=1, after=1) -> list[str]:
    lo, hi = max(0, i - before), min(len(recs), i + after + 1)
    return [f"{'>' if j == i else ' '} {recs[j][0]} {recs[j][1]}" for j in range(lo, hi)]


# ── 리포트 ───────────────────────────────────────────────────


def build(path: Path, pool: list[list[tuple[str, str]]] | None = None) -> str:
    recs = parse(path)
    if not recs:
        return f"# {path.name}\n\n자막 블록을 찾지 못했습니다.\n"

    L: list[str] = [
        f"# 자막 감사 — {path.name}",
        "",
        f"블록 {len(recs)} · 마지막 타임코드 {recs[-1][0]}",
        "",
        "> 이 파일은 **요약을 쓰기 전에** 읽는다. 여기 걸린 것을 요약에서 뺐다면",
        "> 왜 뺐는지 근거가 있어야 한다. 아무 이유 없이 빠졌다면 그게 편향이다.",
        "",
    ]

    rep = find_repeats(recs)
    if rep:
        L += ["## 0. 반복 구간 (환각 의심 — 중요도 근거로 쓰지 말 것)", ""]
        L += [f"- {r}" for r in rep] + [""]

    L += [
        "## 1. 부인·한정 발화",
        "",
        "사례의 성격을 규정하거나 앞말을 뒤집는 문장. **요약에서 가장 잘 사라진다.**",
        "",
    ]
    hits = [(ts, tx) for ts, tx in recs if RE_DENIAL.search(tx)]
    L += [f"- `{ts}` {tx}" for ts, tx in hits[:120]]
    if len(hits) > 120:
        L.append(f"- … 외 {len(hits) - 120}건")
    L.append("")

    L += [
        "## 2. 수치 조건 (앞뒤 1블록 포함)",
        "",
        "숫자만 떼어내면 수식 범위를 잃는다. **무엇에 걸리는 조건인지** 앞뒤로 확인할 것.",
        "",
    ]
    for i, (ts, tx) in enumerate(recs):
        if NUM.search(tx):
            L.append("```")
            L += _ctx(recs, i)
            L.append("```")
    L.append("")

    L += [
        "## 3. 규범 발화 ↔ 행동 발화 (시간순)",
        "",
        "`[원칙]`을 말한 뒤 `[행동]`이 그것과 어긋나는지 본다.",
        "**둘 다 기록해야 한다. 원칙만 남기면 요약이 실제보다 일관돼 보인다.**",
        "",
    ]
    for ts, tx in recs:
        n, a = bool(RE_NORM.search(tx)), bool(RE_ACTION.search(tx))
        if n or a:
            tag = "[원칙+행동]" if (n and a) else ("[원칙]" if n else "[행동]")
            L.append(f"- `{ts}` {tag} {tx}")
    L.append("")

    scope = pool if pool else [recs]
    var = find_variants(scope)
    if var:
        L += [
            "## 4. 표기 흔들림 후보",
            "",
            f"집계 범위: 자막 파일 {len(scope)}개.",
            "한 글자만 다른데 같은 자리에 들어가는 쌍. 자동자막이 같은 말을 두 가지로",
            "적었을 수 있다(실제 사례: 기간 수급 / 기관 수급). **문맥으로 판정할 것.**",
            "",
        ]
        if len(scope) == 1:
            L += [
                "> ⚠ **한 파일만으로는 못 잡는 경우가 있다.** 한쪽 표기가 이 파일에",
                "> 아예 없으면 쌍이 성립하지 않는다. 같은 강사의 다른 회차를 함께 넘길 것.",
                "",
            ]
        L += [f"- {v}" for v in var] + [""]

    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="강의 자막 감사 리포트 생성")
    ap.add_argument("srt", help="감사할 자막 파일 (.srt 또는 타임코드 포함 .txt)")
    ap.add_argument("--out", help="출력 마크다운 경로 (없으면 표준출력)")
    ap.add_argument(
        "--pool-dir",
        help="표기 흔들림을 함께 집계할 자막들이 있는 폴더. 한 파일만으로는 "
             "한쪽 표기가 없어 쌍이 안 잡힌다. 폴더 안의 .srt와 .txt를 모두 읽는다.",
    )
    args = ap.parse_args()

    src = Path(args.srt)
    if not src.exists():
        print(f"파일이 없습니다: {src}", file=sys.stderr)
        return 1

    pool = [parse(src)]
    if args.pool_dir:
        d = Path(args.pool_dir)
        others = [p for ext in ("*.srt", "*.txt") for p in sorted(d.glob(ext))]
        for q in others:
            if q.resolve() != src.resolve():
                pool.append(parse(q))
        print(f"표기 흔들림 집계 범위: {len(pool)}개 파일", file=sys.stderr)

    report = build(src, pool)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"저장: {args.out} ({len(report):,}자)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
