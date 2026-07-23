# Recovery Smoke Design

## Purpose

Validate that local recovery procedures remain deterministic after v0.1.0.

## Scope

- SQLite backup creation;
- restore into isolated directory;
- integrity_check;
- schema version validation;
- startup recovery simulation.

## Security boundary

Recovery reports must not include:

- Cookie;
- token;
- complete signed stream URL;
- raw payload;
- raw frame.

## Not included

This does not validate real Douyin recipient protocol.
`live_verified` remains `false`.
