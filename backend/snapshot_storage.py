"""Create a private exact-file/consistent-SQLite snapshot and record source hashes."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from operational_store import FIXED_DOCUMENTS


def snapshot(root, destination, db_paths):
    os.umask(0o077)
    if destination.exists():
        raise ValueError('Snapshot destination exists')
    destination.mkdir(parents=True, mode=0o700)
    roster = json.loads((root / 'students.json').read_text(encoding='utf-8'))
    keys = list(FIXED_DOCUMENTS) + [str(s['dataFile']) + '.json' for s in roster if s.get('dataFile')]
    manifest = {}
    for key in keys:
        path = root / key
        if not path.is_file():
            continue
        data = path.read_bytes()
        st = path.stat()
        target = destination / 'site' / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        digest = hashlib.sha256(data).hexdigest()
        if digest != hashlib.sha256(target.read_bytes()).hexdigest():
            raise ValueError('File changed while snapshotting')
        manifest[key] = {'sha256': digest, 'mode': st.st_mode & 0o7777, 'uid': st.st_uid, 'gid': st.st_gid}
    for path in db_paths:
        target = destination / path.name
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity failure')
        st = path.stat()
        manifest[str(path)] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'mode': st.st_mode & 0o7777, 'uid': st.st_uid, 'gid': st.st_gid}
    for key in keys:
        if key in manifest and hashlib.sha256((root / key).read_bytes()).hexdigest() != manifest[key]['sha256']:
            raise ValueError('JSON changed during snapshot; repeat with a new destination')
    (destination / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
    return {'jsonFiles': sum(not k.startswith('/') for k in manifest), 'sqliteSnapshots': len(db_paths), 'ok': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--source-db', type=Path, action='append', default=[])
    a = p.parse_args()
    print(json.dumps(snapshot(a.root, a.destination, a.source_db), sort_keys=True))
