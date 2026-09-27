"""Dry-run or transactionally backfill IDs on legacy student JSON profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hours_ledger import COLLECTIONS, audit_profile, ensure_profile_ids


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _profiles(root: Path) -> list[tuple[str, Path]]:
    index = json.loads((root / "students.json").read_text(encoding="utf-8"))
    result = []
    for student in index:
        student_id = str(student.get("id") or "")
        if not student_id or student.get("role") == "teacher" or student_id in {"teacher", "emily_mo"}:
            continue
        data_file = str(student.get("dataFile") or student_id)
        if Path(data_file).name != data_file:
            raise ValueError("Student dataFile must be a plain filename stem")
        path = (root / f"{data_file}.json").resolve()
        path.relative_to(root.resolve())
        if not path.is_file():
            raise FileNotFoundError(f"Missing student profile file: {path.name}")
        result.append((student_id, path))
    return result


def _transform(student_id: str, raw: bytes) -> tuple[bytes, dict[str, Any]]:
    profile = json.loads(raw.decode("utf-8"))
    if not isinstance(profile, dict):
        raise ValueError("Student profile must be a JSON object")
    before_ids = sum(
        1 for collection in COLLECTIONS
        for item in _as_items(profile.get(collection))
        if isinstance(item, dict) and item.get("id")
    )
    before_sessions = sum(
        1 for collection in ("records", "leaveRequests", "oneTimeClasses")
        for item in _as_items(profile.get(collection))
        if isinstance(item, dict) and item.get("sessionId")
    )
    ensure_profile_ids(student_id, profile)
    after_ids = sum(
        1 for collection in COLLECTIONS
        for item in _as_items(profile.get(collection))
        if isinstance(item, dict) and item.get("id")
    )
    after_sessions = sum(
        1 for collection in ("records", "leaveRequests", "oneTimeClasses")
        for item in _as_items(profile.get(collection))
        if isinstance(item, dict) and item.get("sessionId")
    )
    encoded = (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return encoded, {
        "idsAdded": after_ids - before_ids,
        "sessionIdsAdded": after_sessions - before_sessions,
        "audit": audit_profile(profile),
    }


def _as_items(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def _atomic_write(path: Path, raw: bytes, mode: int, uid: int, gid: int) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=".ledger-id-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_name, stat.S_IMODE(mode))
        try:
            os.chown(tmp_name, uid, gid)
        except (AttributeError, PermissionError):
            pass
        os.replace(tmp_name, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def run(root: Path, apply: bool, backup_dir: Path | None) -> dict[str, Any]:
    entries = []
    totals: dict[str, int] = {"profiles": 0, "idsAdded": 0, "sessionIdsAdded": 0}
    issue_counts: dict[str, int] = {}

    for student_id, path in _profiles(root):
        original = path.read_bytes()
        updated, report = _transform(student_id, original)
        totals["profiles"] += 1
        totals["idsAdded"] += report["idsAdded"]
        totals["sessionIdsAdded"] += report["sessionIdsAdded"]
        for issue in report["audit"]["issues"]:
            category = issue.rsplit(":", 1)[0]
            issue_counts[category] = issue_counts.get(category, 0) + 1
        entries.append({"studentId": student_id, "path": path, "before": original,
                        "after": updated, "beforeHash": _sha256(original),
                        "report": report})

    if not apply:
        return {**totals, "issues": issue_counts, "applied": False}
    if backup_dir is None:
        raise ValueError("--apply requires --backup-dir outside the web root")
    root_resolved = root.resolve()
    backup_resolved = backup_dir.resolve()
    if backup_resolved == root_resolved or root_resolved in backup_resolved.parents:
        raise ValueError("Backup directory must be outside the web root")
    try:
        active = subprocess.run(["systemctl", "is-active", "--quiet", "emily-backend.service"]).returncode == 0
    except (OSError, FileNotFoundError):
        active = False
    if active:
        raise RuntimeError("Stop emily-backend.service before applying this offline migration")

    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(backup_dir, 0o700)
    manifest = []
    for index, entry in enumerate(entries):
        backup_path = backup_dir / f"profile-{index:03d}.json"
        shutil.copy2(entry["path"], backup_path)
        manifest.append({"file": backup_path.name, "profile": entry["path"].name,
                         "sha256": entry["beforeHash"]})
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(backup_dir / "manifest.json", 0o600)

    changed = []
    try:
        for entry in entries:
            path = entry["path"]
            if _sha256(path.read_bytes()) != entry["beforeHash"]:
                raise RuntimeError("Profile changed during migration preflight")
        for entry in entries:
            path = entry["path"]
            if entry["after"] == entry["before"]:
                continue
            metadata = path.stat()
            _atomic_write(path, entry["after"], metadata.st_mode, metadata.st_uid, metadata.st_gid)
            changed.append(entry)
    except Exception:
        for entry in reversed(changed):
            backup_path = backup_dir / f"profile-{entries.index(entry):03d}.json"
            original = backup_path.read_bytes()
            current = entry["path"].stat()
            _atomic_write(entry["path"], original, current.st_mode, current.st_uid, current.st_gid)
        raise

    return {**totals, "issues": issue_counts, "applied": True,
            "filesChanged": len(changed), "backupDir": str(backup_dir)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/www/lesson-tracker"))
    parser.add_argument("--apply", action="store_true", help="write additive IDs after an external backup")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.apply, args.backup_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
