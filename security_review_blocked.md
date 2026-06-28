# Security Review Blocked

This repository was requested for a mandatory security review as part of a brownfield team workflow.

## Status

**Blocked / preservation fail**

## Why

The implementation artifacts required to safely review and modify the existing codebase were not present in the provided evidence:

- no scope document inventory
- no complete backend patch or full backend files
- no complete, non-truncated visible source for the Flask trust boundary files
- speculative UI candidate only; not certifiable against current repo state

## Consequence

Without the current source of record, any concrete code change risks deleting or altering existing routes, middleware, auth behavior, or security controls.

## Required to unblock

- `required-artifacts/scope_doc.md`
- `required-artifacts/gil_backend_patch.diff` or full backend files
- `required-artifacts/artie_ui_files.md` with complete visible files

## Interim guidance

The architecture guidance remains sound:
- keep the local-first single-Pi architecture
- harden `/api/ingest`
- move SQLite to request-scoped/operation-scoped connections
- add request and decoded-payload size limits
- use constant-time secret comparison
- keep file-serving paths constrained to fixed directories
- keep debug off by default
