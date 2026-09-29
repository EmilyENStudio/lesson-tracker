"""Read-only audit of the current unified database, with no learner content output."""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from hours_ledger import audit_profile
from audit_practice_chain import audit_chain


def audit(database):
    issues = Counter()
    profiles = 0
    collections = Counter()
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        documents = {k: json.loads(p) for k, p in db.execute('SELECT key,payload FROM storage_documents')}
        for key, payload, digest in db.execute('SELECT key,payload,digest FROM storage_documents'):
            issues['document_digest'] += int(hashlib.sha256(payload.encode()).hexdigest() != digest)
        for student in documents['students.json']:
            if not student.get('dataFile'):
                continue
            profile = documents[str(student['dataFile']) + '.json']
            result = audit_profile(profile)
            profiles += 1
            collections.update(result['counts'])
            issues.update(result['issues'])
        practice = audit_chain(*(documents['data/' + name + '.json'] for name in (
            'practice-catalog', 'practice-assignments', 'practice-submissions')))
        issues.update(practice['issues'])
        issues['foreign_key'] += len(list(db.execute('PRAGMA foreign_key_check')))
        issues['integrity'] += int(db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok')
        for table, field in (('states', 'data'), ('feedback', 'data'), ('invitations', 'data'),
                             ('invitation_replies', 'data'), ('resources', 'task'), ('revisions', 'task')):
            for row in db.execute('SELECT "' + field + '" FROM "' + table + '"'):
                try:
                    json.loads(row[0])
                except (TypeError, ValueError):
                    issues['sqlite_json'] += 1
        issues['missing_resource_revision'] += db.execute('SELECT COUNT(*) FROM resources r WHERE NOT EXISTS '
             '(SELECT 1 FROM revisions v WHERE v.id=r.id AND v.version=r.version)').fetchone()[0]
        issues['orphan_invitation_reply'] += db.execute('SELECT COUNT(*) FROM invitation_replies r WHERE NOT EXISTS '
             '(SELECT 1 FROM invitations i WHERE i.id=r.invitation_id)').fetchone()[0]
        counts = {t: db.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0] for t in (
            'states', 'feedback', 'audit', 'invitations', 'invitation_replies', 'resources', 'revisions', 'storage_history')}
    issues = {k: n for k, n in issues.items() if n}
    return {'documents': len(documents), 'profiles': profiles, 'collections': dict(collections),
            'practice': practice['counts'], 'sqliteTables': counts, 'issues': issues, 'ok': not issues}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.database)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['ok'] else 1)
