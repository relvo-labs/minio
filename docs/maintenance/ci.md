# CI trigger policy and restoration status (issue #3)

Status: **CI source restored; hosted and heavy qualification pending.** The
workflows, pinned tooling and local entrypoints below are in place and the
portable checks were run locally on the candidate source. No hosted Actions
run, heavy integration or full `-race` run is claimed by this document.

## Event matrix

| Class | Events | Workflows |
| --- | --- | --- |
| portable | `workflow_dispatch`, `pull_request` types `[ready_for_review]` | `go-lint.yml`, `go-cross.yml`, `vulncheck.yml`, `typos.yml`, `shfmt.yml`, `helm-lint.yml` |
| pr-metadata | `pull_request` types `[ready_for_review]` only | `depsreview.yaml` (needs a PR payload) |
| heavy | `workflow_dispatch` only | `go-race.yml`, `go.yml`, `go-healing.yml`, `go-resiliency.yml`, `iam-integrations.yaml`, `mint.yml`, `replication.yaml`, `root-disable.yml`, `upgrade-ci-cd.yaml` |
| removed | none | `issues.yaml`, `lock.yml` |

Push, schedule, other PR activity types, `workflow_run`, `workflow_call`,
`pull_request_target` and every other event are rejected. Existing
`branches: [master]` filters are kept; `typos.yml` and `depsreview.yaml` have
none. The two upstream administration bots (an issue-project bot using an
upstream PAT, and a scheduled thread-lock bot with `issues: write`) were
removed. `.github/stale.yml` configures the Probot Stale app; it is not an
Actions workflow and is outside this policy.

## What the portable workflows run

Every workflow calls the Makefile, so a local run uses the same commands.

| Workflow | Make targets |
| --- | --- |
| `go-lint.yml` | `ci-go-version`, `ci-policy`; `ci-build`, `ci-lint`, `ci-test`, then `ci-check-gen` last |
| `go-cross.yml` | `ci-crosscompile` |
| `vulncheck.yml` | `ci-vulncheck` |
| `typos.yml`, `shfmt.yml`, `helm-lint.yml` | `ci-typos`, `ci-shfmt`, `ci-helm-lint` |
| `go-race.yml` (manual) | `ci-test-race` |

`ci-test` is the `make test` command (`go test -v -tags kqueue,dev ./...` with
`CGO_ENABLED=0`, `MINIO_API_REQUESTS_MAX=10000`). `ci-test-race` runs the
unchanged `buildscripts/race.sh` (`GORACE=history_size=7`). The full race suite
used to run on Ready inside `go-lint.yml`; it is resource-heavy and now runs
only by manual dispatch of `go-race.yml`. The memory-heavy targets set
`GOMAXPROCS=4 GOGC=50 GOMEMLIMIT=14GiB`. `ci-vulncheck` uses text mode, which
exits nonzero when a reachable symbol is affected.

## Pins

Go: every job uses `actions/setup-go` with `go-version-file: go.mod`, which
selects the `toolchain go1.27.4` directive, and `make ci-go-version` fails if the
active Go differs.

Actions are pinned to the commit of a release tag. The SHAs come from
`git ls-remote --tags` of each canonical repository; the tags are each
repository's latest GitHub release.

| Action | Commit | Tag |
| --- | --- | --- |
| `actions/checkout` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | v7.0.1 |
| `actions/setup-go` | `b7ad1dad31e06c5925ef5d2fc7ad053ef454303e` | v7.0.0 |
| `actions/dependency-review-action` | `a1d282b36b6f3519aa1f3fc636f609c47dddb294` | v5.0.0 |

The floating `azure/setup-helm@v4`, `crate-ci/typos@master` and
`luizm/action-sh-checker@master` actions are replaced by pinned tools.
`buildscripts/ci/tools.env` holds the versions. `buildscripts/ci/install-tools.sh`
installs them under `CI_TOOLS_DIR` (default `.bin/ci`, git-ignored). It never
writes to `$GOPATH/bin`.

| Tool | Version | Verification |
| --- | --- | --- |
| golangci-lint | 2.15.0 | release archive SHA-256 from the release `checksums.txt` |
| typos | 1.52.0 | release asset SHA-256 |
| Helm | 3.22.1 | `get.helm.sh` `.sha256sum` |
| govulncheck | v1.9.0 | `go install`, checked against sum.golang.org |
| shfmt | v3.14.1 | `go install`, checked against sum.golang.org |
| PyYAML | 6.0.3 | wheel hash must be in `requirements-ci.txt`, which lists the PyPI digests |

## Enforced policy

`buildscripts/ci/workflow_policy.py` fails closed. Its narrow stdlib grammar
accepts only the canonical block form of `on`. A strict PyYAML 6.0.3 load must
agree with that parse. The load rejects duplicate keys, YAML 1.1 booleans,
anchors, tags and multiple documents. The inventory is exact, and run/no-run
results are simulated from the parsed events.

These are errors, not warnings:

- Permissions are exactly `contents: read` at top level, and read-only per job.
- The concurrency group is
  `${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}`
  with cancel-in-progress. A newer Ready run cancels an older one for the same
  PR. Manual runs never share a group.
- Runners are GitHub-hosted (`ubuntu-latest`, `ubuntu-24.04`). There are no
  containers and no reusable or local actions.
- Every `uses:` line is `owner/repo@<sha> # vX.Y.Z` and matches the pin table.
- Checkout uses `persist-credentials: false`.
- setup-go accepts only `go-version-file: go.mod` and the cache options. A job
  that sets up Go must run a target that checks the toolchain.
