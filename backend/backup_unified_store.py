"""Online consistent SQLite backup with integrity verification and bounded retention."""
import datetime
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def backup(database, destination):
    os.umask(0o077)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = 'core-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3'
    path = destination / name
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup integrity failure')
    os.chmod(path, 0o600)
    for old in sorted(destination.glob('core-*.sqlite3'), reverse=True)[28:]:
        old.unlink()
    return path


if __name__ == '__main__':
    database = Path(os.environ.get('EMILY_STORAGE_DB', '/var/lib/emily-store/core.sqlite3'))
    backup(database, database.parent / 'backups')
    print('Unified storage backup verified')
