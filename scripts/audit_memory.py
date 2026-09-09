"""메모리 정합성 검사 — 낡은 사실이 몇 주씩 살아 있는 것을 막는다.

왜 필요한가
-----------
2026-09-09 메모리 정리에서 모순 4건이 나왔는데, **3건이 같은 원인**이었다.

  > 코드의 현재 상태를 메모리에 적어놨는데, 코드가 바뀌었다.

스케줄("1차=14:50"), 시크릿 표(`TELEGRAM_CHAT_ID_2`), 패턴 목록("김형준 기법"),
구현 여부("후발주 강등: 다음 후보" — 같은 파일 아래 절에는 "완료"라고 적혀 있었다).
전부 코드를 보면 1초에 아는 것들이고, 각각 **두 달씩** 틀린 채로 있었다.

`consolidate-memory` 스킬이 이걸 잡긴 하지만 메모리 전체(약 300KB)를 통독해야 해서
매번 돌리기엔 비싸고, 무엇보다 **사후 청소**다. 이 스크립트는 싸고 즉시 돈다.

무엇을 보는가
-------------
  1. 코드에서 사라진 이름이 메모리에 남아 있는가
  2. 같은 수치가 두 파일에서 다른가 (승률·비중처럼 자주 인용되는 것)
  3. MEMORY.md 인덱스에 있는 링크가 실제 파일을 가리키는가
  4. 메모리가 주장하는 알림 시각이 `market_calendar.get_run_type`과 맞는가

무엇을 못 보는가 (정직하게)
---------------------------
**요약하면서 결론이 뒤집힌 것.** 2026-09-09에 실제로 있었다 — MEMORY.md의
`reference_broker_api_findings` 한 줄이 "재개 시 KIS부터"였는데 본문 결론은
"KIS로도 안 된다"였다. 문법도 수치도 안 틀려서 기계로는 안 잡힌다.
이건 인덱스를 쓸 때 **본문 결론 문장을 그대로 옮기고 내 말로 바꾸지 않는 것**으로만 막는다.

    python -m scripts.audit_memory          # 실패하면 exit 1
    python -m scripts.audit_memory -v       # 통과 항목도 출력
"""

from __future__ import annotations

import argparse
import io
import os
import pathlib
import re
import subprocess
import sys

_MEM = pathlib.Path(
    r"C:\Users\purpl\.claude\projects\C--Users-purpl-Jongbe-Project\memory")
_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 코드에서 제거됐는데 메모리에 남으면 안 되는 이름.
# 값은 "이 이름이 코드에 살아 있는지 확인할 검색어" — 코드에 있으면 검사를 건너뛴다.
_REMOVED_NAMES = {
    "김형준":              "kim_hyungjun",
    "kim_hyungjun":        "kim_hyungjun",
    "TELEGRAM_CHAT_ID_2":  "TELEGRAM_CHAT_ID_2",
}


def _mem_files() -> list[pathlib.Path]:
    return sorted(p for p in _MEM.glob("*.md"))


def _paragraphs(text: str):
    """(시작줄번호, 문단) 목록. 빈 줄로 나눈다.

    ⚠ 줄 단위로 보면 안 된다. 메모리는 한글 산문이라 한 문장이 두세 줄로
    소프트랩되고, 과거형 어미("살아 있었다")가 다음 줄로 넘어가면 이력 문장을
    현재형 주장으로 오판한다. 2026-09-09에 실제로 이 오탐이 났다.
    """
    out, buf, start = [], [], 1
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            if not buf:
                start = i
            buf.append(line)
        elif buf:
            out.append((start, " ".join(buf)))
            buf = []
    if buf:
        out.append((start, " ".join(buf)))
    return out


def _grep_code(term: str) -> bool:
    """코드·워크플로에 이 이름이 **살아 있는 경로로** 남아 있는가.

    ⚠ 세 곳을 빼야 한다. 처음 만들었을 때 전부 빼먹어서 "아직 코드에 있음"이
    잘못 나왔다(2026-09-09) — 이 스크립트 자신이 검색어를 소스에 담고 있고,
    `_archive/`는 의도적으로 보존한 죽은 코드이며, `__pycache__`는 빌드 산물이다.
    """
    _self = pathlib.Path(__file__).resolve()
    for sub in ("scripts", "config", ".github"):
        d = _ROOT / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if not (p.is_file() and p.suffix in (".py", ".yml", ".yaml")):
                continue
            if p.resolve() == _self:
                continue
            if "_archive" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                if term in p.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except Exception:
                pass
    return False


def check_removed_names(verbose: bool) -> list[str]:
    """1. 코드에서 사라진 이름이 메모리에 남아 있는가."""
    fails = []
    for name, code_term in _REMOVED_NAMES.items():
        if _grep_code(code_term):
            if verbose:
                print(f"  건너뜀 — '{name}'은 코드에 아직 있음")
            continue
        for f in _mem_files():
            txt = f.read_text(encoding="utf-8", errors="ignore")
            # 파일 자체가 그 제거를 기록한 문서면 본문 언급은 전부 이력이다.
            # (예: project_telegram_multi_chat.md — description에 "영구 제거"가 있다)
            head = txt[:600]
            if any(w in head for w in ("제거", "삭제", "폐기")) and name in head:
                if verbose:
                    print(f"  건너뜀 — {f.name}은 '{name}' 제거를 기록한 문서")
                continue
            for i, line in _paragraphs(txt):
                if name in line:
                    # 이 검사가 잡으려는 것은 **현재형 주장**이다.
                    # 과거형 문장은 "예전에 이랬다"는 이력이라 정상이고, 오히려
                    # 남겨야 한다(왜 뺐는지가 재발을 막는다).
                    if any(w in line for w in ("제거", "삭제", "폐기", "뺐다", "없앴",
                                               "죽은 코드", "였음", "이었",
                                               "었다", "았다", "였다", "있었")):
                        continue
                    fails.append(f"{f.name}:{i} 코드에 없는 '{name}'을 현재형으로 언급")
    return fails


