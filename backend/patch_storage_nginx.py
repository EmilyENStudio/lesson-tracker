"""Change only the two already-protected JSON locations to read the database adapter."""
import argparse
from pathlib import Path


def patch(source):
    for marker in ('location ~ ^/[A-Za-z0-9_-]+\\.json$ {', 'location ~ ^/data/.+\\.json$ {'):
        start = source.index(marker)
        first = source.index('{', start)
        depth = 0
        for end in range(first, len(source)):
            depth += int(source[end] == '{') - int(source[end] == '}')
            if depth == 0:
                break
        block = source[start:end + 1]
        if block.count('try_files $uri =404;') != 1:
            raise ValueError('Unexpected JSON routing block')
        updated = block.replace('try_files $uri =404;', 'proxy_pass http://127.0.0.1:5000;\n    proxy_set_header Cookie $http_cookie;\n    proxy_set_header Authorization $http_authorization;')
        source = source[:start] + updated + source[end + 1:]
    return source


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('destination', type=Path)
    a = p.parse_args()
    a.destination.write_bytes(patch(a.source.read_bytes().decode('utf-8')).encode('utf-8'))
