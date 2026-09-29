# Practice Version and Distribution Chain

Published assignments contain frozen selected task snapshots. Each snapshot carries
a deterministic `taskVersionId`; each distribution carries `assignmentVersionId`.
Catalog edits create new task content versions and never replace earlier snapshots.

The student page submits the displayed assignment and task versions. The API rejects
conflicting versions or duplicate task IDs before saving. Legacy clients that omit
version fields remain supported, with links filled from the frozen distribution.
Teacher review only changes review fields. Withdrawal preserves the distribution,
student answers, attachments, and feedback while hiding it from active student lists.

Run the read-only audit with:

```sh
python3 backend/audit_practice_chain.py /var/www/lesson-tracker
```

It checks primary ID uniqueness, snapshot integrity, student recipients, duplicate
submissions, and assignment/answer/attachment version links. Historical snapshots
that differ from the current catalog are counted and remain valid. The output has
aggregate counts only and contains no student names or answers. It never writes data.

Route tests use a Flask test client, temporary assignment/submission JSON files,
and synthetic auth tokens. Production profile, attendance, and payment files are
not test fixtures. This phase retains the existing file storage and service ownership.

## Runtime deployment boundary

The live homepage contains newer server-only changes beyond the repository mirror.
Do not deploy this repository as a whole-site copy. Compare and back up each live
file, then apply only the selected modules or guarded patches. The after-class
loader added on 2026-09-28 is represented by `backend/patch_after_class_home.py`.
Its default mode only checks; applying requires the inspected live file hash and
preserves all other bytes, owner, and mode. The current production loader is already
up to date. The follow-up service model and JavaScript snapshot include that day's
notification and lesson-record matching fixes.
