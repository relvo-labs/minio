# Unofficial fork maintenance plan

Version: 0.2.0

This plan covers the initial maintenance of Granary (`relvo-labs/granary`,
formerly `relvo-labs/minio`). On 2026-11-14 the owner renamed the fork to
Granary, superseding the 2026-11-09 decision to keep the MinIO name, enabled
GitHub private vulnerability reporting, and selected `vYYYY.MM.DD` release tags
with `ghcr.io/relvo-labs/granary` images. Publishing a specific release still
requires its own qualification and owner go-ahead. This plan does not authorize
merging pull requests, deploying services, or changing production data.

## Sequence and acceptance

1. **Dependency security — issue #1.** Update vulnerable dependencies and select
   a patched Go toolchain. Preserve module/import paths and storage/S3 contracts.
   Require a clean module graph, build and vet, internal tests, and the tagged
   command-package tests. Keep pre-existing failures separate using a matching
   baseline control. Report module, imported-package and reachable-symbol
   findings separately; JSON scanner exit zero is not proof of zero findings.
   Call-graph resource qualification is tracked separately in issue #2. Obtain
   independent exact-head review before a Ready handoff.
2. **CI restoration — issue #3.** Based on the dependency checkpoint, restore
   runnable build/test/lint/security validation with the selected Go version.
   Validation may run only on explicit manual dispatch or a PR's conversion
   from Draft to Ready; no push, synchronize, ordinary PR or scheduled runs.
   Use immutable action SHAs and read-only default token permissions. Separate
   heavy integration checks from portable checks; do not silently remove tests.
   Do not use upstream-only runners, credentials or project automation.
3. **Fork documentation — issue #4.** Explain the unofficial relationship, source
   build path, maintenance boundaries and inherited upstream references. Keep
   upstream attribution, LICENSE, NOTICE and CREDITS. Do not imply vendor
   sponsorship, support or official artifacts. No artifact is advertised as
   available until it is actually published.
4. **Rename to Granary — issue #21.** In separate PRs: (a) public docs use the
   Granary name with MinIO attribution; (b) repository rename (done
   2026-11-14, GitHub redirects the old URL); (c) binary, Dockerfiles, helm and
   download/image references; (d) Go module path
   `github.com/minio/minio` → `github.com/relvo-labs/granary`, last. Keep S3
   behavior, `MINIO_*` variables, `.minio.sys`, `x-minio-*` headers and `mc`
   compatibility; `GRANARY_*` aliases may be added without removing old names.

The umbrella is issue #5. Record new verified problems in deduplicated issues;
issue creation is tracking, not proof of implementation or authority to release.
Use separate PR-scoped writers for distinct deliverables, with stacked PRs if
necessary. The next phase can use a qualified predecessor checkpoint without
claiming that it has landed on the default branch.

## Qualification and rollback

Keep implementation on non-default branches. Publish coherent checkpoints as
Draft PRs with `[skip ci]` while inherited automatic triggers remain. Inspect
all reachable workflow event types before publication; Draft status alone does
not suppress Actions. The CI phase must remove the skip marker from its final
HEAD only after its own event matrix prevents ordinary push/PR execution.

Attach actual command exits and frozen source identity outside the checkout;
keep raw scanner payloads and large logs out of version control. Reverting a
candidate before merge is straightforward; rolling back a deployed server or
storage-format migration is not qualified by these maintenance tests. No
storage-format or deployment migration is part of this plan.
