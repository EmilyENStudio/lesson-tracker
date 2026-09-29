# Unified Operational Storage

## Production status

The live cutover completed on 2026-09-29. Operational JSON documents and the two existing SQLite service stores now share `/var/lib/emily-store/core.sqlite3`. The original business records, payloads, IDs, and SQLite table rows were verified during migration. Static lesson plans, book text, audio, and illustrations remain files; this change unifies operational state, not all website assets.

## Data boundaries

- `storage_documents` holds the canonical JSON payloads and monotonically increasing revisions.
- `storage_history` keeps the prior payload for each changed document revision.
- `storage_ids`, `storage_roster`, `storage_assignments`, and `storage_submissions` index stable IDs and enforce references while preserving the existing API payload shapes.
- The existing `states`, `feedback`, `audit`, invitation, and resource tables are copied into the same database without changing their schema or row IDs.
- The backend, after-class service, and practice-resource service use the same SQLite path. Writers serialize through SQLite transactions; a failed request rolls back as a unit.

## Verification

The migration is additive and refuses to overwrite an existing destination. It compares every migrated JSON value semantically, checks each old SQLite table row-for-row including row IDs, then runs foreign-key and SQLite integrity checks. `audit_unified_storage.py` checks IDs, balance/record links, roster/profile links, assignment/submission references, and the copied SQLite tables without printing learner details.

The 2026-09-29 live audit reported 18 documents, 9 profiles, 116 lesson records, 19 payments, 10 leave requests, 64 temporary classes, 707 catalog tasks, 54 assignments, 124 task snapshots, and 26 submissions with no issues. Existing SQLite counts were 46 states, 2 feedback records, 163 audit rows, 1 invitation, 4 replies, and 9 resources. No source record was rewritten to manufacture new identifiers.

## Backup and recovery

`emily-store-backup.timer` runs an SQLite online backup. The first post-cutover backup completed successfully. Keep `/var/lib/emily-store/backups` private and retain the configured 28 most recent backups.

The exact pre-cutover files and consistent SQLite snapshots are under `/root/emily-practice-backups/unified-store-cutover-20260929/`. Rollback must use the current unified database, not only that stale pre-cutover snapshot: export the current documents/tables back to the legacy JSON and SQLite layouts, restore the backed-up app/Nginx adapters, then restore the prior backup timers. This preserves writes made after migration. The copy in Git intentionally excludes learner data, secrets, and the production `app.py` containing deployment configuration.

## Operating cautions

- Never edit/delete the old storage files as cleanup until a separately verified retention and restore plan is approved. They are currently retained as a fallback snapshot, not authoritative live stores.
- Do not restore the pre-cutover snapshot over newer live data.
- Before code changes, compare the live file hash with the expected release and preserve concurrent production-only fixes.
- Static courseware/assets and content catalogs remain file-backed unless a separate migration is planned and validated.
- A SQLite database is a single-writer system. Use bounded transactions; do not hold a write transaction while calling another service or waiting on user input.
