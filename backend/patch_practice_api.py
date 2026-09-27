#!/usr/bin/env python3
"""Apply a narrowly scoped, exact-match update to the live practice API source."""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path


PRACTICE_ROUTES = '''PRACTICE_ASSIGN_FILE = os.path.join(DATA_DIR, 'practice-assignments.json')
PRACTICE_SUBMIT_FILE = os.path.join(DATA_DIR, 'practice-submissions.json')
practice_write_lock = threading.Lock()

def _load_json_list(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except FileNotFoundError:
        return []

def _save_json_list(path, items):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

@app.route('/api/practice/assignments', methods=['GET'])
@require_auth
def practice_assignments_get(user):
    """Students can only see their own active distributions."""
    items = _load_json_list(PRACTICE_ASSIGN_FILE)
    sid = request.args.get('student', '').strip()
    if not _teacher_write_allowed(user):
        own_id = str(user.get('username') or '').strip()
        if sid and sid.casefold() != own_id.casefold():
            return jsonify({'error': 'Student access denied'}), 403
        sid = own_id
        items = [a for a in items if a.get('status') != 'withdrawn']
    if sid:
        sl = sid.casefold()
        items = [a for a in items if sl in [str(x).casefold() for x in (a.get('studentIds') or [])] or sl in [str(x).casefold() for x in (a.get('targetIds') or [])]]
    return jsonify(items)

@app.route('/api/practice/assignments', methods=['POST'])
@require_auth
def practice_assignments_post(user):
    """Create assignment snapshots with immutable task-version links."""
    if not _teacher_write_allowed(user):
        return jsonify({'error': 'Teacher access required'}), 403
    data = request.get_json(silent=True) or {}
    new_items = data.get('assignments') if isinstance(data.get('assignments'), list) else [data]
    new_items = [a for a in new_items if a and a.get('assignmentId')]
    if not new_items:
        return jsonify({'error': 'assignmentId required'}), 400
    try:
        with practice_write_lock:
            items = _load_json_list(PRACTICE_ASSIGN_FILE)
            existing_ids = {str(a.get('assignmentId')) for a in items}
            batch_ids = set()
            for assignment in new_items:
                assignment_id = str(assignment.get('assignmentId'))
                if assignment_id in existing_ids or assignment_id in batch_ids:
                    return jsonify({'error': 'assignmentId already exists'}), 409
                link_assignment_version(assignment)
                batch_ids.add(assignment_id)
            items.extend(new_items)
            _save_json_list(PRACTICE_ASSIGN_FILE, items)
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'ok': True, 'count': len(new_items), 'total': len(items)})

@app.route('/api/practice/submissions', methods=['GET'])
@require_auth
def practice_submissions_get(user):
    """Students can only read their own submissions."""
    items = _load_json_list(PRACTICE_SUBMIT_FILE)
    sid = request.args.get('student', '').strip()
    if not _teacher_write_allowed(user):
        own_id = str(user.get('username') or '').strip()
        if sid and sid.casefold() != own_id.casefold():
            return jsonify({'error': 'Student access denied'}), 403
        sid = own_id
    if sid:
        sl = sid.casefold()
        items = [s for s in items if str(s.get('studentId') or '').casefold() == sl]
    return jsonify(items)

@app.route('/api/practice/submissions', methods=['POST'])
@require_auth
def practice_submissions_post(user):
    """Create a student-owned submission linked to the distributed versions."""
    data = request.get_json(silent=True) or {}
    if not data.get('submissionId'):
        return jsonify({'error': 'submissionId required'}), 400
    student_id = str(data.get('studentId') or '').strip()
    if not student_id or student_id.casefold() != str(user.get('username') or '').casefold():
        return jsonify({'error': 'Student identity mismatch'}), 403
    try:
        with practice_write_lock:
            assignments = _load_json_list(PRACTICE_ASSIGN_FILE)
            assignment = next((a for a in assignments if a.get('assignmentId') == data.get('assignmentId')), None)
            if not assignment or assignment.get('status') == 'withdrawn':
                return jsonify({'error': 'Active assignment not found'}), 404
            targets = assignment.get('studentIds') or assignment.get('targetIds') or []
            if student_id.casefold() not in {str(x).casefold() for x in targets}:
                return jsonify({'error': 'Assignment is not assigned to this student'}), 403
            submissions = _load_json_list(PRACTICE_SUBMIT_FILE)
            if any(s.get('submissionId') == data.get('submissionId') for s in submissions):
                return jsonify({'error': 'submissionId already exists'}), 409
            if any(s.get('assignmentId') == assignment.get('assignmentId') and str(s.get('studentId') or '').casefold() == student_id.casefold() for s in submissions):
                return jsonify({'error': 'Assignment already submitted'}), 409
            link_submission_version(data, assignment)
            data['practiceId'] = assignment.get('practiceId', '')
            submissions.append(data)
            _save_json_list(PRACTICE_SUBMIT_FILE, submissions)
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'ok': True, 'count': 1, 'total': len(submissions)})

@app.route('/api/practice/submissions/<submission_id>', methods=['PUT'])
@require_auth
def practice_submission_update(submission_id, user):
    """Update a submission's teacher review without changing its version links."""
    if not _teacher_write_allowed(user):
        return jsonify({'error': 'Teacher access required'}), 403
    data = request.get_json(silent=True) or {}
    with practice_write_lock:
        items = _load_json_list(PRACTICE_SUBMIT_FILE)
        found = next((s for s in items if s.get('submissionId') == submission_id), None)
        if found is None:
            return jsonify({'error': 'submission not found'}), 404
        for key in ('status', 'teacherFeedback', 'score', 'checkedAt'):
            if key in data:
                found[key] = data[key]
        _save_json_list(PRACTICE_SUBMIT_FILE, items)
    return jsonify({'ok': True})

@app.route('/api/practice/assignments/<assignment_id>', methods=['DELETE'])
@require_auth
def practice_assignment_delete(assignment_id, user):
    """Withdraw a distribution while preserving its snapshot and submissions."""
    if not _teacher_write_allowed(user):
        return jsonify({'error': 'Teacher access required'}), 403
    with practice_write_lock:
        items = _load_json_list(PRACTICE_ASSIGN_FILE)
        found = next((a for a in items if a.get('assignmentId') == assignment_id), None)
        if found is None:
            return jsonify({'error': 'assignment not found'}), 404
        found['status'] = 'withdrawn'
        found['withdrawnAt'] = datetime.utcnow().isoformat() + 'Z'
        _save_json_list(PRACTICE_ASSIGN_FILE, items)
    return jsonify({'ok': True, 'archived': True, 'removedSubmissions': 0})
'''


def patched_source(source: str) -> str:
    import_line = "from hours_ledger import ensure_profile_ids, recompute_balance\n"
    if source.count(import_line) != 1:
        raise ValueError("could not uniquely locate the existing hours-ledger import")
    if "from practice_versioning import link_assignment_version, link_submission_version" in source:
        raise ValueError("practice API already imports the version helper")
    source = source.replace(
        import_line,
        import_line + "from practice_versioning import link_assignment_version, link_submission_version\n",
        1,
    )
    start = source.index("PRACTICE_ASSIGN_FILE = os.path.join(DATA_DIR, 'practice-assignments.json')")
    end_marker = "\nif __name__ == '__main__':"
    end = source.index(end_marker, start)
    return source[:start] + PRACTICE_ROUTES + source[end:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app_file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    old = args.app_file.read_text(encoding="utf-8")
    new = patched_source(old)
    if not args.apply:
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="live/backend/app.py", tofile="updated/backend/app.py", lineterm="")
        print("\n".join(diff))
        print("Dry run only; no files changed.")
        return 0
    args.app_file.write_text(new, encoding="utf-8")
    print("Applied the scoped practice API update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
