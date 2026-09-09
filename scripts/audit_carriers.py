"""운반 딕셔너리 감사 — 표시 계층이 "그 딕셔너리에 없는 키"를 읽고 있지 않은가.

왜 필요한가
-----------
2026-09-09에 같은 유형으로 두 번 물렸다. `_daily_gate`가

    (data.get("market_summary", {}) or {}).get("run_type")

를 읽었는데 `run_type`은 `report_data["metadata"]`에만 있다. 늘 `None`이라
9/3부터 엿새간 모든 대시보드의 대형주 트랙 문구가 실행 종류와 무관하게 같았다.
**에러가 안 난다.** `.get()`은 조용히 `None`을 준다. pyflakes도 스모크도 못 잡는다.
함수 자체는 옳았기 때문이다 — 틀린 것은 넘기는 값이다.

무엇을 하는가
-------------
`pipeline.py`가 만드는 운반 딕셔너리(`report_data`·`metadata`·`market_summary`·
`_ms_extra`·`_summary_data`)의 키 집합을 뽑고, 표시 계층이 그 딕셔너리에서
읽는 키가 실제로 들어 있는지 대조한다.

한계 — 이 스크립트가 "없음"이라고 할 때 그 말의 범위
----------------------------------------------------
수신자 식을 운반 경로로 풀 수 있는 읽기만 검사한다. 종목별 딕셔너리(`c`)처럼
수십 곳에서 조립되는 것은 키 집합을 정적으로 확정할 수 없어 **검사 대상이 아니다**.
`--verbose`가 검사/미검사 건수를 같이 찍는다. 커버리지를 보고 읽을 것.

    python -m scripts.audit_carriers            # 실패하면 exit 1
    python -m scripts.audit_carriers -v         # 커버리지와 운반 목록도 출력
"""

from __future__ import annotations

import argparse
import ast
import collections
import io
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CARRIER_NAMES = ("report_data", "_ms_extra", "_summary_data")

# 소비 파일 -> {인자 이름: 운반 이름}
_CONSUMERS = {
    "scripts/_dashboard_sections.py": {"data": "report_data"},
    "scripts/dashboard.py":           {"data": "report_data"},
    "scripts/notifier.py":            {"extra": "_ms_extra", "ex": "_ms_extra"},
}


def _dict_keys(node: ast.Dict) -> set[str]:
    return {k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def collect_carriers() -> dict[str, set[str]]:
    """pipeline.py가 만드는 운반 딕셔너리의 키 집합."""
    tree = ast.parse(io.open(_ROOT / "scripts/pipeline.py", encoding="utf-8").read())
    car: dict[str, set[str]] = collections.defaultdict(set)
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
           and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Dict) \
           and n.targets[0].id in _CARRIER_NAMES:
            name = n.targets[0].id
            car[name] |= _dict_keys(n.value)
            for k, v in zip(n.value.keys, n.value.values):     # 중첩 리터럴
                if isinstance(k, ast.Constant) and isinstance(v, ast.Dict):
                    car[f"{name}.{k.value}"] |= _dict_keys(v)
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if not isinstance(t, ast.Subscript) or not isinstance(t.slice, ast.Constant):
                    continue
                if isinstance(t.value, ast.Name) and t.value.id in _CARRIER_NAMES:
                    car[t.value.id].add(t.slice.value)          # report_data["x"] =
                elif isinstance(t.value, ast.Subscript) \
                        and isinstance(t.value.value, ast.Name) \
                        and t.value.value.id in _CARRIER_NAMES \
                        and isinstance(t.value.slice, ast.Constant):
                    car[f"{t.value.value.id}.{t.value.slice.value}"].add(t.slice.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
           and n.func.attr == "update" and isinstance(n.func.value, ast.Name) \
           and n.func.value.id in _CARRIER_NAMES and n.args and isinstance(n.args[0], ast.Dict):
            car[n.func.value.id] |= _dict_keys(n.args[0])
    return dict(car)


def scan(path: str, param_map: dict[str, str], car: dict[str, set[str]]):
    """(위반 목록, 검사 건수, 미해석 건수)."""
    tree = ast.parse(io.open(_ROOT / path, encoding="utf-8").read())
    bad, checked, unresolved = [], 0, 0
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        alias = {a.arg: param_map[a.arg]
                 for a in fn.args.args + fn.args.kwonlyargs if a.arg in param_map}

        def resolve(e, depth=0):
            """수신자 식 -> 운반 경로. 체이닝(`a.get('b').get('c')`)까지 따라간다.

            1판은 `x = data.get("metadata")` 형태만 봐서 실제 버그를 놓쳤다.
            """
            if depth > 6:
                return None
            if isinstance(e, ast.Name):
                return alias.get(e.id)
            if isinstance(e, ast.BoolOp) and e.values:                 # (a or {})
                return resolve(e.values[0], depth + 1)
            if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) \
               and e.func.attr == "get" and e.args and isinstance(e.args[0], ast.Constant) \
               and isinstance(e.args[0].value, str):
                base = resolve(e.func.value, depth + 1)
                return f"{base}.{e.args[0].value}" if base else None
            if isinstance(e, ast.Subscript) and isinstance(e.slice, ast.Constant) \
               and isinstance(e.slice.value, str):
                base = resolve(e.value, depth + 1)
                return f"{base}.{e.slice.value}" if base else None
            return None

        for _ in range(2):                        # 연쇄 대입도 잡히게 두 번
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                   and isinstance(n.targets[0], ast.Name):
                    r = resolve(n.value)
                    if r:
                        alias.setdefault(n.targets[0].id, r)

        for n in ast.walk(fn):
            recv = key = None
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
               and n.func.attr == "get" and n.args and isinstance(n.args[0], ast.Constant) \
               and isinstance(n.args[0].value, str):
                recv, key = n.func.value, n.args[0].value
            elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) \
                    and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                recv, key = n.value, n.slice.value
            if recv is None:
                continue
            path_ = resolve(recv)
            if path_ in car:
                checked += 1
                if key not in car[path_]:
                    bad.append((path.split("/")[-1], n.lineno, fn.name, path_, key))
            else:
                unresolved += 1
    return bad, checked, unresolved


def main() -> int:
    ap = argparse.ArgumentParser(description="운반 딕셔너리 키 대조")
    ap.add_argument("-v", "--verbose", action="store_true", help="커버리지·운반 목록도 출력")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    car = collect_carriers()
    if args.verbose:
        for k in sorted(car):
            print(f"  {k:32s} {len(car[k]):3d}종")
        print()

    bad, checked, unresolved = [], 0, 0
    for path, pm in _CONSUMERS.items():
        b, c, u = scan(path, pm, car)
        bad += b; checked += c; unresolved += u

    if args.verbose:
        total = checked + unresolved
        print(f"검사 {checked} / 미해석 {unresolved} "
              f"→ 커버리지 {checked / total * 100:.1f}%" if total else "읽기 없음")
        print("미해석은 종목별 딕셔너리처럼 키 집합을 정적으로 확정할 수 없는 것들이다.\n")

    if bad:
        print(f"운반 딕셔너리에 없는 키를 읽는 곳 {len(bad)}건")
        for f, ln, fn, carrier, key in bad:
            print(f"  {f}:{ln} {fn}()  {carrier}에 없는 \"{key}\"를 읽는다")
        print("\n조용히 None이 흘러 화면에 틀린 값이 나갑니다. 에러로는 안 드러납니다.")
        return 1
    print(f"운반 키 대조 통과 — {checked}건 검사, 불일치 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