- No `secrets.` references, no `@latest`/`@master`/`@main` and no pipe-to-shell
  in run steps.
- The Make targets of each portable workflow and of `go-race.yml` are exact.
  Every target exists in the Makefile. Race targets must not run in a portable
  workflow.
- In the repository, the go.mod toolchain is go1.27.4. The PyYAML pin agrees in
  `tools.env` and `requirements-ci.txt`. Each checksummed tool version has an
  amd64 and arm64 hash.

`make ci-policy` installs the pinned PyYAML and runs the validator and its
tests. It sets `CI_EXPECT_PYYAML_DIR`, so a different `yaml` module on the path
fails.

## Local reproduction (Linux amd64/arm64)

Use Go 1.27.4 first on `PATH` with `GOTOOLCHAIN=local`. Set
`CI_TOOLS_DIR=/some/scratch/dir` to keep tools outside the checkout.

```sh
make ci-tools            # all pinned tools, checksum-verified
make ci-policy           # workflow policy + adversarial tests
make ci-go-version ci-build ci-lint ci-test ci-vulncheck
make ci-shfmt ci-typos ci-helm-lint ci-crosscompile
make ci-test-race        # heavy: full -race suite
make ci-check-gen        # rewrites generated files and go.mod/go.sum: use a scratch clone
```

Without `make`, use a pinned PyYAML in a virtualenv:

```sh
python3 -m venv /tmp/ci-venv
/tmp/ci-venv/bin/pip install --require-hashes --only-binary=:all: -r buildscripts/ci/requirements-ci.txt
/tmp/ci-venv/bin/python buildscripts/ci/workflow_policy.py
/tmp/ci-venv/bin/python -m unittest discover -s tests/ci -p 'test_*.py' -v
```

The heavy Makefile targets (`verify*`, `test-iam`, `test-replication`, etc.)
build through `install-race`. It copies `./granary` to `$(GOPATH)/bin/granary`, so
run them only on a disposable runner or with a scratch `GOPATH`.

`ci-build` produces `./granary`; check `./granary --version` and
`./granary --help`. The root Dockerfile compiles this checkout with Go 1.27.4.
`ci-helm-lint` uses `helm/granary`; its server image tag `unpublished` is a
placeholder because the Granary image is not yet published. Provisioning jobs
retain the separate upstream `mc` client image. Self-update and upstream update
notifications are disabled; install replacement Granary builds manually.

The single root Dockerfile replaces `Dockerfile.release`,
`Dockerfile.release.old_cpu`, `Dockerfile.hotfix`, `Dockerfile.cicd` and
`Dockerfile.scratch`: upstream downloads, inherited server images and prebuilt
binary recipes no longer describe a source build. `docker-buildx.sh` was an
upstream signing/publishing script and was removed. The upstream chart archive
`helm-releases/`, `index.yaml`, `helm-reindex.sh`, `CNAME` and `_config.yml` were
removed because they only supported publishing MinIO charts. Hotfix publishing
Make targets fail explicitly. No publication is part of these local checks.

## Known failures and pending gates

- **Spelling (#12).** `make ci-typos` (typos 1.52.0, unchanged `.typos.toml`)
  had six inherited findings. It now passes after six comment and local-name
  fixes:
  - two comment typos in `cmd/bucket-replication.go` and
    `cmd/site-replication.go`
  - local `sterr` renamed to `statErr` in `cmd/metacache-walk.go`
  - test-local `cpy` renamed to `itemCopy` in
    `internal/s3select/simdj/reader_amd64_test.go`
- **Stringer output is not checked (#13, deferred).** `go generate` with the
  pinned `golang.org/x/tools` rewrites 19 committed `*_string.go` files. The
  same happens on the unchanged base. The new `String()` methods differ in
  their int conversion, bounds check and index expressions, so the change is
  not proven equivalent. `check-gen` still inspects only `_gen.go` and `go.sum`,
  so it passes; `ci-check-gen` lists the drift. These files are not claimed to
  match their generator. Regenerating them and widening the check are deferred
  to #13.
- **Full race (#11).** An earlier local full run was killed by a 4 GiB cgroup
  OOM. A sharded diagnostic run also hit order-sensitive failures in `cmd`. The
  race gate is incomplete, not passed.
- **Lint (#10).** Fifteen files received formatting and
  `fmt.Fprintf`/dead-assignment fixes. With them, golangci-lint 2.15.0 reports
  0 issues with the unchanged `.golangci.yml`. The `gomodguard` linter is
  deprecated in this release; it stays enabled and its warning is visible.
- **govulncheck** reports one module-level advisory (GO-2026-5932,
  `golang.org/x/crypto/openpgp`), which is not imported. No imported-package or
  reachable-symbol findings. Nothing is suppressed.
- **Heavy workflows were not executed.** They use public upstream images
  (`quay.io/minio/*`, `docker.io/minio/mint:edge`, etcd, nginx), the public
  `minio/minio-iam-testing` repository and public release downloads. `mint.yml`
  now runs on a hosted runner with these job-local prerequisites:
  - a `docker-compose` shim
  - `/etc/hosts` entries for the nginx endpoints
  - a locally built `s3-check-md5`
  - an image that overlays this checkout's `granary` binary and entrypoint on the tag-pinned
    `RELEASE.2023-08-29T23-07-35Z` image

  Cross-compilation, hosted runs and an independent review are also pending.
