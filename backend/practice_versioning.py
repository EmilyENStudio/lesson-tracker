#!/usr/bin/env python3
"""Shared deterministic identifiers for frozen practice content."""

from __future__ import annotations

import hashlib
import json


def _hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def task_version(task: dict) -> str:
    return "tv1-" + _hash({key: value for key, value in task.items() if key != "taskVersionId"})


def assignment_version(assignment: dict) -> str:
    return "av1-" + _hash({
        "practiceId": assignment.get("practiceId", ""),
        "sourceLessonId": assignment.get("sourceLessonId", ""),
        "taskIds": assignment.get("taskIds", []),
        "taskVersionIds": [task.get("taskVersionId") for task in assignment.get("tasks", [])],
    })


def link_assignment_version(assignment: dict) -> None:
    snapshots = assignment.get("tasks")
    task_ids = assignment.get("taskIds")
    if not isinstance(snapshots, list) or not isinstance(task_ids, list):
        raise ValueError("assignment requires taskIds and frozen task snapshots")
    if any(not isinstance(task, dict) or not task.get("id") for task in snapshots):
        raise ValueError("assignment contains an invalid task snapshot")
    snapshot_ids = {str(task["id"]) for task in snapshots}
    if snapshot_ids != {str(task_id) for task_id in task_ids}:
        raise ValueError("taskIds do not match frozen task snapshots")
    for task in snapshots:
        expected = task_version(task)
        if task.get("taskVersionId") not in (None, expected):
            raise ValueError("task snapshot version does not match its content")
        task["taskVersionId"] = expected
    expected = assignment_version(assignment)
    if assignment.get("assignmentVersionId") not in (None, expected):
        raise ValueError("assignment version does not match its frozen tasks")
    assignment["assignmentVersionId"] = expected


def link_submission_version(submission: dict, assignment: dict) -> None:
    link_assignment_version(assignment)
    versions = {str(task["id"]): task["taskVersionId"] for task in assignment["tasks"]}
    submission["assignmentVersionId"] = assignment["assignmentVersionId"]
    for field in ("answers", "files"):
        values = submission.get(field, [])
        if not isinstance(values, list):
            raise ValueError(f"submission {field} must be a list")
        for item in values:
            if not isinstance(item, dict):
                raise ValueError(f"submission contains an invalid {field} item")
            task_id = str(item.get("taskId") or item.get("questionId") or "")
            if not task_id:
                if field == "answers":
                    raise ValueError("submission answer is missing its task ID")
                continue
            if task_id not in versions:
                raise ValueError("submission answer refers to a task outside its assignment")
            item["taskVersionId"] = versions[task_id]