def check_index_links(verbose: bool) -> list[str]:
    """3. MEMORY.md 인덱스 링크가 실제 파일을 가리키는가."""
    idx = _MEM / "MEMORY.md"
    if not idx.exists():
        return ["MEMORY.md 없음"]
    fails = []
    txt = idx.read_text(encoding="utf-8", errors="ignore")
    linked = set()
    for m in re.finditer(r"\]\(([A-Za-z0-9_\-]+\.md)\)", txt):
        target = m.group(1)
        linked.add(target)
        if not (_MEM / target).exists():
            fails.append(f"MEMORY.md → 없는 파일 링크: {target}")
    # 역방향: 인덱스에 안 실린 메모리
    orphan = [f.name for f in _mem_files()
              if f.name not in linked and f.name not in ("MEMORY.md",)]
    if orphan:
        fails.append("인덱스에 없는 메모리: " + ", ".join(orphan))
    if verbose:
        print(f"  인덱스 링크 {len(linked)}개 확인")
    return fails


def check_alert_times(verbose: bool) -> list[str]:
    """4. 메모리가 적어둔 알림 시각이 코드의 판정 창과 맞는가.

    2026-09-09에 `feedback_format.md`가 "1차=14:50"이라고 적고 있었는데
    실제 판정 창은 14:15-14:45였다. 스케줄이 바뀐 뒤 용어 파일이 방치된 것이다.
    """
    fails = []
    try:
        sys.path.insert(0, str(_ROOT))
        from datetime import datetime
        from scripts.market_calendar import get_run_type
    except Exception as e:
        return [f"market_calendar 로드 실패: {e}"]

    # 메모리에 "N차 = HH:MM" 형태로 적힌 것을 모아 코드와 대조
    pat = re.compile(r'"?([123])차"?\s*[=은는]\s*[^0-9]{0,12}(\d{1,2}):(\d{2})')
    _past = ("였다", "있었", "었다", "았다", "폐기", "제거", "옛", "이전",
             "바뀌", "적고 있었", "방치")
    for f in _mem_files():
        for i, line in _paragraphs(f.read_text(encoding="utf-8", errors="ignore")):
            # 과거형·정정 문장은 "예전엔 이랬다"는 이력이라 대조 대상이 아니다.
            if any(w in line for w in _past):
                continue
            for m in pat.finditer(line):
                nth, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
                actual = get_run_type(datetime(2026, 9, 9, hh, mm))
                expect = {"1": "1차", "2": "2차", "3": "수동"}[nth]
                if actual != expect:
                    fails.append(
                        f"{f.name}:{i} \"{nth}차 = {hh:02d}:{mm:02d}\"인데 "
                        f"그 시각의 실제 판정은 '{actual}' (기대 '{expect}')")
                elif verbose:
                    print(f"  OK {f.name}:{i} {nth}차 {hh:02d}:{mm:02d} → {actual}")
    return fails


def check_number_drift(verbose: bool) -> list[str]:
    """2. 자주 인용되는 수치가 파일마다 다른가.

    승률 정의를 고친 뒤 월별 표는 경고만 달고 옛 값을 뒀던 일이 실제로 있었다
    (2026-09-09에 4개 파일에서 발견). 대표 수치만 골라 본다.
    """
    fails = []
    # (라벨, 정규식, 정본 파일) — 정본과 다른 값이 다른 파일에 있으면 걸린다
    targets = [
        ("전체 양수비율", re.compile(r"전체\s*\*{0,2}(\d{2}\.\d)%"), "backtest_monthly_performance.md"),
    ]
    for label, rx, canon_name in targets:
        canon_file = _MEM / canon_name
        if not canon_file.exists():
            continue
        m = rx.search(canon_file.read_text(encoding="utf-8", errors="ignore"))
        if not m:
            continue
        canon = m.group(1)
        if verbose:
            print(f"  {label} 정본 = {canon}% ({canon_name})")
        for f in _mem_files():
            if f.name == canon_name:
                continue
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(txt.splitlines(), 1):
                mm = rx.search(line)
                if mm and mm.group(1) != canon:
                    if any(w in line for w in ("옛", "정정", "이전", "아님", "과거")):
                        continue
                    fails.append(
                        f"{f.name}:{i} {label} {mm.group(1)}% — 정본은 {canon}% ({canon_name})")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description="메모리 정합성 검사")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not _MEM.is_dir():
        print(f"메모리 경로 없음: {_MEM}")
        return 1

    checks = [
        ("코드에서 사라진 이름", check_removed_names),
        ("수치 불일치",         check_number_drift),
        ("인덱스 링크",         check_index_links),
        ("알림 시각",           check_alert_times),
    ]
    all_fails = []
    for label, fn in checks:
        if args.verbose:
            print(f"[{label}]")
        found = fn(args.verbose)
        all_fails += [f"{label}: {x}" for x in found]

    if all_fails:
        print(f"\n메모리 정합성 {len(all_fails)}건 불일치\n")
        for x in all_fails:
            print(f"  - {x}")
        print("\n낡은 사실이 메모리에 남으면 다음 세션이 그걸 근거로 답합니다.")
        return 1
    print(f"메모리 정합성 통과 — 파일 {len(_mem_files())}개, 검사 {len(checks)}종")
    print("⚠ 요약하며 결론이 뒤집힌 것은 이 검사로 못 잡습니다(2026-09-09 실제 사례).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
