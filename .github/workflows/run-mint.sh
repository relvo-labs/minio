#!/bin/bash

set -e

export MODE="$1"
export ACCESS_KEY="$2"
export SECRET_KEY="$3"
export JOB_NAME="$4"
export MINT_MODE="${MINT_MODE:-full}"
export COMPOSE_PROJECT_NAME="granary-mint-${JOB_NAME}"
# quay.io/minio/mint:edge manifest, verified 2026-11-14; see docs/maintenance/ci.md.
mint_image=quay.io/minio/mint@sha256:f76b1148ebbab2c536d20599317b5a068f6da26b9b325f27caab10800f805730
mint_tests=()
if [ -n "${MINT_TESTS:-}" ]; then
	read -r -a mint_tests <<<"${MINT_TESTS}"
fi

cd .github/workflows/mint
compose_file="minio-${MODE}.yaml"
mint_name="mint-${MODE}-${JOB_NAME}"

cleanup() {
	docker rm -f "${mint_name}" 2>/dev/null || true
	docker compose -f "${compose_file}" down --volumes --remove-orphans
}
trap cleanup EXIT

docker pull "${mint_image}"
docker compose -f "${compose_file}" up -d --wait --wait-timeout 120

# Stop two nodes, one of each pool, while preserving quorum.
if [ "${MODE}" == "pools" ]; then
	docker compose -f "${compose_file}" stop minio2 minio6
fi

# Pause one node to check that all S3 calls work while one node goes wrong.
if [ "${MODE}" == "resiliency" ]; then
	docker compose -f "${compose_file}" pause minio4
fi

docker run --rm --net="${COMPOSE_PROJECT_NAME}_default" \
	--name="${mint_name}" \
	-e SERVER_ENDPOINT="nginx:9000" \
	-e ACCESS_KEY="${ACCESS_KEY}" \
	-e SECRET_KEY="${SECRET_KEY}" \
	-e ENABLE_HTTPS=0 \
	-e MINT_MODE="${MINT_MODE}" \
	"${mint_image}" "${mint_tests[@]}"
