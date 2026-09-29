import copy
import unittest

from audit_practice_chain import audit_chain
from practice_versioning import link_assignment_version, link_submission_version, task_version


class PracticeChainAuditTests(unittest.TestCase):
    def setUp(self):
        task = {"id": "task-1", "prompt": "Original question"}
        task["taskVersionId"] = task_version(task)
        self.assignment = {"assignmentId": "a1", "tasks": [task], "taskIds": ["task-1"],
                           "studentIds": ["learner"], "status": "withdrawn"}
        link_assignment_version(self.assignment)
        self.submission = {"submissionId": "s1", "assignmentId": "a1", "studentId": "learner",
                           "answers": [{"taskId": "task-1", "value": "My answer"}],
                           "files": [{"taskId": "task-1", "url": "sample.pdf"}]}
        link_submission_version(self.submission, self.assignment)
        self.catalog = [{"id": "task-1", "prompt": "Revised question"}]
        self.catalog[0]["taskVersionId"] = task_version(self.catalog[0])

    def test_valid_historical_withdrawn_distribution_is_preserved(self):
        before = copy.deepcopy((self.catalog, self.assignment, self.submission))
        report = audit_chain(self.catalog, [self.assignment], [self.submission])
        self.assertTrue(report["ok"])
        self.assertEqual(1, report["counts"]["historicalSnapshots"])
        self.assertEqual(before, (self.catalog, self.assignment, self.submission))

    def test_stale_attachment_version_is_detected(self):
        self.submission["files"][0]["taskVersionId"] = "tv1-stale"
        self.assertIn("submission.invalid_version_links", audit_chain(self.catalog, [self.assignment], [self.submission])["issues"])

    def test_missing_versions_and_wrong_recipient_are_detected(self):
        self.submission.pop("assignmentVersionId")
        self.submission["studentId"] = "another-learner"
        report = audit_chain(self.catalog, [self.assignment], [self.submission])
        self.assertIn("submission.missing_version", report["issues"])
        self.assertIn("submission.wrong_recipient", report["issues"])


if __name__ == "__main__":
    unittest.main()
