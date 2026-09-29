"""Restore a verified CURRENT rollback export while all business services are stopped."""
import argparse
import os
import shutil
import tempfile
from pathlib import Path


def replace(source, target, mode=None, uid=None, gid=None):
    if target.exists():
        st = target.stat()
        mode, uid, gid = st.st_mode & 0o7777, st.st_uid, st.st_gid
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.restore-store-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode if mode is not None else 0o644)
        if uid is not None:
            os.chown(temporary, uid, gid)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--export', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--followup', type=Path, required=True)
    p.add_argument('--resources', type=Path, required=True)
    a = p.parse_args()
    for source in (a.export / 'site').rglob('*.json'):
        dest = (a.root / source.relative_to(a.export / 'site')).resolve()
        if not dest.is_relative_to(a.root.resolve()):
            raise ValueError('Unsafe restore destination')
        replace(source, dest)
    replace(a.export / 'followup.sqlite3', a.followup)
    replace(a.export / 'resources.sqlite3', a.resources)
    print('Current-state legacy export restored')
