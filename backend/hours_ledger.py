"""Stable identifiers and read-only consistency checks for legacy student profiles.

This first migration layer is intentionally additive: it never recalculates or
changes cached balances, lesson statuses, or source records.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any


COLLECTIONS = ("payments", "records", "leaveRequests", "oneTimeClasses")
TERMINAL_LEAVE_STATUSES = {
    "approved", "accepted", "resolved", "rejected", "cancelled", "completed"
}


def _stable_digest(prefix: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _session_parts(item: dict[str, Any]) -> tuple[str, str, str] | None:
    course = _text(item.get("courseName") or item.get("course")).casefold()
    date = _text(item.get("date"))
    time = _text(item.get("startTime") or item.get("time"))
    match = re.match(r"^(\d{1,2}:\d{2})", time)
    if not course or not date or not match:
        return None
    hour, minute = (int(part) for part in match.group(1).split(":"))
    if hour > 23 or minute > 59:
        return None
    return course, date, f"{hour:02d}:{minute:02d}"


def _session_id(item: dict[str, Any]) -> str | None:
    parts = _session_parts(item)
    return _stable_digest("session", parts) if parts else None


def _makeup_origin_id(item: dict[str, Any]) -> str | None:
    origin = item.get("makeupOrigin")
    if not isinstance(origin, dict):
        return None
    return _session_id({
        "courseName": origin.get("courseName") or origin.get("course"),
        "date": origin.get("date"),
        "startTime": origin.get("startTime") or origin.get("time"),
    })


def _identity(collection: str, item: dict[str, Any]) -> dict[str, Any]:
    if collection == "payments":
        return {
            "course": _text(item.get("courseName") or item.get("course")).casefold(),
            "date": _text(item.get("date")),
            "firstLessonDate": _text(item.get("firstLessonDate")),
            "amount": item.get("amount"),
            "hours": item.get("hours"),
        }
    if collection == "records":
        return {
            "sessionId": _session_id(item),
            "kind": _text(item.get("kind")).casefold(),
            "makeupForSessionId": _makeup_origin_id(item),
        }
    if collection == "leaveRequests":
        return {
            "sessionId": _session_id(item),
            "type": _text(item.get("type")).casefold(),
            "source": _text(item.get("source")).casefold(),
            "requestTime": _text(item.get("requestTime")),
            "reason": _text(item.get("reason")),
        }
    return {
        "sessionId": _session_id(item),
        "duration": item.get("duration"),
        "kind": _text(item.get("kind")).casefold(),
        "makeupForSessionId": _makeup_origin_id(item),
    }


def ensure_profile_ids(student_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Add stable item and session relationship IDs in place; safe to rerun."""
    session_leaves: dict[str, list[str]] = defaultdict(list)

    for collection in COLLECTIONS:
        raw = profile.get(collection)
        if raw is None:
            continue
        items = [raw] if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            continue

        occurrences: Counter[str] = Counter()
        for item in items:
            if not isinstance(item, dict):
                continue

            session_id = _session_id(item)
            if session_id:
                item.setdefault("sessionId", session_id)

            makeup_for = _makeup_origin_id(item)
            if makeup_for:
                item.setdefault("makeupForSessionId", makeup_for)

            if collection == "records" and makeup_for:
                leave_ids = session_leaves.get(makeup_for, [])
                if len(leave_ids) == 1:
                    item.setdefault("sourceLeaveRequestId", leave_ids[0])

            if collection == "leaveRequests":
                stable_leave_id = item.get("id")
                if stable_leave_id:
                    session_leaves[session_id].append(str(stable_leave_id))

            if not item.get("id"):
                identity = _identity(collection, item)
                signature = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                ordinal = occurrences[signature]
                occurrences[signature] += 1
                item["id"] = _stable_digest("evt", [student_id, collection, identity, ordinal])

        if isinstance(raw, dict):
            profile[collection] = items[0]

    # Relationships are resolved after IDs exist, so record order cannot matter.
    leaves_by_session: dict[str, list[str]] = defaultdict(list)
    leaves = profile.get("leaveRequests") or []
    if isinstance(leaves, dict):
        leaves = [leaves]
    if isinstance(leaves, list):
        for item in leaves:
            if isinstance(item, dict) and item.get("sessionId") and item.get("id"):
                leaves_by_session[item["sessionId"]].append(str(item["id"]))

    for collection in ("records", "oneTimeClasses"):
        items = profile.get(collection) or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("makeupForSessionId"):
                continue
            matches = leaves_by_session.get(item["makeupForSessionId"], [])
            if len(matches) == 1:
                item.setdefault("sourceLeaveRequestId", matches[0])

    return profile


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def audit_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return counts and balance comparisons without changing the profile."""
    issues: list[str] = []
    counts: dict[str, int] = {}
    items_by_collection: dict[str, list[dict[str, Any]]] = {}

    for collection in COLLECTIONS:
        items = [x for x in _as_list(profile.get(collection)) if isinstance(x, dict)]
        items_by_collection[collection] = items
        counts[collection] = len(items)
        ids = [str(x.get("id")) for x in items if x.get("id")]
        if len(ids) != len(items):
            issues.append(f"{collection}:missing_id:{len(items) - len(ids)}")
        duplicates = len(ids) - len(set(ids))
        if duplicates:
            issues.append(f"{collection}:duplicate_id:{duplicates}")

    payments = items_by_collection["payments"]
    records = items_by_collection["records"]
    leaves = items_by_collection["leaveRequests"]
    try:
        paid_hours = sum(float(x.get("hours") or 0) for x in payments)
    except (TypeError, ValueError):
        paid_hours = None
        issues.append("payments:invalid_hours")

    used_hours = 0.0
    missing_record_hours = 0
    for record in records:
        value = record.get("hours")
        if value is None or value == "":
            missing_record_hours += 1
            value = 1
        try:
            number = float(value)
            if number < 0:
                raise ValueError
            used_hours += number
        except (TypeError, ValueError):
            issues.append("records:invalid_hours")
            used_hours = None
            break

    try:
        stored_total = float(profile.get("totalHours") or 0)
        stored_used = float(profile.get("usedHours") or 0)
        stored_remaining = float(profile.get("remainingHours") or 0)
    except (TypeError, ValueError):
        stored_total = stored_used = stored_remaining = None
        issues.append("balance:invalid_cached_value")

    balance = {
        "paidHours": paid_hours,
        "recordCount": len(records),
        "recordHoursAssumingMissingIsOne": used_hours,
        "missingRecordHours": missing_record_hours,
        "storedTotalHours": stored_total,
        "storedUsedHours": stored_used,
        "storedRemainingHours": stored_remaining,
    }
    if paid_hours is not None and stored_total is not None and abs(paid_hours - stored_total) > 1e-6:
        issues.append("balance:total_differs_from_payments")
    if stored_used is not None and abs(stored_used - len(records)) > 1e-6:
        issues.append("balance:used_differs_from_record_count")
    if used_hours is not None and stored_used is not None and abs(stored_used - used_hours) > 1e-6:
        issues.append("balance:used_differs_from_record_hours")
    if paid_hours is not None and used_hours is not None and stored_remaining is not None:
        expected = paid_hours - used_hours
        if abs(stored_remaining - expected) > 1e-6:
            issues.append("balance:remaining_differs_from_hours_basis")

    handled_mismatches = 0
    for leave in leaves:
        status = str(leave.get("status") or "").strip().casefold()
        if status:
            expected_handled = status in TERMINAL_LEAVE_STATUSES
            if bool(leave.get("handled")) != expected_handled:
                handled_mismatches += 1
    if handled_mismatches:
        issues.append(f"leave:handled_status_mismatch:{handled_mismatches}")

    leave_ids = {str(x.get("id")) for x in leaves if x.get("id")}
    orphan_source_refs = sum(
        1 for collection in ("records", "oneTimeClasses")
        for item in items_by_collection[collection]
        if item.get("sourceLeaveRequestId") and str(item["sourceLeaveRequestId"]) not in leave_ids
    )
    if orphan_source_refs:
        issues.append(f"makeup:orphan_leave_reference:{orphan_source_refs}")

    return {"counts": counts, "balance": balance, "issues": issues}
