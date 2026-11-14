# Granary — unofficial maintenance fork of MinIO

**UNOFFICIAL, independent fork:** Granary ([relvo-labs/granary](https://github.com/relvo-labs/granary))
is derived from [minio/minio](https://github.com/minio/minio) and is not
affiliated with or endorsed by MinIO, Inc. MinIO is a trademark of MinIO, Inc.;
it is used here only to identify the upstream origin.

Granary is an S3-compatible object store. It inherits the upstream MinIO server
source for storing objects in buckets and accessing them through S3 clients.
The rename ([#21](https://github.com/relvo-labs/granary/issues/21)) uses the
`granary` binary and `github.com/relvo-labs/granary` Go module path. Inherited
`MINIO_*` environment variables and S3 behavior remain compatible.
The upstream [minio/minio](https://github.com/minio/minio) repository is archived.
This fork focuses on source maintenance, dependency security, CI and source
compatibility. It offers no newly certified feature set, performance guarantee
or enterprise support service.

**Maintenance qualification is in progress.** CI implementation is in place,
but hosted evidence and broader qualification are not confirmed here. A local
source build or smoke test does not qualify production use, distributed storage,
migrations or recovery. See the [CI runbook](docs/maintenance/ci.md) for gates
and known boundaries, including the [stringer coverage gap (#13)](https://github.com/relvo-labs/granary/issues/13),
manual heavy integrations and pending hosted evidence.

As of **2026-11-14**, this fork has no GitHub releases. Planned releases will use
`vYYYY.MM.DD` tags and `docker.io/neil0628/granary` images; none are published
yet. Upstream `minio/minio` releases and images are not Granary releases. Build
the checked-out fork source to evaluate it locally. The maintainer-host image
script and manual dry-run workflow are described in the [release runbook](docs/maintenance/release.md).

[Build and try locally](#build-and-try-locally) ·
[Find your next step](#find-your-next-step) ·
[Security reporting](SECURITY.md) · [Contribute](CONTRIBUTING.md)

## Build and try locally

This is a single-server local smoke test with synthetic data. Use a disposable
checkout and an empty temporary directory, never an existing storage directory.
The server binds only to loopback; the browser console is disabled for this test.

### 1. Select and build the fork source

Prerequisites: Linux, Git, a Go toolchain, Python 3 (port check and random
credentials), and curl. [go.mod](go.mod) declares minimum language **Go 1.26.0**
and preferred toolchain **Go 1.27.4**. Use Go 1.27.4 on `PATH` for this example.
The build uses two Go processors and a 3 GiB Go memory target; these are runtime
tuning values, not a hard memory cap or deployment sizing guidance.

Start with a clean checkout from this fork:

```sh
git clone https://github.com/relvo-labs/granary.git granary
cd granary
git rev-parse HEAD
```

Select the branch or commit you intend to evaluate before building. Unmerged
maintenance candidates may be on non-default branches; the default branch need
not contain them. Record the full commit ID with your local results.

```sh
go version
mkdir -p .bin
CGO_ENABLED=0 GOMAXPROCS=2 GOGC=50 GOMEMLIMIT=3GiB GOTOOLCHAIN=local \
  go build -o .bin/granary .
```

This creates a local executable, not a published fork artifact. The binary is
named `granary`; the Go module path is `github.com/relvo-labs/granary`.
Build the selected checkout as above. Installation from the new module path
at `@latest` requires this migration to land on `master`; it has not been
verified here.

### 2. Start an isolated local server

In the same terminal, check that TCP port **19000** is free on both loopback
families (IPv4 and IPv6, when available). If it is occupied,
choose another free integer port and replace `19000` in the check, server and
health commands. Do not stop an existing service to free it.

```sh
python3 - <<'PYPORT'
import errno
import socket
for family, host in [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]:
    try:
        with socket.socket(family) as listener:
            listener.bind((host, 19000))
    except OSError as error:
        if family != socket.AF_INET6 or error.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
            raise
print("Loopback port 19000 is free")
PYPORT
```

Create fresh local state and long random test credentials. Keep them private;
do not paste credentials, personal data or real host details into issues.
Run these commands in one shell and leave the server in the foreground:

```sh
umask 077
demo=$(mktemp -d "${TMPDIR:-/tmp}/minio-fork-demo.XXXXXXXX")
mkdir -p "$demo/home" "$demo/config" "$demo/data"
root_user=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
root_password=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
env -i PATH="$PATH" HOME="$demo/home" \
  MINIO_ROOT_USER="$root_user" MINIO_ROOT_PASSWORD="$root_password" \
  MINIO_BROWSER=off GOMAXPROCS=2 GOGC=50 GOMEMLIMIT=3GiB \
  .bin/granary --quiet --config-dir "$demo/config" server \
  --address 127.0.0.1:19000 "$demo/data"
```

The fresh HOME, config and data directories isolate this example from your
normal server configuration. `env -i` also excludes inherited MinIO settings.
The generated credentials are for this temporary local server only.

### 3. Observe the first success

From a second terminal on the same machine, run:

```sh
curl --noproxy '*' --fail --silent --show-error \
  --retry 20 --retry-connrefused --retry-delay 1 --max-time 2 \
  --output /dev/null --write-out '%{http_code}\n' \
  http://127.0.0.1:19000/minio/health/ready
```

Expected output: **`200`**, with exit status zero. This confirms that the local
server answers its readiness endpoint. It does not test object operations or
the browser UI. If it fails, inspect the local server output and verify the
chosen port; a successful compile alone is not a running-server check.

For S3 integration work, use a client against `http://127.0.0.1:19000` with the
generated credentials available in the first shell. Independently verify your
required create, upload, download and delete operations with synthetic data.
Avoid exposing this HTTP example beyond the local machine.

### 4. Stop and remove temporary data

Press **Ctrl-C in the server terminal** and wait for it to exit. Only then, in
that same shell, remove the temporary directory created above:

```sh
unset root_user root_password
case "$demo" in
  "${TMPDIR:-/tmp}"/minio-fork-demo.*) rm -rf -- "$demo" ;;
esac
unset demo
```

This deletes the example's data and configuration. Keep any data you need
elsewhere before cleanup. The checkout and `.bin/granary` remain local files;
you can remove the disposable checkout separately after finishing evaluation.

## Find your next step

| Task | Start here |
| --- | --- |
| Evaluate the local server | [Build and try locally](#build-and-try-locally) |
| Integrate an S3 client | [Upstream minio-go SDK](https://github.com/minio/minio-go); verify against your selected fork revision |
| Report a reproducible, non-sensitive bug | [Fork Issues](https://github.com/relvo-labs/granary/issues), with commit ID and a synthetic reproduction |
| Report a suspected vulnerability | [Private vulnerability report](https://github.com/relvo-labs/granary/security/advisories/new); see the [security policy](SECURITY.md) |
| Contribute a change | [Contribution guide](CONTRIBUTING.md) and [maintenance plan](docs/maintenance/plan.md) |
| Reproduce validation | [Canonical CI runbook](docs/maintenance/ci.md), including prerequisites and incomplete gates |

## Inherited references

The root [Dockerfile](Dockerfile) builds `granary` from this checkout with Go
1.27.4. Build and try a local image with:

```sh
docker build -t granary-local:dev .
docker run --rm granary-local:dev --version
```

The [Helm chart](helm/granary) defaults to
`docker.io/neil0628/granary:unpublished`. **The Granary image is not yet
published**; override the placeholder with an image you built and made
available to your cluster. No binary or image release is advertised yet.
Self-update and background update checks are disabled; install a Granary
release manually when one is available, or rebuild from the selected source.
No `minio` compatibility executable is installed.

The [docs tree](docs) still contains inherited upstream guidance. Historical
test images, upstream client downloads, enterprise/AIStor documentation, sales
links and Slack are upstream references, not this fork's distribution or
support channels. They are not qualified fork deployment instructions.
The upstream [documentation project](https://github.com/minio/docs) is also
historical context; check applicability to the exact source you build.

## Attribution and licensing

Granary is derived from MinIO, which originated with MinIO, Inc. Upstream copyright and licensing notices are
retained in [LICENSE](LICENSE), [NOTICE](NOTICE), [CREDITS](CREDITS) and the source
files. MinIO source is licensed under the GNU AGPLv3; see the preserved license.

The inherited README separately identifies MinIO [documentation](docs) as
licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
That upstream documentation attribution is retained here; this statement does
not relicense all files in this repository. See existing file notices and the
inherited [License Compliance](COMPLIANCE.md) document. Its vendor commercial
licensing references are upstream context, not an offer from this fork.
