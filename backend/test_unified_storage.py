import datetime
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from operational_store import OperationalStore, initialize
from migrate_operational_store import migrate, verify


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'site'
        self.root.mkdir()
        (self.root / 'data').mkdir()
        self.database = Path(self.tmp.name) / 'core.sqlite3'
        self.profile = {'name': '测试', 'username': 'Learner', 'courseName': 'Reading',
                        'activeCourses': ['Reading'], 'payments': [{'id': 'payment1', 'hours': 5}],
                        'records': [{'id': 'record1', 'date': '2026-09-29', 'time': '09:00', 'courseName': 'Reading', 'hours': 1.5}],
                        'oneTimeClasses': [], 'leaveRequests': [], 'totalHours': 5,
                        'usedHours': 1, 'remainingHours': 4}
        self.write('students.json', [{'id': 'Learner', 'name': '测试', 'dataFile': 'learner'}])
        self.write('learner.json', self.profile)
        self.write('data/practice-catalog.json', [])
        self.write('data/practice-assignments.json', [])
        self.write('data/practice-submissions.json', [])
        self.write('course_schedule.json', {'Reading': {'duration': 60}})
        self.source_db = Path(self.tmp.name) / 'old.sqlite3'
        with closing(sqlite3.connect(self.source_db)) as db:
            db.executescript('CREATE TABLE states(id TEXT PRIMARY KEY,version INTEGER NOT NULL,data TEXT NOT NULL);'
                             'CREATE TABLE audit(id INTEGER PRIMARY KEY,action TEXT);')
            db.execute('INSERT INTO states VALUES(?,?,?)', ('row1', 3, '{"sent":true}'))
            db.execute('INSERT INTO audit VALUES(?,?)', (163, 'sent'))
            db.commit()
        self.report = migrate(self.root, self.database, [self.source_db])
        self.store = OperationalStore(self.database, self.root)

    def tearDown(self):
        self.store.finish(False)
        self.tmp.cleanup()

    def write(self, key, value):
        (self.root / key).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def test_migration_preserves_all_values_and_sqlite_rows(self):
        self.assertTrue(self.report['ok'])
        self.assertEqual(self.profile, self.store.read(self.root / 'learner.json'))
        self.assertTrue(verify(self.root, self.database, [self.source_db])['ok'])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT id FROM audit').fetchone()[0], 163)

    def test_account_creation_is_atomic_and_failed_creation_leaves_no_roster_entry(self):
        roster = self.store.read(self.root / 'students.json')
        roster.append({'id': 'New', 'dataFile': 'new', 'name': '新学员'})
        self.store.begin(True)
        self.store.write(self.root / 'students.json', roster)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finish(True)
        self.assertEqual(len(self.store.read(self.root / 'students.json')), 1)
        self.store.begin(True)
        self.store.write(self.root / 'students.json', roster)
        self.store.write(self.root / 'new.json', dict(self.profile, name='新学员'))
        self.store.finish(True)
        self.assertTrue(self.store.exists(self.root / 'new.json'))

    def test_duplicate_record_ids_roll_back_payload_and_indexes(self):
        value = dict(self.profile, records=[self.profile['records'][0]] * 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.write(self.root / 'learner.json', value)
        self.assertEqual(self.profile, self.store.read(self.root / 'learner.json'))

    def test_submission_cannot_reference_missing_distribution(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.write(self.root / 'data/practice-submissions.json',
                             [{'submissionId': 's', 'assignmentId': 'missing', 'studentId': 'Learner'}])
        self.assertEqual([], self.store.read(self.root / 'data/practice-submissions.json'))

    def test_crashed_uncommitted_write_is_not_persisted(self):
        code = """import os,sys
from operational_store import OperationalStore
s=OperationalStore(sys.argv[1],sys.argv[2]);s.begin(True)
s.write(sys.argv[2]+'/learner.json',{'name':'uncommitted'})
os._exit(23)
"""
        result = subprocess.run([sys.executable, '-c', code, str(self.database), str(self.root)])
        self.assertEqual(result.returncode, 23)
        self.assertEqual(self.profile, self.store.read(self.root / 'learner.json'))

    def test_competing_process_writes_are_serialized(self):
        self.store.write(self.root / 'data/leads.json', [])
        code = """import sys
from operational_store import OperationalStore
s=OperationalStore(sys.argv[1],sys.argv[2])
for n in range(8):
 s.begin(True);v=s.read(sys.argv[2]+'/data/leads.json');v.append(sys.argv[3]);s.write(sys.argv[2]+'/data/leads.json',v);s.finish(True)
"""
        jobs = [subprocess.Popen([sys.executable, '-c', code, str(self.database), str(self.root), str(n)]) for n in range(3)]
        self.assertEqual([p.wait(timeout=20) for p in jobs], [0, 0, 0])
        self.assertEqual(len(self.store.read(self.root / 'data/leads.json')), 24)

    def test_backup_restores_latest_data_and_preserves_previous_revision(self):
        value = dict(self.profile, roadmapOverride={'dahl': 'ongoing'})
        self.store.write(self.root / 'learner.json', value)
        backup = Path(self.tmp.name) / 'restored.sqlite3'
        with closing(sqlite3.connect(self.database)) as src, closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
        restored = OperationalStore(backup, self.root)
        self.assertEqual(restored.read(self.root / 'learner.json'), value)
        with closing(sqlite3.connect(backup)) as db:
            old = json.loads(db.execute("SELECT payload FROM storage_history WHERE key='learner.json'").fetchone()[0])
            self.assertEqual(old, self.profile)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    @unittest.skipUnless(os.environ.get('STORAGE_TEST_FOLLOWUP'), 'service source copies required')
    def test_existing_followup_and_resource_writes_share_database(self):
        sys.path.insert(0, str(Path(os.environ['STORAGE_TEST_FOLLOWUP']).parent))
        followup = load_module('storage_test_followup', os.environ['STORAGE_TEST_FOLLOWUP'])
        resource = load_module('storage_test_resource', os.environ['STORAGE_TEST_RESOURCE'])
        fresh = Path(self.tmp.name) / 'service-core.sqlite3'
        initialize(fresh)
        tracker = followup.Store(fresh)
        library = resource.Store(fresh)
        tracker.save({'id': 'fixture-session', 'stateVersion': 0}, {'sent': True}, 'fixture-teacher', 'sent')
        self.assertEqual(tracker.snapshot()[0]['fixture-session']['version'], 1)
        with self.assertRaises(followup.Problem):
            tracker.save({'id': 'fixture-session', 'stateVersion': 0}, {'sent': False}, 'fixture-teacher', 'sent')
        task = {'title': 'Fixture', 'book': 'Fixture', 'lesson': 'Fixture', 'studentRenderer': 'worksheet',
                'fields': [{'id': 'field-1', 'label': 'Write one sentence.'}]}
        rid = 'custom-00000000-0000-0000-0000-000000000001'
        self.assertEqual(library.save({'id': rid, 'task': task}, 'fixture-teacher')['resourceVersion'], 1)
        task['title'] = 'Updated fixture'
        self.assertEqual(library.save({'id': rid, 'version': 1, 'task': task}, 'fixture-teacher', True)['resourceVersion'], 2)
        self.assertEqual(tracker.snapshot()[0]['fixture-session']['data'], {'sent': True})
        self.assertEqual(len(library.list()), 1)
        # Rollback exports must include writes made after migration, including revisions.
        from export_legacy_storage import export
        from migrate_operational_store import documents
        current = OperationalStore(fresh, self.root)
        current.begin(True)
        for key, value in documents(self.root).items():
            current.write(self.root / key, value)
        current.write(self.root / 'data/leads.json', [{'source': 'post-migration-fixture'}])
        current.finish(True)
        output = Path(self.tmp.name) / 'rollback'
        export(fresh, output)
        self.assertTrue(verify(output / 'site', fresh, [output / 'followup.sqlite3', output / 'resources.sqlite3'])['ok'])

    @unittest.skipUnless(os.environ.get('STORAGE_TEST_APP'), 'live app copy required')
    def test_frozen_assignment_submit_review_and_withdraw_are_preserved(self):
        app = load_module('storage_practice_test_app', os.environ['STORAGE_TEST_APP'])
        app.BASE_DIR = str(self.root)
        app.STUDENTS_FILE = str(self.root / 'students.json')
        app.PRACTICE_ASSIGN_FILE = str(self.root / 'data/practice-assignments.json')
        app.PRACTICE_SUBMIT_FILE = str(self.root / 'data/practice-submissions.json')
        app.operational_store = self.store
        expires = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        app.active_tokens.update({'teacher-test': {'role': 'teacher', 'username': 'emily_mo', 'expires': expires},
                                 'learner-test': {'role': 'student', 'username': 'Learner', 'expires': expires}})
        client = app.app.test_client()
        teacher = {'Authorization': 'Bearer teacher-test'}
        student = {'Authorization': 'Bearer learner-test'}
        task = {'id': 'task-1', 'title': 'Frozen fixture', 'studentRenderer': 'worksheet'}
        assignment = {'assignmentId': 'assignment-1', 'studentIds': ['Learner'], 'taskIds': ['task-1'], 'tasks': [task]}
        self.assertEqual(client.post('/api/practice/assignments', headers=teacher, json=assignment).status_code, 200)
        frozen = client.get('/api/practice/assignments', headers=student).json[0]
        submission = {'submissionId': 'submission-1', 'assignmentId': 'assignment-1', 'studentId': 'Learner',
                      'assignmentVersionId': 'incorrect', 'answers': [{'taskId': 'task-1', 'answer': 'Fixture answer.'}]}
        self.assertEqual(client.post('/api/practice/submissions', headers=student, json=submission).status_code, 400)
        self.assertEqual(self.store.read(self.root / 'data/practice-submissions.json'), [])
        submission['assignmentVersionId'] = frozen['assignmentVersionId']
        submission['answers'][0]['taskVersionId'] = frozen['tasks'][0]['taskVersionId']
        self.assertEqual(client.post('/api/practice/submissions', headers=student, json=submission).status_code, 200)
        self.assertEqual(client.put('/api/practice/submissions/submission-1', headers=teacher,
                                   json={'teacherFeedback': 'Well done', 'status': 'checked'}).status_code, 200)
        saved = self.store.read(self.root / 'data/practice-submissions.json')[0]
        self.assertEqual(saved['answers'], submission['answers'])
        self.assertEqual(saved['assignmentVersionId'], frozen['assignmentVersionId'])
        self.assertEqual(client.delete('/api/practice/assignments/assignment-1', headers=teacher).status_code, 200)
        self.assertEqual(client.get('/api/practice/assignments', headers=student).json, [])
        self.assertEqual(self.store.read(self.root / 'data/practice-submissions.json')[0], saved)

    @unittest.skipUnless(os.environ.get('STORAGE_TEST_APP'), 'live app copy required')
    def test_flask_profile_account_and_static_reads_use_database(self):
        app = load_module('storage_test_app', os.environ['STORAGE_TEST_APP'])
        app.BASE_DIR = str(self.root)
        app.DATA_DIR = str(self.root / 'data')
        app.STUDENTS_FILE = str(self.root / 'students.json')
        app.PRACTICE_ASSIGN_FILE = str(self.root / 'data/practice-assignments.json')
        app.PRACTICE_SUBMIT_FILE = str(self.root / 'data/practice-submissions.json')
        app.operational_store = self.store
        app.active_tokens['teacher-test'] = {'role': 'teacher', 'username': 'emily_mo', 'expires': datetime.datetime.utcnow() + datetime.timedelta(hours=1)}
        client = app.app.test_client()
        client.set_cookie('authToken', 'teacher-test')
        auth = {'Authorization': 'Bearer teacher-test'}
        # A stale legacy file must not leak into the migrated API or static JSON route.
        self.write('learner.json', {'name': 'stale'})
        self.assertEqual(client.get('/api/students/Learner', headers=auth).json, self.profile)
        self.assertEqual(client.get('/learner.json').json, self.profile)
        response = client.post('/api/student-accounts', headers=auth, json={'name': 'Second', 'courseName': 'Power Up 1'})
        self.assertEqual(response.status_code, 201)
        self.assertFalse((self.root / ('student_' + app.hashlib.sha256(b'Second').hexdigest()[:20] + '.json')).exists())
        self.assertEqual(client.get('/api/students/Second', headers=auth).json['courseName'], 'Power Up 1')
        self.assertEqual(client.post('/api/auth/login', json={'username': 'Second', 'role': 'student'}).status_code, 200)
        response = client.patch('/api/student-accounts/Learner', headers=auth, json={'courseName': 'Power Up 1', 'mode': 'add'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/api/students/Learner', headers=auth).json['activeCourses'], ['Reading', 'Power Up 1'])
        response = client.post('/api/students/Learner/leave', headers=auth, json={'date': '2026-10-01', 'courseName': 'Reading', 'startTime': '09:00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.store.read(self.root / 'learner.json')['leaveRequests']), 1)
        response = client.post('/api/students/Learner/payments/delete', headers=auth, json={'index': 0, 'expected': self.profile['payments'][0]})
        self.assertEqual(response.status_code, 200)
        value = client.get('/api/students/Learner', headers=auth).json
        self.assertEqual((value['totalHours'], value['usedHours'], value['remainingHours']), (0, 1, -1))
        # Explicit errors must roll back a request without changing saved data.
        self.assertEqual(client.post('/api/students/Learner/payments/delete', headers=auth, json={'index': 0, 'expected': {}}).status_code, 409)
        self.assertEqual(client.get('/api/students/Learner', headers=auth).json, value)


if __name__ == '__main__':
    unittest.main()
