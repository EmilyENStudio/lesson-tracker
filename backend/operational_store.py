"""Transactional operational documents; static books/media remain filesystem assets."""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

FIXED_DOCUMENTS = (
    'students.json', 'course_schedule.json', 'wordcards_vocab.json',
    'data/practice-catalog.json', 'data/practice-assignments.json',
    'data/practice-submissions.json', 'data/activation_keys.json',
    'data/leads.json', 'data/fox-quiz.json', 'data/frog-quiz.json',
)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS storage_documents(
  key TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)),
  revision INTEGER NOT NULL CHECK(revision>0), digest TEXT NOT NULL,
  updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS storage_history(
  key TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
  digest TEXT NOT NULL, archived TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(key,revision));
CREATE TABLE IF NOT EXISTS storage_ids(
  document_key TEXT NOT NULL REFERENCES storage_documents(key) ON DELETE CASCADE,
  collection TEXT NOT NULL, id TEXT NOT NULL, position INTEGER NOT NULL,
  PRIMARY KEY(document_key,collection,id));
CREATE TABLE IF NOT EXISTS storage_roster(
  student_id TEXT PRIMARY KEY COLLATE NOCASE,
  profile_key TEXT UNIQUE REFERENCES storage_documents(key) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE IF NOT EXISTS storage_assignments(
  id TEXT PRIMARY KEY, version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS storage_submissions(
  id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL
    REFERENCES storage_assignments(id) DEFERRABLE INITIALLY DEFERRED,
  student_id TEXT NOT NULL COLLATE NOCASE,
  UNIQUE(assignment_id,student_id));
'''


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class OperationalStore:
    def __init__(self, database, root):
        self.database = str(database)
        self.root = Path(root).resolve()
        self.local = threading.local()

    def connect(self, readonly=False):
        uri = Path(self.database).resolve().as_uri() + ('?mode=ro' if readonly else '?mode=rw')
        db = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        if readonly:
            db.execute('PRAGMA query_only=ON')
        return db

    def begin(self, write=False):
        if getattr(self.local, 'connection', None) is not None:
            raise RuntimeError('Storage request already open')
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        except Exception:
            db.close()
            raise
        self.local.connection = db

    def finish(self, success=False):
        db = getattr(self.local, 'connection', None)
        if db is None:
            return
        self.local.connection = None
        try:
            db.execute('COMMIT' if success else 'ROLLBACK')
        finally:
            db.close()

    def key(self, path):
        return Path(path).resolve().relative_to(self.root).as_posix()

    def _connection(self):
        current = getattr(self.local, 'connection', None)
        return current or self.connect(), current is None

    def manages(self, path):
        try:
            key = self.key(path)
        except ValueError:
            return False
        if key in FIXED_DOCUMENTS:
            return True
        db, close = self._connection()
        try:
            return db.execute('SELECT 1 FROM storage_documents WHERE key=?', (key,)).fetchone() is not None or db.execute(
                'SELECT 1 FROM storage_roster WHERE profile_key=?', (key,)).fetchone() is not None
        finally:
            if close:
                db.close()

    def exists(self, path):
        db, close = self._connection()
        try:
            return db.execute('SELECT 1 FROM storage_documents WHERE key=?', (self.key(path),)).fetchone() is not None
        finally:
            if close:
                db.close()

    def read(self, path):
        db, close = self._connection()
        try:
            row = db.execute('SELECT payload FROM storage_documents WHERE key=?', (self.key(path),)).fetchone()
            if row is None:
                raise FileNotFoundError(str(path))
            return json.loads(row[0])
        finally:
            if close:
                db.close()

    def write(self, path, value):
        db, close = self._connection()
        try:
            if close:
                db.execute('BEGIN IMMEDIATE')
            self.put(db, self.key(path), value)
            if close:
                db.execute('COMMIT')
        except Exception:
            if close and db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            if close:
                db.close()

    @staticmethod
    def put(db, key, value):
        payload = encode(value)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        old = db.execute('SELECT revision,payload,digest FROM storage_documents WHERE key=?', (key,)).fetchone()
        if old and old[2] == digest:
            return
        if old:
            db.execute('INSERT INTO storage_history(key,revision,payload,digest) VALUES(?,?,?,?)',
                       (key, old[0], old[1], old[2]))
        revision = old[0] + 1 if old else 1
        db.execute('INSERT INTO storage_documents(key,payload,revision,digest) VALUES(?,?,?,?) '
                   'ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, revision=excluded.revision, '
                   'digest=excluded.digest,updated=CURRENT_TIMESTAMP', (key, payload, revision, digest))
        db.execute('DELETE FROM storage_ids WHERE document_key=?', (key,))
        if key == 'students.json':
            db.execute('DELETE FROM storage_roster')
            for item in value:
                student_id = str(item['id'])
                stem = str(item.get('dataFile') or student_id)
                if Path(stem).name != stem or '/' in stem or '\\' in stem:
                    raise ValueError('Profile file must be a plain filename')
                profile = stem + '.json' if item.get('dataFile') else None
                db.execute('INSERT INTO storage_roster(student_id,profile_key) VALUES(?,?)', (student_id, profile))
        elif key == 'data/practice-assignments.json':
            db.execute('DELETE FROM storage_assignments')
            for item in value:
                db.execute('INSERT INTO storage_assignments(id,version) VALUES(?,?)',
                           (item['assignmentId'], item['assignmentVersionId']))
        elif key == 'data/practice-submissions.json':
            db.execute('DELETE FROM storage_submissions')
            for item in value:
                db.execute('INSERT INTO storage_submissions(id,assignment_id,student_id) VALUES(?,?,?)',
                           (item['submissionId'], item['assignmentId'], item['studentId']))
        collections = {}
        if isinstance(value, dict):
            collections = {k: value.get(k, []) for k in ('payments', 'records', 'leaveRequests', 'oneTimeClasses')}
        elif key == 'data/practice-catalog.json':
            collections = {'tasks': value}
        for collection, items in collections.items():
            if isinstance(items, dict):
                items = [items]
            for position, item in enumerate(items or []):
                if not isinstance(item, dict) or not item.get('id'):
                    raise ValueError('Missing stable record ID: ' + collection)
                db.execute('INSERT INTO storage_ids(document_key,collection,id,position) VALUES(?,?,?,?)',
                           (key, collection, str(item['id']), position))


def initialize(database):
    db = sqlite3.connect(str(database), isolation_level=None)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(SCHEMA)
    finally:
        db.close()
