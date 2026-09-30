# Durable master-push queue correction

## Scope

Preserve successfully received ordinary master pushes across the UTC month-boundary window. The existing API budget, model, maximum request, source filters and public-issue policy stay in force. GitHub trigger loss, 100-run concurrency overflow, force pushes, unsupported or oversized diffs remain outside full coverage.

The queue stores immutable repository/ref/before/after identifiers on the existing ai-review-budget branch. It never stores source code, credentials or instructions to execute. All queued code is fetched only as inert comparison JSON. `.github` control-plane files are excluded from application-source review, alongside existing generated/vendor/binary/README exclusions.

## Processing

The coordinator runs on ordinary master pushes, hourly at minute 17, and manual workflow_dispatch. It enqueues even when AI_REVIEW_ENABLED is false. It does not claim or spend during blackout, when the gate is false, after pricing expiry, or when the monthly reservation cap is reached. Pending events remain present for later processing.

One eligible pending event is claimed per workflow. At most ten non-source/unsupported events are triaged beforehand. A normal empty hourly poll performs one GitHub read and no OpenAI request. GitHub Actions schedules can be delayed, dropped or disabled for inactivity; next pushes/manual dispatch also drain preserved work. There is no exact completion-time guarantee.

Coordinator and finalizer use contents:write without the model key. Reviewer has contents:read and the environment key. Publisher has contents:read+issues:write without the key. Both scripts are fetched from one audited immutable commit, into the same isolated job directory. No new credential or permission class is added.

## States and payment safety

- pending: preserved, no claim currently active
- claimed: USD1 permanently reserved and assigned to one run/attempt
- complete: review completed; enabled publication succeeded or publication was deliberately disabled
- ignored: no eligible application source, no paid reservation
- attention: unsupported diff, configuration failure, uncertain paid outcome or publication failure; no automatic paid retry

A review that explicitly proves it stopped before payment due to crossing the safe month window returns to pending. Its previous USD1 reservation is not refunded. Any missing/ambiguous result stays claimed or attention, requiring manual investigation rather than another automatic paid call. Partial reruns with another run_attempt cannot reuse or requeue the old claim. Queue capacity is 3,000 recorded events; it fails visibly rather than silently truncating history.

## Migration and setup window

CAS migration from ledger version1 to version2 retains every original monthly reservation. On first migration, one catch-up range from 1e5da901d4d8ee94d90c8ca123fa8584d9219a27 (last successful pre-correction smoke review) to the activation event SHA covers any ordinary master changes during setup. The first individual push is not redundantly enqueued. The catch-up diff still obeys normal size/lineage limits; unsupported ranges become attention-required, never silently deleted.

Do not reset or replace the live ledger with an empty example. The live coordinator performs the guarded migration. Preserve the original baseline until migration completes.

## Deployment verification

Pause paid review and publication only for the activation swap; retain the last reviewed baseline above. Publish audited code on a staging branch, pin its exact commit in the active master workflow, and verify enqueue/migration while gates are false. Then restore review, validate pending-event processing, and restore publication. Use only harmless source changes and verify no synthetic Issues are created.

Regression coverage includes ledger preservation, blackout deferral and next-month recovery, gate-disabled capture, duplicate delivery, budget exhaustion, pricing expiry, corrupted claims, compare failure, ambiguous payment, partial reruns, finalization and workflow permission isolation.
