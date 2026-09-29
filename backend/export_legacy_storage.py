"""Export the CURRENT unified store to private rollback material, including new writes."""
import argparse
import json
import os
import sqlite3
from pathlib import Path
from contextlib import closing

GROUPS = {
    'followup.sqlite3': ('states', 'contacts', 'feedback', 'audit', 'invitations', 'invitation_replies'),
    'resources.sqlite3': ('resources', 'revisions'),
}


def export(database, destination):
    os.umask(0o077)
    if destination.exists():
        raise ValueError('Rollback export directory already exists')
    destination.mkdir(parents=True, mode=0o700)
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        source.execute('BEGIN')
        for key, payload in source.execute('SELECT key,payload FROM storage_documents'):
            target = (destination / 'site' / key).resolve()
            if not target.is_relative_to((destination / 'site').resolve()):
                raise ValueError('Unsafe document path')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(json.loads(payload), ensure_ascii=False, indent=2), encoding='utf-8')
        for filename, tables in GROUPS.items():
            with closing(sqlite3.connect(destination / filename)) as target:
                for table in tables:
                    schema = source.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone()
                    if schema is None:
                        raise ValueError('Missing source table')
                    target.execute(schema[0])
                    rows = source.execute('SELECT rowid,* FROM "' + table + '" ORDER BY rowid').fetchall()
                    if rows:
                        columns = ['rowid'] + [r[1] for r in source.execute('PRAGMA table_info("' + table + '")')]
                        target.executemany('INSERT INTO "' + table + '" (' + ','.join('"' + c + '"' for c in columns) + ') VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
                    for (sql,) in source.execute('SELECT sql FROM sqlite_master WHERE type=? AND tbl_name=? AND sql IS NOT NULL', ('index', table)):
                        target.execute(sql)
                target.commit()
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Rollback export integrity failure')
        source.rollback()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    export(a.database, a.destination)
    print(json.dumps({'ok': True}))
