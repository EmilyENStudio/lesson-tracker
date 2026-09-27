import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from migrate_ids import run


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "web"
        self.root.mkdir()
        (self.root / "students.json").write_text(json.dumps([
            {"id": "student-a", "dataFile": "profile-a"},
            {"id": "teacher", "dataFile": "teacher", "role": "teacher"},
        ]), encoding="utf-8")
        self.profile_path = self.root / "profile-a.json"
        self.profile_path.write_text(json.dumps({
            "totalHours": 3,
            "usedHours": 1,
            "remainingHours": 2,
            "payments": [{"courseName": "Reading A1", "date": "2026-09-01", "hours": 3}],
            "records": [{"courseName": "Reading A1", "date": "2026-09-02", "time": "18:30-19:30"}],
            "leaveRequests": [],
            "oneTimeClasses": [],
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_never_writes_profile(self):
        original = self.profile_path.read_bytes()
        result = run(self.root, apply=False, backup_dir=None)
        self.assertEqual(self.profile_path.read_bytes(), original)
        self.assertEqual(result["idsAdded"], 2)
        self.assertFalse(result["applied"])

    @patch("migrate_ids.subprocess.run")
    def test_apply_backups_and_only_adds_identifiers(self, run_systemctl):
        run_systemctl.return_value.returncode = 1
        original = self.profile_path.read_bytes()
        backup = Path(self.temp.name) / "private-backup"
        result = run(self.root, apply=True, backup_dir=backup)
        migrated = json.loads(self.profile_path.read_text(encoding="utf-8"))
        old = json.loads(original.decode("utf-8"))
        self.assertEqual(result["filesChanged"], 1)
        self.assertEqual(migrated["totalHours"], old["totalHours"])
        self.assertEqual(migrated["usedHours"], old["usedHours"])
        self.assertEqual(migrated["remainingHours"], old["remainingHours"])
        self.assertEqual(json.loads((backup / "profile-000.json").read_text(encoding="utf-8")), old)
        self.assertTrue((backup / "manifest.json").exists())

    @patch("migrate_ids.subprocess.run")
    def test_apply_refuses_while_backend_is_active(self, run_systemctl):
        run_systemctl.return_value.returncode = 0
        with self.assertRaisesRegex(RuntimeError, "Stop emily-backend"):
            run(self.root, apply=True, backup_dir=Path(self.temp.name) / "private-backup")


if __name__ == "__main__":
    unittest.main()
