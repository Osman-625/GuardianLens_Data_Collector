# Build and Audit Verification Report

Verification date: 2026-08-24  
Target: GuardianLens Data Collector v2.0.0  
Environment used: `guardianlens-collector`, Python 3.12.13, Node.js 24.18.0

## Readiness boundary

The source and distributable are eligible for the real two-listing soft test. They are not
certified for a 2,500-record collection campaign until one manually selected Carousell
listing and one manually selected Mudah listing both pass the live Chrome/provider
procedure in `README.md`.

## Final automated gates

- Python compile/import check: PASS
- Pytest suite: PASS (173 collected/passed; 0 failed; 0 skipped)
- JavaScript syntax: PASS for dashboard, extension service worker, and popup
- Structural self-test: PASS; OS-temporary database only
- Synthetic integration: PASS; one Carousell + one Mudah record approved in an OS-temporary project
- Security/data-integrity regression suites: PASS
- Concurrent duplicate approval and atomic Edit + Approve: PASS
- Concurrent first-start migration: PASS; one durable migration row and `quick_check=ok`
- Crash recovery: PASS, including explicit interrupted-review audit marker
- Soft-test database/assets/exports/reports isolation: PASS
- Manifest generation and verification: PASS on the documentation-complete source tree
- Strict-allowlist ZIP and fresh-extraction verification: PASS
- Fresh extracted application startup on an isolated loopback port: PASS

The only test warning is an upstream Starlette `TestClient` deprecation concerning its
httpx integration; it does not indicate an application test failure.

## Five audit passes

### Pass 1 — Functional

Traced manual capture → captured queue → batch → normalized provider extraction → PII and
image processing → validation → mandatory review → atomic approval → approved-only export.
Fixed invalid listing acceptance, invisible failures, provider/config errors, review form
mutation/rollback problems, retry loops, and partial image/capture writes.

### Pass 2 — Data integrity and consistency

Added versioned/validated migrations with pre-migration backups; expanded orphan, file,
hash, duplicate, state-history, review-session, processing-run, cleanup, and soft-test
audits; made counts/planner/export use coherent read snapshots; added full lifecycle search.

### Pass 3 — Adversarial failure and security

Hardened localhost Host/client boundaries, form/API token checks, same-origin private assets,
provider error redaction, bounded retry/backoff, HTTPS image retrieval and SSRF defenses,
safe image re-encoding, atomic filesystem choreography, release secret scanning, and ZIP
path/content verification. An unsafe automatic production-reset utility found during this
pass was removed and regression-protected; production reset is not a shipped feature.

### Pass 4 — Research methodology

Verified that marketplace discovery and selection remain manual, capture is researcher
triggered, batches consume only already captured rows, AI only fills collection fields,
there are no fraud/legitimacy or AI-confidence fields, approval is human-only, completion is
based on approved counts, and private screenshots never enter ordinary exports.

### Pass 5 — Researcher experience

Added exact live status counts, operational/failure visibility, All Records search/paging,
bounded review queue totals, safe reason/provenance displays, dirty-form protection,
status-aware missing-asset copy, keyboard/focus/touch improvements, and 4-second component
polling without full-page reloads. Provider switches are serialized and stale model-list
responses are ignored.

## Release hygiene verified by code/tests

The release uses an explicit source allowlist and rejects stale manifests. It excludes:

- `.env` and provider credentials
- collector token/provider override files
- production and soft-test SQLite databases and sidecars
- screenshots, staging material, collected product images, exports, and reports
- logs, caches, bytecode, dependencies, temporary files, and agent/tool state

The ZIP verifier rejects unsafe/duplicate names, symlinks, oversized content, runtime-file
injection, missing required files/directories, manifest mismatches, and secret-like content.
Fresh verification runs self-test, the complete pytest suite, and synthetic integration from
a newly extracted disposable directory.

## Live checks still required

These cannot be certified offline because they require the operator's current Chrome,
marketplace pages, network path, and configured provider account:

1. Capture exactly one manually chosen Carousell Malaysia individual listing.
2. Capture exactly one manually chosen Mudah individual listing.
3. Confirm actual page evidence/image URLs remain compatible with current marketplace DOMs.
4. Confirm the selected real provider/model authenticates, extracts the normalized schema,
   and reports quota/model/network failures safely.
5. Review, correct, approve, audit, and export both records entirely in soft-test mode.

Do not begin large real collection until all five live checks pass.
