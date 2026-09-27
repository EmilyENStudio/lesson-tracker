import unittest

from practice_versioning import (
    assignment_version,
    link_assignment_version,
    link_submission_version,
    task_version,
)


class PracticeVersioningTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "task-1", "type": "multiple-choice", "prompt": "Choose one", "questions": [{"answer": "A"}]}
        self.assignment = {
            "assignmentId": "distribution-1",
            "practiceId": "lessonplan:lesson-1",
            "sourceLessonId": "lesson-1",
            "taskIds": ["task-1"],
            "tasks": [dict(self.task)],
            "studentIds": ["student-1"],
        }

    def test_content_versions_are_stable_and_change_with_content(self):
        first = task_version(self.task)
        self.assertEqual(first, task_version(dict(self.task)))
        changed = dict(self.task, prompt="Choose another")
        self.assertNotEqual(first, task_version(changed))

    def test_assignment_and_submission_link_to_frozen_versions(self):
        link_assignment_version(self.assignment)
        expected_assignment = assignment_version(self.assignment)
        answer = {"taskId": "task-1", "value": "A"}
        submission = {"answers": [answer], "files": []}
        link_submission_version(submission, self.assignment)
        self.assertEqual(expected_assignment, submission["assignmentVersionId"])
        self.assertEqual(self.assignment["tasks"][0]["taskVersionId"], answer["taskVersionId"])

    def test_assignment_rejects_mismatched_snapshot_ids(self):
        self.assignment["taskIds"] = ["wrong-id"]
        with self.assertRaises(ValueError):
            link_assignment_version(self.assignment)

    def test_submission_rejects_answers_outside_the_assignment(self):
        link_assignment_version(self.assignment)
        with self.assertRaises(ValueError):
            link_submission_version({"answers": [{"taskId": "other-task"}], "files": []}, self.assignment)


if __name__ == "__main__":
    unittest.main()
