import copy
import unittest

from hours_ledger import (audit_profile, calculate_balance, ensure_profile_ids,
                          leave_is_handled, normalize_terminal_leave_handled)


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

    def test_duration_is_not_confused_with_credits(self):
        original = copy.deepcopy(self.profile)
        audit = audit_profile(self.profile)
        self.assertEqual(audit["balance"]["lessonCreditsUsed"], 2)
        self.assertEqual(audit["balance"]["recordDurationHours"], 2.5)
        self.assertNotIn("balance:used_differs_from_record_count", audit["issues"])
        self.assertEqual(self.profile, original)

    def test_balance_preserves_legacy_empty_payments_but_deletion_can_zero(self):
        profile = {"totalHours": 3, "usedHours": 1, "records": [{}], "payments": []}
        self.assertEqual(calculate_balance(profile)["remainingHours"], 2)
        self.assertEqual(calculate_balance(profile, empty_payments="zero")["remainingHours"], -1)

    def test_paid_hours_and_lesson_count_use_one_shared_balance_rule(self):
        result = calculate_balance(self.profile)
        self.assertEqual(result, {"totalHours": 4, "usedHours": 2, "remainingHours": 2})

    def test_cancelled_draft_and_deleted_records_do_not_charge(self):
        profile = {"totalHours": 5, "payments": [{"hours": 5}], "records": [
            {"status": "completed"}, {"status": "cancelled"}, {"status": "draft"}, {"status": "deleted"}
        ]}
        self.assertEqual(calculate_balance(profile),
                         {"totalHours": 5, "usedHours": 1, "remainingHours": 4})

    def test_pending_request_can_be_handled_without_changing_its_status(self):
        self.assertTrue(leave_is_handled({"status": "pending", "handled": True}))
        self.assertTrue(leave_is_handled({"status": "approved", "handled": False}))
        self.assertFalse(leave_is_handled({"status": "pending", "handled": False}))

    def test_normalizer_only_materializes_terminal_states(self):
        profile = {"leaveRequests": [
            {"status": "approved", "handled": False},
            {"status": "cancelled", "handled": False},
            {"status": "pending", "handled": True},
        ]}
        self.assertEqual(normalize_terminal_leave_handled(profile), 2)
        self.assertEqual([x["handled"] for x in profile["leaveRequests"]], [True, True, True])
        self.assertEqual(profile["leaveRequests"][2]["status"], "pending")

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
