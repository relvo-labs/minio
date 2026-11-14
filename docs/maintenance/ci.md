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
| heavy | `workflow_dispatch` only | `go-race.yml`, `go.yml`, `go-healing.yml`, `go-resiliency.yml`, `iam-integrations.yaml`, `mint.yml`, `replication.yaml`, `root-disable.yml`, `upgrade-ci-cd.yaml`, `release-dryrun.yml` |
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
| `release-dryrun.yml` (manual) | `ci-go-version`, `release-image` (both image platforms; no push) |

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

The root module and linker symbol prefix are `github.com/relvo-labs/granary`.
Independent modules under `docs/debugging` retain their inherited module paths
and Go pins because they do not import the root module (#27).

`ci-build` produces `./granary` and then fails unless the `(commit-id=...)`
field of `./granary --version` is exactly the full `git rev-parse HEAD` commit
and there is no `DEVELOPMENT.GOGET` placeholder (#34); the commit elsewhere in
the output, such as in the binary name, does not count. The Go linker silently
ignores an `-X` flag with a wrong package path, so the result is checked rather
than the flags. `make check-version-stamp VERSION_STAMP_BIN=/path/to/granary` runs the
same check, `buildscripts/check-version-stamp.sh`, on any binary.

The root Dockerfile compiles this checkout with Go 1.27.4. `.git` is not in
its build context, so `make docker` passes the commit ID and commit time from
the `make build` ldflags (plus `MINIO_RELEASE`/`MINIO_HOTFIX`) as build args.
The build stage runs `buildscripts/gen-ldflags.go` with them, so the image
binary carries the same `Version`, `ReleaseTag`, `CommitID`, `ShortCommitID`
and `CopyrightYear` as `make build`, and then runs the version check (#29).
`gen-ldflags.go` rejects a commit ID other than 40 lowercase hex digits and a
`MINIO_RELEASE`/`MINIO_HOTFIX` other than up to 64 `[A-Za-z0-9._-]` characters
starting with a letter or digit, so build metadata cannot add linker flags; the
Docker build also fails if they are given without the commit args. A
plain `docker build .` without these args still builds, with the
`DEVELOPMENT.GOGET` placeholders of an unstamped build. `Version` also sets the
`WrittenByVersion` of new `xl.meta` versions; an unstamped binary records `0`
there, as a `go build` binary does.

Both base images are pinned by multi-arch index digest, with the tag in a
comment. The digests were read on 2026-11-14 with anonymous
`docker buildx imagetools inspect docker.io/library/<tag>` and confirmed by the
`Docker-Content-Digest` header of an anonymous registry `HEAD` request:

| Tag | Index digest |
| --- | --- |
| `golang:1.27.4-bookworm` | `sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea` |
| `debian:bookworm-slim` | `sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc` |

`ci-helm-lint` uses `helm/granary`; its server image tag `unpublished` is a
placeholder because the Docker Hub `docker.io/neil0628/granary` image is not
yet published. The [release runbook](release.md) describes host-local
publishing and the manual dry-run workflow; neither has publication authority.
Provisioning jobs retain the separate upstream `mc` client image. Self-update and upstream update
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
- **Heavy qualification remains partial.** Full hosted integration workflows,
  cross-compilation and independent review remain pending. The image restoration
  and disabled historical scenarios are described below.

## Integration images and unavailable baselines (#30)

Granary is an unofficial fork derived from MinIO. Integration tests build the
server under test from this checkout; they do not pull upstream MinIO server
images. `mint.yml` runs the root multi-stage Dockerfile directly:

```sh
SHA_SHORT=$(git rev-parse --short HEAD)
docker build -t "granary-ci:${SHA_SHORT}" -f Dockerfile .
```

This build passes no version args, so the test image reports
`DEVELOPMENT.GOGET`. Use `make docker` for a stamped image and check it with:

```sh
docker run --rm "<image>" --version
```

The root Dockerfile compiles Granary with Go 1.27.4 using the public Go and
Debian base images. The local `granary-ci` name has no registry prefix and is
never published. The former staged context / `.dockerignore` workaround and
upstream-image overlay in `mint.yml` are removed. The root `.dockerignore`
still excludes generated binaries and other unsuitable source-build inputs.

All four `mint/*.yaml` modes use that local tag with `pull_policy: never`.
Their readiness checks request `/minio/health/cluster` through bash TCP sockets;
the source-built image does not bundle `mc` or curl. This endpoint permits
anonymous GET requests and returns 503 until the object layer, bucket metadata
and IAM are initialized and every erasure set has write quorum. `run-mint.sh`
waits for healthy servers before starting the suite or stopping pool nodes.
It defaults to full Mint coverage;
for a reduced local check, use `MINT_MODE=core MINT_TESTS=aws-sdk-go` before
calling `.github/workflows/run-mint.sh erasure minio minio123 "$SHA_SHORT"`
from the repository root. Test credentials here are public fixtures only.
Each run uses `granary-mint-${SHA_SHORT}` as its Compose project and removes
its own containers, network and volumes on exit. Workflow cleanup also handles
interrupted runs and removes the local server image. No global prune is used
in the active Mint path.

The upstream **Mint S3 conformance client** remains in use, pinned to
`quay.io/minio/mint@sha256:f76b1148ebbab2c536d20599317b5a068f6da26b9b325f27caab10800f805730`.
Digest source: anonymous `docker buildx imagetools inspect quay.io/minio/mint:edge`
on 2026-11-14 (Docker v2 manifest, linux/amd64). This is the test client, not
a server baseline. This pin is separate from the immutable Actions/tool policy.
The nginx proxy and IAM auxiliary `quay.io/minio/openldap` and
`quay.io/minio/dex` images are unchanged. Helm's upstream `mc` client image
is outside #30 and remains tracked separately.

Anonymous registry probes on 2026-11-14 found upstream server images
unavailable (Quay returned 401 for upstream server tags; Docker Hub returned
404 for `minio/minio:latest`). These historical scenarios are explicitly
skipped with #30 messages in workflows; direct script and Make entrypoints
fail before downloads, builds, Docker operations or cleanup:

| Scenario | Required historical baseline | Entry point |
| --- | --- | --- |
| Multipart migration / healing after upgrade | `RELEASE.2023-08-29T23-07-35Z` image | `multipart/migrate.sh` in `mint.yml` |
| Storage upgrade / checksum regression | `RELEASE.2019-12-19T22-52-26Z` image | `minio-upgrade.sh`, `make test-upgrade`, `upgrade-ci-cd.yaml` |
| LDAP IAM export from old server / import into Granary | `RELEASE.2024-03-26T22-10-45Z` server binary baseline | `minio-iam-ldap-upgrade-import-test.sh`, `make test-iam-ldap-upgrade-import`, `iam-integrations.yaml` |

The regression logic is retained behind fail-fast guards for future restoration
with qualified historical artifacts. Substituting the current checkout for the
old server would invalidate the upgrade test. The multipart and upgrade
Compose files now accept only local `granary-ci` tags and never pull a server;
they remain usable as source-built fixtures, but do not provide historical
upgrade coverage. The dormant historical release/download references in the
guarded scripts describe the required baselines, not runnable dependencies.
The independent source-based multipart quorum, IAM, replication and Mint
scenarios remain enabled; the pre-existing Mint resiliency workflow step
remains commented out because PUT deadlines are unresolved.
