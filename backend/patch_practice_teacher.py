#!/usr/bin/env python3
"""Make teacher-side withdrawal preserve the locally displayed history."""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path


REPLACEMENTS = (
    (
        """    return assignments.slice().reverse().filter(function(a){
      var st=((a.studentIds||[]).join(' ')+' '+(a.targetIds||[]).join(' '))||'';
""",
        """    return assignments.slice().reverse().filter(function(a){
      if(a.status==='withdrawn') return false;
      var st=((a.studentIds||[]).join(' ')+' '+(a.targetIds||[]).join(' '))||'';
""",
    ),
    (
        """  function refreshAfterDelete(ids){
    var idSet={}; ids.forEach(function(x){idSet[x]=1;});
    assignments=assignments.filter(function(a){return !idSet[(a.assignmentId||'')];});
    submissions=submissions.filter(function(s){return !idSet[(s.assignmentId||'')];});
    ids.forEach(function(x){ delete recSelected[x]; });
""",
        """  function refreshAfterDelete(ids){
    var idSet={}; ids.forEach(function(x){idSet[x]=1;});
    assignments.forEach(function(a){if(idSet[a.assignmentId||''])a.status='withdrawn';});
    ids.forEach(function(x){ delete recSelected[x]; });
""",
    ),
    (
        "确定删除这条推送记录？关联的待批改作业也会一并删除。该操作不可恢复。",
        "确定撤回这条作业？学生将无法继续看到，已提交答案和老师批改会保留。",
    ),
    (
        '">删除</button></div>',
        '">撤回</button></div>',
    ),
)


def patched_source(source: str) -> str:
    for old, new in REPLACEMENTS:
        count = source.count(old)
        if count != 1:
            raise ValueError(f"expected exactly one frontend patch target, found {count}: {old[:60]!r}")
        source = source.replace(old, new, 1)
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    old = args.source_file.read_text(encoding="utf-8")
    new = patched_source(old)
    if not args.apply:
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="live/practice-teacher.html", tofile="updated/practice-teacher.html", lineterm="")
        print("\n".join(diff))
        print("Dry run only; no files changed.")
        return 0
    args.source_file.write_text(new, encoding="utf-8")
    print("Applied the scoped teacher practice UI update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
