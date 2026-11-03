# Application tracking backup and rollback

The tracking release adds only `application_tracking` and `application_events`
tables. It does not rewrite existing jobs, searches, users, delivery queues, or
Applied flags. Older app code ignores these tables. Prefer an **image rollback
with the current database** so jobs and alerts discovered since deployment survive.
Keep the tracker tables even when temporarily running the older app.

## Verified baseline before this release

- Previous code: `57be8193f7eac99687c1e6ba838781392c68d93e`.
- Database: `/var/lib/job-watcher/data/backups/pre-application-tracking-20261103T193606Z.sqlite3`.
- Database SHA-256: `04b440883b568042f034820f6163cfbab2747264fced0b678dd664280ba1cb2f`.
- SQLite integrity check: `ok`; 2 accounts, 5 searches, 1,116 jobs, 1,172 user-job records.
- Source archive: `/var/lib/job-watcher/data/backups/pre-tracking-source-57be819.tar.gz`.
- Source SHA-256: `679629984e88b74d1bc1e59be2d1b7d0c32644266d811c820e0c70bfdd011611`.
- Pinned image tags: `job-watcher-rollback:watcher-pre-tracking-20261103`
  and `job-watcher-rollback:browser-pre-tracking-20261103`.
- A second backup preflight passed on production without replacing containers:
  stamp `20261103T194241Z`, database SHA-256
  `36d7c23b423bcbe4c0abb2f549bb0b6ba7e09f3c9b66c0da9f235342c951ce62`.
  Its `.compose.yaml` override is ready for image rollback.
- An offline rehearsal confirmed code at `57be819` can read and scan a migrated
  synthetic database, and tracker notes/status survive returning to the new code.

Backups stay on the private server with restricted permissions; do not attach them
to issues or commit them, since account databases contain credentials. These are
release rollback backups on the same VM, not protection against loss of the VM.

## Automatic pre-deployment backup

`scripts/deploy.sh` pins both exact running images before rebuilding, makes a
consistent online SQLite backup, runs `PRAGMA integrity_check`, and records a
SHA-256 checksum. A failed backup aborts deployment. Under the host data directory,
`backups/<UTC-stamp>.json` records the database name and checksum, and
`backups/<UTC-stamp>.compose.yaml` selects the pinned previous images. No resolved
environment configuration is printed or copied.

## Roll back code without losing recent data

1. Confirm the server address, Job Watcher Compose status, disk space, and public
   route. Work only in Job Watcher's server checkout. Do not touch the PS5 stack.
2. Select the verified pre-release stamp from `backups/` (not a later deployment).
   Check that both image tags listed in its Compose override still exist.
3. With Docker privileges, run from the Job Watcher checkout:

   ```bash
   docker compose --project-name job-watcher --env-file /etc/job-watcher.env \
     -f compose.yaml \
     -f /var/lib/job-watcher/data/backups/<UTC-stamp>.compose.yaml \
     up -d --no-build --force-recreate
   ```

   Recreate both services because they share a network namespace. Keep the current
   database and browser-profile volume. Do not run `down -v` or prune rollback images.
4. Verify both services are healthy, the public dashboard responds, authenticated
   searches and job counts are intact, and source workers resume. New source offers
   during verification are normal; do not expect frozen counts.
5. To return to the tested tracking release, rerun its successful CI/deploy workflow.

For the named baseline images, create an equivalent two-service override using
the pinned tags above if no automatic stamp override is available.

## Database restoration is a last resort

An image rollback does not need a database restore. Restore a database only for
confirmed corruption or an explicitly chosen point-in-time recovery: changes
after that backup would be lost and previously sent Telegram alerts might be
resent. Stop both Job Watcher services first, retain the current database and its
WAL/SHM files as a separate recovery copy, verify the chosen backup checksum and
SQLite integrity, then restore it with the data directory's existing owner and
mode. Move the old WAL/SHM sidecars aside with the old database rather than
reusing them. Restart both services and verify accounts, searches, alerts, and
source health. Never restore a database while a watcher is running.
