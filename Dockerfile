# Granary is an unofficial fork derived from MinIO. Build this checkout only.
FROM golang:1.27.4-bookworm AS build
WORKDIR /src
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOMAXPROCS=8
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG TARGETOS=linux
ARG TARGETARCH
ARG VERSION=development
ARG COMMIT_ID=unknown
RUN test "$(go env GOVERSION)" = "go$(sed -n 's/^toolchain go//p' go.mod)" && \
    GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -tags kqueue -trimpath \
    -ldflags "-s -w -X github.com/relvo-labs/granary/cmd.ReleaseTag=${VERSION} -X github.com/relvo-labs/granary/cmd.CommitID=${COMMIT_ID} -X github.com/relvo-labs/granary/cmd.CopyrightYear=$(date -u +%Y)" \
    -o /out/granary .

FROM debian:bookworm-slim
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
