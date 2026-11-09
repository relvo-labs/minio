# Unofficial fork maintenance plan

Version: 0.1.0

This plan covers the initial maintenance of `relvo-labs/minio`. The owner has
selected keeping the MinIO name with a prominent unofficial-fork disclaimer.
It does not authorize publishing releases, binaries or container images, merging
pull requests, deploying services, or changing production data.

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
   upstream attribution, LICENSE, NOTICE and CREDITS. Retain the MinIO name;
   do not imply vendor sponsorship, support or official artifacts. Release and
   artifact publishing remain undecided and must not be advertised as available.

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
