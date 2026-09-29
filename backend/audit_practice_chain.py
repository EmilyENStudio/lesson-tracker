#!/usr/bin/env python3
"""Read-only audit of catalog, frozen distributions, and student submissions."""

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

from practice_versioning import link_assignment_version, link_submission_version, task_version


def audit_chain(catalog, assignments, submissions):
    issues = Counter()

    def index(items, key, label):
        result = {}
        for item in items:
            value = item.get(key)
            if not isinstance(value, str) or not value:
                issues[label + ".missing_id"] += 1
            elif value in result:
                issues[label + ".duplicate_id"] += 1
            else:
                result[value] = item
        return result

    tasks = index(catalog, "id", "catalog")
    distributions = index(assignments, "assignmentId", "assignment")
    index(submissions, "submissionId", "submission")
    historical = 0
    snapshots = 0
    for task in catalog:
        if task.get("taskVersionId") != task_version(task):
            issues["catalog.invalid_task_version"] += 1
    for assignment in assignments:
        frozen = assignment.get("tasks") or []
        if not isinstance(frozen, list):
            issues["assignment.invalid_tasks"] += 1
            continue
        snapshots += len(frozen)
        if not assignment.get("assignmentVersionId"):
            issues["assignment.missing_version"] += 1
        for task in frozen:
            if not isinstance(task, dict):
                issues["assignment.invalid_task"] += 1
                continue
            if not task.get("taskVersionId"):
                issues["assignment.missing_task_version"] += 1
            current = tasks.get(task.get("id"))
            historical += int(current is None or task_version(current) != task_version(task))
        try:
            link_assignment_version(copy.deepcopy(assignment))
        except (ValueError, TypeError):
            issues["assignment.invalid_snapshot_links"] += 1

    student_distributions = set()
    for submission in submissions:
        assignment = distributions.get(submission.get("assignmentId"))
        if assignment is None:
            issues["submission.missing_assignment"] += 1
            continue
        student_id = str(submission.get("studentId") or "").casefold()
        recipients = assignment.get("studentIds") or assignment.get("targetIds") or []
        if not student_id or student_id not in {str(x).casefold() for x in recipients}:
            issues["submission.wrong_recipient"] += 1
        pair = (assignment["assignmentId"], student_id)
        if pair in student_distributions:
            issues["submission.duplicate_student_distribution"] += 1
        student_distributions.add(pair)
        if not submission.get("assignmentVersionId"):
            issues["submission.missing_version"] += 1
        for field in ("answers", "files"):
            values = submission.get(field, [])
            if not isinstance(values, list):
                issues["submission.invalid_" + field] += 1
                continue
            for value in values:
                if isinstance(value, dict) and (value.get("taskId") or value.get("questionId")) and not value.get("taskVersionId"):
                    issues["submission.missing_" + field + "_version"] += 1
        try:
            link_submission_version(copy.deepcopy(submission), copy.deepcopy(assignment))
        except (ValueError, TypeError):
            issues["submission.invalid_version_links"] += 1

    return {
        "counts": {"catalogTasks": len(catalog), "assignments": len(assignments),
                   "taskSnapshots": snapshots, "historicalSnapshots": historical,
                   "submissions": len(submissions),
                   "withdrawnAssignments": sum(x.get("status") == "withdrawn" for x in assignments)},
        "issues": dict(sorted(issues.items())),
        "ok": not issues,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    lists = []
    for filename in ("practice-catalog.json", "practice-assignments.json", "practice-submissions.json"):
        data = json.loads((args.root / "data" / filename).read_text(encoding="utf-8"))
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
            raise ValueError("expected JSON array of objects: " + filename)
        lists.append(data)
    report = audit_chain(*lists)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
