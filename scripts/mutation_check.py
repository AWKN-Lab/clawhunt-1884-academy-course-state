#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异自证：证明测试真能抓住回归，不是恒绿。

A 相：原样跑 pytest 全过。
B 相：依次注入 3 个变异（每次只改一处，跑完立即恢复并核对 SHA256）：
  M1 删除合法流转 pending_review -> published，预期 test_legal_approve 失败；
  M2 关掉提交人自审拦截（if 条件改 False），预期 test_self_approval 失败；
  M3 删掉审计日志写入，预期 test_audit_log_records_every_transition 失败。
任一相不符合预期即 exit 1；全部符合打印 MUTATION CHECK: PASS。
用法：python3 scripts/mutation_check.py [pytest解释器]
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "course_state.py"

MUTANTS = [
    (
        "M1-remove-legal-approve",
        '    ("pending_review", "published"),\n',
        "",
        ["tests/test_course_state.py::test_legal_approve_pending_review_to_published"],
    ),
    (
        "M2-disable-self-approval-guard",
        "if course.submitted_by is not None and course.submitted_by == actor.user_id:",
        "if False:  # MUTATED: self-approval guard disabled",
        ["tests/test_course_state.py::test_self_approval_returns_403"],
    ),
    (
        "M3-drop-audit-write",
        "        self.audit_log.append(entry)",
        "        pass  # MUTATED: audit write removed",
        ["tests/test_course_state.py::test_audit_log_records_every_transition"],
    ),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(py: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [py, "-m", "pytest", "-q", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )


def main() -> int:
    py = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    original = TARGET.read_text(encoding="utf-8")
    sha_before = sha(TARGET)

    a = run(py, ["tests/"])
    last = a.stdout.strip().splitlines()[-1] if a.stdout.strip() else ""
    print("A-phase:", last)
    if a.returncode != 0:
        print("MUTATION CHECK: FAIL (A phase did not pass)")
        print(a.stdout[-2000:], a.stderr[-2000:])
        return 1

    caught = 0
    for name, anchor, mutated, tests in MUTANTS:
        assert anchor in original, f"mutation anchor not found: {name}"
        TARGET.write_text(original.replace(anchor, mutated, 1),
                          encoding="utf-8")
        try:
            b = run(py, tests)
        finally:
            TARGET.write_text(original, encoding="utf-8")
        ok = b.returncode != 0 and sha(TARGET) == sha_before
        print(f"{name}: returncode={b.returncode} restored_sha_ok={sha(TARGET) == sha_before}")
        if not ok:
            print(f"MUTATION CHECK: FAIL ({name} not caught or file not restored)")
            print(b.stdout[-2000:], b.stderr[-2000:])
            return 1
        caught += 1

    c = run(py, ["tests/"])
    last = c.stdout.strip().splitlines()[-1] if c.stdout.strip() else ""
    print("C-phase:", last)
    if c.returncode != 0 or sha(TARGET) != sha_before:
        print("MUTATION CHECK: FAIL (C phase did not pass or file changed)")
        return 1

    print(f"MUTATION CHECK: PASS (mutants caught {caught}/3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
