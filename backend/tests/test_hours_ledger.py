import copy
import unittest

from hours_ledger import audit_profile, ensure_profile_ids


class HoursLedgerTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "totalHours": 4,
            "usedHours": 2,
            "remainingHours": 2,
            "payments": [{"courseName": "Power Up 1", "date": "2026-09-01", "hours": 4}],
            "records": [
                {"courseName": "Power Up 1", "date": "2026-09-02", "time": "09:00-10:30", "hours": 1.5},
                {"courseName": "Power Up 1", "date": "2026-09-09", "startTime": "09:00", "hours": 1},
            ],
            "leaveRequests": [{
                "courseName": "Power Up 1", "date": "2026-09-16", "startTime": "09:00",
                "requestTime": "2026-09-15T09:00:00Z", "status": "approved", "handled": True,
            }],
            "oneTimeClasses": [{
                "courseName": "Power Up 1", "date": "2026-09-02", "startTime": "09:00", "duration": 90,
            }],
        }

    def test_additive_ids_and_session_links_are_idempotent(self):
        original = copy.deepcopy(self.profile)
        migrated = ensure_profile_ids("student-a", copy.deepcopy(self.profile))
        first = copy.deepcopy(migrated)
        self.assertEqual(ensure_profile_ids("student-a", migrated), first)
        self.assertEqual(migrated["totalHours"], original["totalHours"])
        self.assertEqual(migrated["usedHours"], original["usedHours"])
        self.assertEqual(migrated["remainingHours"], original["remainingHours"])
        for collection in ("payments", "records", "leaveRequests", "oneTimeClasses"):
            self.assertTrue(all(item.get("id") for item in migrated[collection]))
            self.assertEqual(len({item["id"] for item in migrated[collection]}), len(migrated[collection]))
        self.assertEqual(migrated["records"][0]["sessionId"], migrated["oneTimeClasses"][0]["sessionId"])

    def test_existing_ids_are_preserved(self):
        self.profile["records"][0]["id"] = "legacy-record-42"
        migrated = ensure_profile_ids("student-a", self.profile)
        self.assertEqual(migrated["records"][0]["id"], "legacy-record-42")

    def test_audit_flags_hours_mismatch_without_mutating_data(self):
        original = copy.deepcopy(self.profile)
        audit = audit_profile(self.profile)
        self.assertIn("balance:used_differs_from_record_hours", audit["issues"])
        self.assertIn("balance:remaining_differs_from_hours_basis", audit["issues"])
        self.assertEqual(audit["balance"]["recordHoursAssumingMissingIsOne"], 2.5)
        self.assertEqual(self.profile, original)

    def test_audit_flags_conflicting_leave_state(self):
        self.profile["leaveRequests"][0]["handled"] = False
        audit = audit_profile(self.profile)
        self.assertTrue(any(issue.startswith("leave:handled_status_mismatch") for issue in audit["issues"]))

    def test_duplicate_existing_ids_are_not_silently_changed(self):
        self.profile["records"][0]["id"] = "same-id"
        self.profile["records"][1]["id"] = "same-id"
        ensure_profile_ids("student-a", self.profile)
        audit = audit_profile(self.profile)
        self.assertIn("records:duplicate_id:1", audit["issues"])


if __name__ == "__main__":
    unittest.main()
