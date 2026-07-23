# Recovery Smoke

## Purpose

`tools/backup_restore_smoke.py` validates that runtime backups remain recoverable after the
v0.1.0 release without reading any real Douyin credentials or protocol payloads.

## Executed workflow

1. Create an isolated runtime root and initialize the current SQLite schema.
2. Insert one synthetic room marker.
3. Create a runtime backup through the production `create_runtime_backup` implementation.
4. Validate the external SHA-256 sidecar.
5. Extract the ZIP into a new isolated directory with path-traversal and symbolic-link checks.
6. Run read-only SQLite `integrity_check`, `foreign_key_check`, and migration checksum checks.
7. Confirm the restored schema version and synthetic marker row.
8. Confirm that media contents are not archived; only the records manifest is preserved.

## Commands

```bash
python tools/backup_restore_smoke.py \
  --output-dir userdata/backup-restore-smoke \
  --json-output userdata/backup-restore-smoke.json

python tools/database_integrity_check.py userdata/douyin_recorder.db
python tools/diagnostics_report.py --root . --output userdata/diagnostics-report.json
```

The smoke is also executed by Python 3.12 CI and the Windows `verify.bat` entrypoint.

## Security boundary

Public smoke and diagnostic reports contain only controlled metadata. They exclude private
configuration values, complete stream URLs, raw protocol data, media contents, and absolute
runtime paths. The runtime backup itself can contain private configuration and must remain in a
protected local location outside Git.

## Protocol boundary

This validation does not prove any real Douyin recipient protocol field. The contract remains
`live_verified=false`, and Issue #1 remains the evidence gate.
