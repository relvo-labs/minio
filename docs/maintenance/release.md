# Docker Hub image release (#39)

Granary is an unofficial fork derived from MinIO, without upstream endorsement.
As of 2026-11-14, **no Granary image has been published**. The planned image is
`docker.io/neil0628/granary`; Helm keeps the `unpublished` placeholder until a
real release is qualified. Neil's 2026-11-14 decision in #39 replaces the GHCR
proposal with maintainer-host publishing. Every specific release still needs
owner go-ahead; the first release also waits for #25 to merge.

## Prerequisites and source identity

Use a Linux maintainer host with Bash, Git, Python 3, curl, tar, timeout, Docker Engine,
buildx and the exact Go toolchain in go.mod. The host must execute linux/amd64
and linux/arm64 containers, using binfmt/QEMU for its non-native architecture.
The script executes both actual Granary binaries; advertised buildx platform
support alone does not pass verification. Allow enough disk, memory and time
for two Go builds (the Dockerfile currently sets GOMAXPROCS=8).

The checkout must be clean, including untracked files. HEAD must equal the
optional `--commit` full SHA (otherwise the script selects current HEAD).
The script verifies that origin is exactly
`https://github.com/relvo-labs/granary.git` and freshly fetches master from that
explicit URL, including full history in shallow
clones, and requires HEAD to be an ancestor of or equal to that ref. This lets
the owner qualify a fixed master commit while master advances. An unmerged
feature branch is rejected, including when selected for manual Actions.
When `latest` exists, HEAD must also be a descendant of or equal to its
`org.opencontainers.image.revision`. Both platform revisions and version labels
must agree. The labels are read with the publisher's Docker context from the
resolved immutable `latest` digest; missing or malformed labels abort.
Build context comes from `git archive` of that exact commit, excluding ignored
local files. No branch switch, git tag or GitHub Release is made by the script.

VERSION must be a real calendar date in `vYYYY.MM.DD` form within today UTC
plus or minus one day. `docker buildx imagetools inspect` uses the publisher's
Docker context to check the candidate tag and `latest`; only an explicit
manifest-unknown or reference-specific not-found response establishes absence.
Authentication, network and unknown failures abort. An anonymous Docker Hub
API 404 never substitutes for this check, because it can hide private tags.
VERSION must be newer than the authenticated `latest` version and every tag
in the anonymous tag listing. All tag pages are checked; if `latest` exists
but anonymous enumeration returns 404, publication aborts. Explicit absence
of both candidate and `latest` permits first publication. These tag, version
and revision checks repeat just before pushing.

Run only one publisher at a time. Hub manifest writes have no atomic
create-if-absent operation; these checks cannot prevent another publisher
racing between the last check and push. The owner must serialize publication
and protect version tags against writes by other publishers. Never overwrite
an existing version tag, even to recover a failed release.

## Version mapping

`vYYYY.MM.DD` is the image tag and OCI `version` label, not a timestamp passed
as `cmd.Version`. `gen-ldflags.go` expects a UTC RFC3339 timestamp and derives
`PREFIX.YYYY-MM-DDTHH-MM-SSZ[.SUFFIX]` as its binary release tag. The script
passes the commit's UTC committer time as `COMMIT_TIME`, the full HEAD SHA as
`COMMIT_ID`, `MINIO_RELEASE=RELEASE` and `MINIO_HOTFIX=vYYYY.MM.DD`.
For example, commit time `2026-11-14T10:04:42Z` and image `v2026.11.15` produce
`RELEASE.2026-11-14T10-04-42Z.v2026.11.15` in `granary --version`.

