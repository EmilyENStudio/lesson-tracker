"""Explicit, lossless migration into a NEW database; never modifies source storage."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from operational_store import FIXED_DOCUMENTS, OperationalStore, initialize, encode


def documents(root):
    roster = json.loads((root / 'students.json').read_text(encoding='utf-8'))
    keys = list(FIXED_DOCUMENTS)
    keys += [str(s['dataFile']) + '.json' for s in roster if s.get('dataFile')]
    return {key: json.loads((root / key).read_text(encoding='utf-8')) for key in keys if (root / key).is_file()}


def table_rows(db, table):
    return db.execute('SELECT rowid,* FROM "' + table + '" ORDER BY rowid').fetchall()


def copy_database(target, source):
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        tables = list(src.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))
        for name, sql in tables:
            if target.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone():
                raise ValueError('Source table name collision: ' + name)
            target.execute(sql)
            rows = table_rows(src, name)
            if rows:
                columns = ['rowid'] + [r[1] for r in src.execute('PRAGMA table_info("' + name + '")')]
                target.executemany('INSERT INTO "' + name + '" (' + ','.join('"' + c + '"' for c in columns) + ') VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
        for (sql,) in src.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
            target.execute(sql)


def verify(root, database, source_dbs):
    source = documents(root)
    issues = []
    counts = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        current = {k: json.loads(p) for k, p in db.execute('SELECT key,payload FROM storage_documents')}
        if source != current:
            issues.append('document_semantic_mismatch')
        for key, payload, digest in db.execute('SELECT key,payload,digest FROM storage_documents'):
            if hashlib.sha256(payload.encode()).hexdigest() != digest:
                issues.append('document_digest_mismatch')
        for src_path in source_dbs:
            with closing(sqlite3.connect(src_path.resolve().as_uri() + '?mode=ro', uri=True)) as src:
                for (table,) in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    rows = table_rows(src, table)
                    counts[table] = len(rows)
                    if rows != table_rows(db, table):
                        issues.append('sqlite_table_mismatch:' + table)
        if list(db.execute('PRAGMA foreign_key_check')):
            issues.append('foreign_key_failure')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            issues.append('integrity_failure')
    return {'documents': len(source), 'sqliteTables': counts, 'issues': issues, 'ok': not issues}


def migrate(root, database, source_dbs):
    if database.exists():
        raise ValueError('Destination exists; refusing to replace it')
    database.parent.mkdir(parents=True, exist_ok=True)
    initialize(database)
    store = OperationalStore(database, root)
    source = documents(root)
    db = store.connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        for path in source_dbs:
            copy_database(db, path)
        ordered = sorted(source, key=lambda k: (k == 'data/practice-submissions.json', k))
        for key in ordered:
            store.put(db, key, source[key])
        db.execute('COMMIT')
    except Exception:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()
    report = verify(root, database, source_dbs)
    if not report['ok']:
        raise ValueError('Post-migration comparison failed')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--source-db', type=Path, action='append', default=[])
    p.add_argument('--verify', action='store_true')
    p.add_argument('--export', type=Path)
    args = p.parse_args()
    if args.export:
        # Export current DB documents for a rollback, not a stale pre-switch backup.
        args.export.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for key, payload in db.execute('SELECT key,payload FROM storage_documents'):
                dest = (args.export / key).resolve()
                if not dest.is_relative_to(args.export.resolve()):
                    raise ValueError('Unsafe document path')
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(json.loads(payload), ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'exported': True}))
        return
    report = verify(args.root, args.database, args.source_db) if args.verify else migrate(args.root, args.database, args.source_db)
    print(json.dumps(report, sort_keys=True))
    if not report['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
