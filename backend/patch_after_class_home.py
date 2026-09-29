#!/usr/bin/env python3
"""Guarded, idempotent loader patch preserving newer runtime homepage changes."""
import argparse
import hashlib
import os
import re
import tempfile
from pathlib import Path

LOADER = '<script src="/after-class.js?v=20260928-1"></script>'


def patch_home(html):
    pattern = r'<script\b[^>]*\bsrc=[\x22\x27]/?after-class\.js(?:\?[^\x22\x27]*)?[\x22\x27][^>]*>\s*</script>'
    matches = list(re.finditer(pattern, html, re.I))
    if len(matches) > 1:
        raise ValueError("multiple after-class loaders; inspect the live file")
    if matches:
        return re.sub(pattern, lambda _: LOADER, html, count=1, flags=re.I)
    if len(re.findall(r'</body\s*>', html, re.I)) != 1:
        raise ValueError("expected one closing body tag")
    newline = '\r\n' if '\r\n' in html else '\n'
    return re.sub(r'</body\s*>', lambda m: LOADER + newline + m.group(), html, count=1, flags=re.I)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    original = args.path.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    updated = patch_home(original.decode('utf-8')).encode('utf-8')
    if not args.apply:
        print("Already current" if updated == original else "Loader patch required", before)
        return
    if args.expected_sha256 != before:
        parser.error("--apply requires the inspected live file's --expected-sha256")
    if updated == original:
        print("Already current; no write")
        return
    info = args.path.stat()
    fd, temporary = tempfile.mkstemp(dir=args.path.parent, prefix=args.path.name + '.')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, info.st_mode & 0o777)
        if hasattr(os, 'chown'):
            os.chown(temporary, info.st_uid, info.st_gid)
        if args.path.read_bytes() != original:
            raise ValueError("homepage changed concurrently; reload before applying")
        os.replace(temporary, args.path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Applied loader only", hashlib.sha256(updated).hexdigest())


if __name__ == '__main__':
    main()