`cmd.Version` remains the commit timestamp, preserving `make build` and the
Unix timestamp used by `xl.meta`'s `WrittenByVersion`. The release script checks
the generator's exit and essential flags directly, avoiding Make's silent
empty-LDFLAGS failure (#38). Both platform banners must then report the exact
release tag, full commit and matching runtime architecture. OCI `created` is
the same deterministic commit time; revision is the full commit. Source, title
and description identify this unofficial MinIO-derived fork. The release
license label is `AGPL-3.0-or-later`, following LICENSE and the source notices'
“version 3 ... or ... any later version” grant. Attribution files are retained.

## Dry run

On the selected clean master commit, with a currently unused date:

```sh
make release-image VERSION=v2026.11.15
# Equivalent, with an optional exact source check:
bash buildscripts/release-image.sh --dry-run --commit "$(git rev-parse HEAD)" v2026.11.15
```

The default is dry run. It creates a task-scoped docker-container buildx
builder with `--driver-opt image=moby/buildkit@sha256:c5f8862f52499773af85bc07695b2379ef08e8002352b0df2c4864c91d78dabe`,
builds and locally loads each platform, and runs `granary --version`
without network access or storage mounts. Cleanup removes only its own local
image tags and builder, including that builder's cache; it never prunes globally.
Each Docker cleanup command has a 30-second timeout and 5-second kill grace;
failure of one removal does not skip the others. Cleanup ignores further INT
and TERM signals and preserves the original failure or interruption status.
The BuildKit pin is the multi-arch index resolved on 2026-11-14 with an empty
Docker config using `docker buildx imagetools inspect docker.io/moby/buildkit:buildx-stable-1`.
No registry login or push occurs. An interruption that bypasses traps (such as
SIGKILL) may require removing the printed task builder and its image tags by
their exact names. Never use global prune for cleanup.

`release-dryrun.yml` is manual-only with no inputs or secrets, read-only token
permissions and pinned actions. Select master for the dispatch; it uses the
current UTC date as VERSION. A used date deliberately fails closed. It registers
QEMU on the disposable GitHub runner with a Docker Hub binfmt index pinned by
digest (read anonymously on 2026-11-14), then calls the same dry-run Make target.
Docker buildx is supplied by the runner and checked before use. No hosted run
or completed two-platform build is claimed by this runbook.

## Publish and read back (owner-authorized maintainer host only)

Docker Hub credentials must already be configured on the host, outside the
checkout. The Docker Hub repository must be **public**; check its visibility
before publishing, including for the first release. Set `DOCKER_CONFIG` to
that host-local directory if necessary. The
script never logs in, reads credential files or prints credential values, and
does not send them into the build context or Actions. Do not enable shell
tracing or record credentials in evidence. After owner go-ahead, use the same
qualified clean source and date:

```sh
bash buildscripts/release-image.sh --push --commit "$(git rev-parse HEAD)" v2026.11.15
```

After both local architecture checks pass, the script builds the multi-arch
index from the same source and cached layers and pushes `:v2026.11.15` and
`:latest`. `--metadata-file` captures `containerimage.digest` from the push.
The version tag must then be readable through both the anonymous Hub API
and a buildx manifest inspect with a fresh empty Docker config. A failed
anonymous read aborts with an instruction to set the repository public.
The script reads back both tags with `docker buildx imagetools inspect`,
requires both linux/amd64 and linux/arm64 in each manifest, and compares both
index digests and the anonymous digest with the captured pushed digest.
Missing metadata or any mismatch fails. Independent read-back commands are:

```sh
docker buildx imagetools inspect docker.io/neil0628/granary:v2026.11.15
docker buildx imagetools inspect docker.io/neil0628/granary:latest
```

Keep command exits, both version banners, source SHA, version and final digest
as private local evidence outside the checkout. If a push or read-back fails,
stop and inspect both remote tags: publication is not transactional. Preserve
any version tag that exists; ask the owner to approve recovery of `latest`
against the verified digest instead of rerunning or replacing a version tag.
After the first successful release, prepare a separately reviewed docs/Helm
update replacing the `unpublished` placeholder with the actual version/digest.
Git tags and release notes, if desired, remain separate owner-authorized work.
