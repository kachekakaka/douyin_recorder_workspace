# Database Maintenance and Startup Recovery

## Purpose

v0.1.1 Phase 2 adds safe SQLite maintenance planning and deterministic restart recovery tests.
These tools do not access Douyin and do not validate the recipient protocol.

## Read-only maintenance plan

```bash
python tools/database_maintenance.py --root .
```

The default mode does not modify the database. It validates:

- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`;
- all recorded migration names and checksums;
- SQLite page, freelist, WAL and SHM metadata;
- whether a future manual VACUUM review may be useful.

The v0.1.1 Phase 2 tool never executes VACUUM. It only reports a recommendation after at least
64 MiB and 20% of database pages are free.

## Apply checkpoint and optimize

Stop the application first. Then run:

```bash
python tools/database_maintenance.py \
  --root . \
  --apply \
  --confirm-stopped \
  --backup-dir backups/maintenance \
  --output userdata/database-maintenance-report.json
```

Apply mode fails closed unless all of the following are true:

1. the configured SQLite path is a normal local file;
2. integrity, foreign keys and migration checksums pass;
3. `--confirm-stopped` is explicit;
4. a full runtime backup is created and its SHA-256 sidecar verifies;
5. an exclusive SQLite lock can be acquired;
6. the WAL checkpoint reports no busy readers;
7. post-maintenance integrity and migration state match the precheck.

The only write maintenance operations are `wal_checkpoint(TRUNCATE)` and `PRAGMA optimize`.
No business row or media file is deleted.

## Automated smoke tests

```bash
python tools/database_maintenance_smoke.py \
  --output-dir userdata/database-maintenance-smoke \
  --json-output userdata/database-maintenance-smoke.json

python tools/startup_recovery_smoke.py \
  --cycles 25 \
  --json-output userdata/startup-recovery-smoke.json
```

The startup recovery smoke repeats synthetic crash recovery for recording sessions, open
recipient intervals, running postprocess jobs, attempts and outputs. Each cycle also runs a second
recovery pass to prove idempotency.

## Failure injection

Automated tests cover:

- another SQLite writer holding the database lock;
- modified migration checksums;
- missing explicit stopped confirmation;
- backup and checksum validation;
- repeated recording and postprocess restart recovery.

## Security boundary

Public maintenance reports include only controlled metadata. They exclude private configuration
values, absolute runtime paths, complete signed URLs, Cookie, token, raw payload and raw frame.
Runtime backup archives can contain private configuration and must never be committed to GitHub.

`live_verified=false` remains unchanged. Real protocol evidence continues through Issue #1.
