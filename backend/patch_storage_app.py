"""Patch a verified live app copy without including secrets or replacing other logic."""
import argparse
import ast
from pathlib import Path

HELPERS = '''
# Operational storage is enabled only by an explicit service setting.
import sqlite3
from operational_store import OperationalStore
operational_store = OperationalStore(os.environ['EMILY_STORAGE_DB'], BASE_DIR) if os.environ.get('EMILY_STORAGE_DB') else None

def _read_document(path):
    if operational_store is not None and operational_store.manages(path):
        return operational_store.read(path)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _document_exists(path):
    if operational_store is not None and operational_store.manages(path):
        return operational_store.exists(path)
    return os.path.exists(path)

@app.before_request
def begin_storage_request():
    if operational_store is not None:
        mutation = request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and (
            request.path.startswith(('/api/students/', '/api/student-accounts', '/api/practice/', '/api/admin/keys'))
            or request.path in ('/api/leads', '/api/activate'))
        operational_store.begin(write=mutation)

@app.after_request
def finish_storage_request(response):
    if operational_store is not None:
        operational_store.finish(success=response.status_code < 400)
    return response

@app.teardown_request
def rollback_storage_request(error=None):
    if operational_store is not None:
        operational_store.finish(success=False)

@app.errorhandler(sqlite3.DatabaseError)
def storage_error(error):
    return jsonify({'error': '数据存储暂不可用，请刷新后重试'}), 503

# Nginx retains the existing cookie gate and no-cache headers for these URLs.
@app.route('/<filename>.json')
@app.route('/data/<path:filename>')
def storage_json_document(filename):
    if not request.cookies.get('authToken'):
        return jsonify({'error': 'Login required'}), 401
    relative = ('data/' + filename) if request.path.startswith('/data/') else filename + '.json'
    if '..' in relative or not relative.endswith('.json'):
        return jsonify({'error': 'Not found'}), 404
    path = os.path.join(BASE_DIR, relative)
    if not _document_exists(path):
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_read_document(path))

def _delete_payment_from_store(user, student_id):
    student = find_student(student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    body = request.get_json(silent=True) or {}
    index = body.get('index')
    if type(index) is not int:
        return jsonify({'error': 'Invalid payment index'}), 400
    path = os.path.join(BASE_DIR, get_data_file(student) + '.json')
    data = _read_document(path)
    payments = data.get('payments') or []
    if index < 0 or index >= len(payments) or payments[index] != body.get('expected'):
        return jsonify({'error': '记录已变化，请刷新后重试'}), 409
    payments.pop(index)
    data['payments'] = payments
    recompute_balance(data, empty_payments='zero')
    # The store archives the exact previous revision in the same transaction.
    _write_json_file(path, data)
    return jsonify({'success': True})

'''


def patch(source):
    if b'def begin_storage_request' in source:
        raise ValueError('App already patched')
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def span(node):
        return offsets[node.lineno - 1] + node.col_offset, offsets[node.end_lineno - 1] + node.end_col_offset

    edits = []
    def replace(node, text):
        start, end = span(node)
        edits.append((start, end, text.encode('utf-8')))

    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    fixed = {'load_students': 'def load_students():\n    return _read_document(STUDENTS_FILE)',
             '_load_json_list': "def _load_json_list(path):\n    try:\n        d = _read_document(path)\n        return d if isinstance(d, list) else []\n    except FileNotFoundError:\n        return []",
             '_save_json_list': 'def _save_json_list(path, items):\n    _write_json_file(path, items)'}
    for name, text in fixed.items():
        replace(functions[name], text)
    writer = functions['_write_json_file']
    pos = span(writer.body[0])[0]
    edits.append((pos, pos, b'if operational_store is not None and operational_store.manages(path):\n        operational_store.write(path, value)\n        return\n    '))
    payment = functions['delete_payment']
    first_import = next(n for n in payment.body if isinstance(n, ast.Import))
    pos = span(first_import)[0]
    edits.append((pos, pos, b'if operational_store is not None:\n        return _delete_payment_from_store(user, student_id)\n    '))
    leave = functions['teacher_create_leave']
    lock = next(n for n in leave.body if isinstance(n, ast.With))
    first = next(n for n in lock.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'stat')
    start = span(first)[0]
    end = span(lock.body[-1])[1]
    edits.append((start, end, b'ensure_profile_ids(student_id, data)\n        _write_json_file(student_file, data)'))
    excluded = set(fixed) | {'delete_payment', '_write_json_file'}
    paths = {'student_file', 'data_path', 'quiz_file', 'LEADS_FILE', 'KEYS_FILE'}
    converted = 0
    for fn in functions.values():
        if fn.name in excluded:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == 'os.path.exists' and node.args and ast.unparse(node.args[0]) in paths:
                replace(node, '_document_exists(' + ast.unparse(node.args[0]) + ')')
            if not isinstance(node, ast.With) or len(node.items) != 1 or len(node.body) != 1:
                continue
            ctx = node.items[0].context_expr
            if not isinstance(ctx, ast.Call) or ast.unparse(ctx.func) != 'open' or not ctx.args:
                continue
            path = ast.unparse(ctx.args[0])
            allowed = path in paths or (fn.name == '_load_vocab_index' and path == 'path') or (fn.name == 'source_aware_vocab_card' and 'BASE_DIR' in path)
            # The course metadata read has a nested json.load(...).get(...).
            if 'BASE_DIR' in path and fn.name != 'publish_vocab_cards':
                allowed = True
            if not allowed:
                continue
            stmt = node.body[0]
            text = ast.unparse(stmt)
            file_var = ast.unparse(node.items[0].optional_vars)
            load = 'json.load(' + file_var + ')'
            if load in text:
                replace(node, text.replace(load, '_read_document(' + path + ')'))
                converted += 1
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and ast.unparse(stmt.value.func) == 'json.dump':
                replace(node, '_write_json_file(' + path + ', ' + ast.unparse(stmt.value.args[0]) + ')')
                converted += 1
    # Require the known live operational load/write blocks rather than silently skipping them.
    if converted < 16:
        raise ValueError('Unexpected operational I/O shape: ' + str(converted))
    anchor = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'LEADS_FILE' for t in n.targets))
    pos = span(anchor)[1]
    edits.append((pos, pos, ('\n' + HELPERS).encode('utf-8')))
    # Reject overlapping structural replacements.
    ordered = sorted(edits, key=lambda x: (x[0], x[1]))
    for left, right in zip(ordered, ordered[1:]):
        if left[1] > right[0]:
            raise ValueError('Overlapping app edits')
    for start, end, text in reversed(ordered):
        source = source[:start] + text + source[end:]
    ast.parse(source)
    return source


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('destination', type=Path)
    args = p.parse_args()
    args.destination.write_bytes(patch(args.source.read_bytes()))
