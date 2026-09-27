#!/usr/bin/env python3
"""Add deterministic version links to existing practice snapshots and submissions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


FILES = {
    "catalog": "data/practice-catalog.json",
    "assignments": "data/practice-assignments.json",
    "submissions": "data/practice-submissions.json",
}


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def task_version(task: dict) -> str:
    content = {key: value for key, value in task.items() if key != "taskVersionId"}
    return "tv1-" + canonical_hash(content)[:24]


def assignment_version(assignment: dict) -> str:
    identity = {
        "practiceId": assignment.get("practiceId", ""),
        "sourceLessonId": assignment.get("sourceLessonId", ""),
        "taskIds": assignment.get("taskIds", []),
        "taskVersionIds": [task.get("taskVersionId") for task in assignment.get("tasks", [])],
    }
    return "av1-" + canonical_hash(identity)[:24]


def read_json(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ValueError(f"expected a JSON array of objects: {path}")
    return value


def prepare(root: Path) -> tuple[dict[str, list[dict]], dict[str, int]]:
    data = {name: read_json(root / rel) for name, rel in FILES.items()}
    catalog = data["catalog"]
    assignments = data["assignments"]
    submissions = data["submissions"]

    def require_unique(items: list[dict], field: str, label: str) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for item in items:
            key = item.get(field)
            if not isinstance(key, str) or not key:
                raise ValueError(f"{label} row missing {field}")
            if key in result:
                raise ValueError(f"duplicate {label} key: {field}")
            result[key] = item
        return result

    catalog_by_id = require_unique(catalog, "id", "catalog task")
    assignment_by_id = require_unique(assignments, "assignmentId", "assignment")
    require_unique(submissions, "submissionId", "submission")

    for task in catalog:
        version = task_version(task)
        if task.get("taskVersionId") not in (None, version):
            raise ValueError("catalog taskVersionId conflicts with snapshot content")
        task["taskVersionId"] = version

    assignment_versions: dict[str, str] = {}
    task_versions_by_assignment: dict[str, dict[str, str]] = {}
    for assignment in assignments:
        snapshots = assignment.get("tasks")
        task_ids = assignment.get("taskIds")
        if not isinstance(snapshots, list) or not isinstance(task_ids, list):
            raise ValueError("assignment missing task snapshots or taskIds")
        snapshot_ids = [task.get("id") for task in snapshots if isinstance(task, dict)]
        if len(snapshot_ids) != len(snapshots) or set(map(str, snapshot_ids)) != set(map(str, task_ids)):
            raise ValueError("assignment taskIds do not match its task snapshots")
        versions: dict[str, str] = {}
        for task in snapshots:
            version = task_version(task)
            if task.get("taskVersionId") not in (None, version):
                raise ValueError("assignment taskVersionId conflicts with snapshot content")
            task["taskVersionId"] = version
            versions[str(task["id"])] = version
        version = assignment_version(assignment)
        if assignment.get("assignmentVersionId") not in (None, version):
            raise ValueError("assignmentVersionId conflicts with its frozen task list")
        assignment["assignmentVersionId"] = version
        assignment_versions[assignment["assignmentId"]] = version
        task_versions_by_assignment[assignment["assignmentId"]] = versions

    for submission in submissions:
        assignment_id = submission.get("assignmentId")
        if assignment_id not in assignment_by_id:
            raise ValueError("submission references missing assignment")
        version = assignment_versions[assignment_id]
        if submission.get("assignmentVersionId") not in (None, version):
            raise ValueError("submission assignmentVersionId conflicts with its assignment")
        submission["assignmentVersionId"] = version
        versions = task_versions_by_assignment[assignment_id]
        for answer in submission.get("answers", []):
            task_id = str(answer.get("taskId") or answer.get("questionId") or "")
            if not task_id or task_id not in versions:
                raise ValueError("submission answer references a task outside its assignment")
            version = versions[task_id]
            if answer.get("taskVersionId") not in (None, version):
                raise ValueError("answer taskVersionId conflicts with its assignment snapshot")
            answer["taskVersionId"] = version
        for submitted_file in submission.get("files", []):
            task_id = str(submitted_file.get("taskId") or submitted_file.get("questionId") or "")
            if task_id:
                if task_id not in versions:
                    raise ValueError("submission file references a task outside its assignment")
                submitted_file["taskVersionId"] = versions[task_id]

    counts = {
        "catalogTasks": len(catalog),
        "assignments": len(assignments),
        "taskSnapshots": sum(len(x.get("tasks", [])) for x in assignments),
        "submissions": len(submissions),
        "answers": sum(len(x.get("answers", [])) for x in submissions),
    }
    return data, counts


def audit(data: dict[str, list[dict]]) -> list[str]:
    errors: list[str] = []
    for task in data["catalog"]:
        if task.get("taskVersionId") != task_version(task):
            errors.append("catalog task version hash mismatch")
    assignments = {x["assignmentId"]: x for x in data["assignments"]}
    for assignment in assignments.values():
        if assignment.get("assignmentVersionId") != assignment_version(assignment):
            errors.append("assignment version hash mismatch")
        for task in assignment.get("tasks", []):
            if task.get("taskVersionId") != task_version(task):
                errors.append("assignment task version hash mismatch")
    for submission in data["submissions"]:
        assignment = assignments.get(submission.get("assignmentId"))
        if assignment is None or submission.get("assignmentVersionId") != assignment.get("assignmentVersionId"):
            errors.append("submission is not linked to its assignment version")
            continue
        versions = {str(t.get("id")): t.get("taskVersionId") for t in assignment.get("tasks", [])}
        for answer in submission.get("answers", []):
            task_id = str(answer.get("taskId") or answer.get("questionId") or "")
            if answer.get("taskVersionId") != versions.get(task_id):
                errors.append("submission answer is not linked to its task version")
    return errors


def atomic_write(path: Path, value: list[dict]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_name, path.stat().st_mode & 0o777)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and os.environ.get("PRACTICE_MIGRATION_QUIESCED") != "1":
        parser.error("--apply requires the write service to be stopped and PRACTICE_MIGRATION_QUIESCED=1")
    data, counts = prepare(args.root)
    errors = audit(data)
    if errors:
        print("\n".join(errors))
        return 1
    print("Audit passed:", json.dumps(counts, sort_keys=True))
    if args.apply:
        for key in ("catalog", "assignments", "submissions"):
            atomic_write(args.root / FILES[key], data[key])
        print("Applied deterministic task and assignment version IDs.")
    else:
        print("Dry run only; no files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
