---
name: fork-maintenance
description: "Use when maintaining Granary, the unofficial MinIO fork."
version: 0.1.0
author: Neil Lei (Lei-k), Hermes Agent
license: AGPL-3.0-only
platforms: [linux]
---

# Fork maintenance

## When to use

Use for dependency, Go toolchain, CI and fork-positioning changes in this repo.
Do not use as authority for production deployment, release publishing, S3 API
changes, new storage formats or license/trademark decisions.

## Owns / does not own

Owns reproducible source maintenance, bounded verification and issue tracking.
Does not own merge permission, artifact-publication decisions, upstream support
or production acceptance. `AGENTS.md` governs repository conduct;
`docs/maintenance/plan.md` governs accepted scope and phase order.

## Procedure

1. Verify the live base, issue and isolated branch; read the plan, governing
   instructions, affected manifests, callers and tests before mutation.
2. Run Go from the selected toolchain; `go.mod` declares minimum language and
   preferred toolchain separately. Pin scanner/linter versions. Do not replace
   the module path or rewrite all upstream URLs as part of a dependency update.
3. Run `go mod tidy`, `go mod verify`, `go build ./...`, `go vet ./...`,
   `go test -count=1 ./internal/...` and the affected command-package tests.
   The upstream canonical test tags are `kqueue,dev`, with `CGO_ENABLED=0` and
   `MINIO_API_REQUESTS_MAX=10000`. Derive longer integration commands from the
   Makefile and scripts; do not run publishing/hotfix/install targets as tests.
4. Bound expensive scans/tests with task-local CPU/memory limits. Store logs,
   per-command exits and content hashes outside the checkout. An observer's
   successful exit is not the inner command's result. Preserve failed attempts.
5. Parse govulncheck JSON findings by trace detail: module-only, imported
   package, reachable symbol. A scan at a coarser level does not prove absence
   of reachable code, and JSON mode exit zero does not prove zero findings.
   List unimported packages without suppressing advisories globally.
6. Inspect all workflow event types before pushing. During the legacy-trigger
   period, use a verified `[skip ci]` checkpoint for push/pull_request events;
   it does not suppress `pull_request_target` or scheduled/operational events.
   Do not query Actions except after the exact completion webhook.
7. Publish the first coherent checkpoint as Draft, then finish qualification
   and fresh independent review of its exact HEAD. New issues/follow-ups must
   not be silently repaired outside the bounded write-set.

## Pitfalls

- A fork's upstream-only runner, PAT or organization project is not available
  merely because the workflow file exists. Do not copy upstream secrets.
- Go toolchain changes can enable new vet checks; compare failures against a
  matching baseline rather than assuming a previously unchanged test is valid.
- A source build is not a release; a unit test is not a distributed-storage,
  console-browser, external identity-provider or physical deployment test.

## Verification

Require a clean diff check, module consistency, actual required command exits,
a source-bound scanner summary, frozen base/HEAD and a separate review verdict.
Record every skipped or failed required gate with its exact reason. Check
remote branch and PR HEAD after publication, without reading hosted check state.
