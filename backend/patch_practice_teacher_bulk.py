#!/usr/bin/env python3
"""Align bulk withdrawal copy and only update records that actually succeeded."""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path


REPLACEMENTS = (
    (
        "确定删除选中的 '+ids.length+' 条推送记录？关联的待批改作业也会一并删除。该操作不可恢复。",
        "确定撤回选中的 '+ids.length+' 条作业？学生将无法继续看到，历史提交和老师批改会保留。",
    ),
    (
        "      refreshAfterDelete(ids);\n      if(ok<ids.length) alert('已删除 '+ok+' 条，其余 '+ (ids.length-ok) +' 条删除失败，请重试。');\n      else if(ok>0) alert('已删除 '+ok+' 条推送记录及关联待批改作业 ✅');",
        "      var succeeded=ids.filter(function(id,i){return res[i];});\n      refreshAfterDelete(succeeded);\n      if(ok<ids.length) alert('已撤回 '+ok+' 条，其余 '+ (ids.length-ok) +' 条失败，请重试。');\n      else if(ok>0) alert('已撤回 '+ok+' 条作业，历史提交和批改记录已保留。');",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    old = args.source_file.read_text(encoding="utf-8")
    new = old
    for before, after in REPLACEMENTS:
        if new.count(before) != 1:
            raise ValueError(f"expected one bulk UI target: {before[:50]!r}")
        new = new.replace(before, after, 1)
    if not args.apply:
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="live/practice-teacher.html", tofile="updated/practice-teacher.html", lineterm="")
        print("\n".join(diff))
        print("Dry run only; no files changed.")
        return 0
    args.source_file.write_text(new, encoding="utf-8")
    print("Applied the scoped bulk-withdrawal UI update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
