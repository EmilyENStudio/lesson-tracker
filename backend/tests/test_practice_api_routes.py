#!/usr/bin/env python3
"""Integration tests for the practice API versioning and withdrawal behavior."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


class PracticeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("PRACTICE_TEST_APP") or not os.environ.get("PRACTICE_TEST_PATHS"):
            raise unittest.SkipTest("set PRACTICE_TEST_APP and PRACTICE_TEST_PATHS for live-route integration tests")
        app_path = Path(os.environ["PRACTICE_TEST_APP"]).resolve()
        module_dirs = [Path(value).resolve() for value in os.environ["PRACTICE_TEST_PATHS"].split(os.pathsep)]
        sys.path[:0] = [str(path) for path in module_dirs]
        spec = importlib.util.spec_from_file_location("practice_api_test_runtime", app_path)
        cls.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.api)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.assign_path = root / "assignments.json"
        self.sub_path = root / "submissions.json"
        self.assign_path.write_text("[]", encoding="utf-8")
        self.sub_path.write_text("[]", encoding="utf-8")
        self.api.PRACTICE_ASSIGN_FILE = str(self.assign_path)
        self.api.PRACTICE_SUBMIT_FILE = str(self.sub_path)
        self.api.active_tokens.clear()
        self.api.active_tokens.update({
            "teacher-token": {"username": "teacher", "role": "teacher", "expires": datetime.utcnow() + timedelta(hours=1)},
            "student-token": {"username": "student-1", "role": "student", "expires": datetime.utcnow() + timedelta(hours=1)},
            "other-token": {"username": "student-2", "role": "student", "expires": datetime.utcnow() + timedelta(hours=1)},
        })
        self.client = self.api.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def auth(token):
        return {"Authorization": "Bearer " + token}

    def teacher_assign(self):
        return self.client.post("/api/practice/assignments", headers=self.auth("teacher-token"), json={
            "assignmentId": "distribution-1",
            "practiceId": "lessonplan:unit-1",
            "sourceLessonId": "unit-1",
            "title": "Frozen task",
            "studentIds": ["student-1"],
            "taskIds": ["task-1"],
            "tasks": [{"id": "task-1", "type": "multiple-choice", "prompt": "Choose one"}],
            "status": "active",
        })

    def test_roles_versions_and_withdrawal_preserve_history(self):
        denied = self.client.post("/api/practice/assignments", headers=self.auth("student-token"), json={})
        self.assertEqual(403, denied.status_code)
        created = self.teacher_assign()
        self.assertEqual(200, created.status_code)
        assignment = json.loads(self.assign_path.read_text(encoding="utf-8"))[0]
        self.assertTrue(assignment["assignmentVersionId"].startswith("av1-"))
        self.assertTrue(assignment["tasks"][0]["taskVersionId"].startswith("tv1-"))

        private_read = self.client.get("/api/practice/assignments?student=student-2", headers=self.auth("student-token"))
        self.assertEqual(403, private_read.status_code)
        response = self.client.post("/api/practice/submissions", headers=self.auth("student-token"), json={
            "submissionId": "submission-1",
            "assignmentId": "distribution-1",
            "studentId": "student-1",
            "answers": [{"taskId": "task-1", "value": "A"}],
            "files": [],
            "status": "submitted",
        })
        self.assertEqual(200, response.status_code)
        submission = json.loads(self.sub_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(assignment["assignmentVersionId"], submission["assignmentVersionId"])
        self.assertEqual(assignment["tasks"][0]["taskVersionId"], submission["answers"][0]["taskVersionId"])

        withdrawn = self.client.delete("/api/practice/assignments/distribution-1", headers=self.auth("teacher-token"))
        self.assertEqual(200, withdrawn.status_code)
        self.assertEqual(1, len(json.loads(self.sub_path.read_text(encoding="utf-8"))))
        student_assignments = self.client.get("/api/practice/assignments", headers=self.auth("student-token"))
        self.assertEqual([], student_assignments.get_json())
        teacher_submissions = self.client.get("/api/practice/submissions", headers=self.auth("teacher-token"))
        self.assertEqual(1, len(teacher_submissions.get_json()))

    def test_student_cannot_submit_to_another_students_assignment(self):
        self.assertEqual(200, self.teacher_assign().status_code)
        response = self.client.post("/api/practice/submissions", headers=self.auth("other-token"), json={
            "submissionId": "submission-2",
            "assignmentId": "distribution-1",
            "studentId": "student-2",
            "answers": [{"taskId": "task-1", "value": "A"}],
            "files": [],
        })
        self.assertEqual(403, response.status_code)
        self.assertEqual([], json.loads(self.sub_path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
