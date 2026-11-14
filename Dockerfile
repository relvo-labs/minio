# Granary is an unofficial fork derived from MinIO. Build this checkout only.
# golang:1.27.4-bookworm (multi-arch index digest)
FROM golang:1.27.4-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS build
WORKDIR /src
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOMAXPROCS=8
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG TARGETOS=linux
ARG TARGETARCH
# Build metadata, passed by `make docker` (.git is not in the build context):
# the full commit ID, its UTC RFC3339 commit time (cmd.Version) and the
# optional release prefix/suffix that gen-ldflags.go reads from the
# environment.  With them the binary is stamped exactly like `make build` and
# the build fails unless `granary --version` reports COMMIT_ID.  Without them
# it keeps the DEVELOPMENT.GOGET placeholders of an unstamped build, and a
# release prefix/suffix is an error.  Each value is checked as a whole string
# (no trailing lines) before it becomes part of the linker flags.
ARG COMMIT_ID=
ARG COMMIT_TIME=
ARG MINIO_RELEASE=
ARG MINIO_HOTFIX=
RUN set -eu; \
    test "$(go env GOVERSION)" = "go$(sed -n 's/^toolchain go//p' go.mod)"; \
    if [ -n "${COMMIT_ID}${COMMIT_TIME}" ]; then \
        { test "${#COMMIT_ID}" -eq 40 && case "${COMMIT_ID}" in *[!0-9a-f]*) false ;; esac; } || \
            { echo "COMMIT_ID must be a full 40-character commit ID" >&2; exit 1; }; \
        case "${COMMIT_TIME}" in \
            [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ;; \
            *) echo "COMMIT_TIME must be a UTC RFC3339 time (YYYY-MM-DDTHH:MM:SSZ)" >&2; exit 1 ;; \
        esac; \
        ldflags="$(go run buildscripts/gen-ldflags.go "${COMMIT_TIME}" "${COMMIT_ID}")"; \
    else \
        test -z "${MINIO_RELEASE}${MINIO_HOTFIX}" || \
            { echo "MINIO_RELEASE/MINIO_HOTFIX need COMMIT_ID and COMMIT_TIME (see make docker)" >&2; exit 1; }; \
        ldflags="-s -w -X github.com/relvo-labs/granary/cmd.CopyrightYear=$(date -u +%Y)"; \
    fi; \
    GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -tags kqueue -trimpath -ldflags "${ldflags}" -o /out/granary .; \
    if [ -n "${COMMIT_ID}" ]; then bash buildscripts/check-version-stamp.sh /out/granary "${COMMIT_ID}"; fi

# debian:bookworm-slim (multi-arch index digest)
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*
LABEL org.opencontainers.image.title="Granary" \
      org.opencontainers.image.description="Unofficial object storage fork derived from MinIO" \
      org.opencontainers.image.source="https://github.com/relvo-labs/granary" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV MINIO_ACCESS_KEY_FILE=access_key \
    MINIO_SECRET_KEY_FILE=secret_key \
    MINIO_ROOT_USER_FILE=access_key \
    MINIO_ROOT_PASSWORD_FILE=secret_key \
    MINIO_KMS_SECRET_KEY_FILE=kms_master_key \
    MINIO_CONFIG_ENV_FILE=config.env
COPY --from=build /out/granary /usr/bin/granary
COPY LICENSE NOTICE CREDITS /licenses/
COPY --chmod=755 dockerscripts/docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["granary", "server", "/data"]
