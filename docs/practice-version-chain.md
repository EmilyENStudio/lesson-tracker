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
