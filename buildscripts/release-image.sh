#!/usr/bin/env bash
# Granary is an unofficial fork derived from MinIO.
# SPDX-License-Identifier: AGPL-3.0-or-later
# Host-local release path (#39). Never logs in; uses the caller's DOCKER_CONFIG.
set -euo pipefail

die() {
	echo "release-image: $*" >&2
	exit 1
}
usage() { echo "usage: $0 [--dry-run|--push] [--commit FULL_SHA] vYYYY.MM.DD"; }
push=0
commit=
version=${VERSION:-}
while (($#)); do
	case $1 in
	--dry-run) push=0 ;;
	--push) push=1 ;;
	--commit)
		(($# >= 2)) || die "--commit requires a full SHA"
		commit=$2
		shift
		;;
	--help)
		usage
		exit 0
		;;
	-*) die "unknown option: $1" ;;
	*)
		[[ -z $version ]] || die "provide VERSION once"
		version=$1
		;;
	esac
	shift
done
[[ $version =~ ^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}$ ]] || die "VERSION must match vYYYY.MM.DD"
python3 -I - "$version" <<'PY' || die "VERSION must be a valid calendar date"
import datetime, sys
datetime.date.fromisoformat(sys.argv[1][1:].replace('.', '-'))
PY
for tool in git go docker curl tar; do
	command -v "$tool" >/dev/null || die "required tool missing: $tool"
done
cd "$(git rev-parse --show-toplevel)"
state=$(git status --porcelain=v1 --untracked-files=all --ignore-submodules=none) || die "cannot inspect worktree"
[[ -z $state ]] || die "clean worktree required (including untracked files)"
head=$(git rev-parse HEAD)
commit=${commit:-$head}
[[ $commit =~ ^[0-9a-f]{40}$ && $head == "$commit" ]] || die "HEAD must equal --commit (full SHA)"
# Fetch the entire master history on shallow Actions checkouts too. An ancestor
# is allowed so the owner can qualify a fixed commit while master moves ahead.
fetch_args=(--no-tags)
if [[ $(git rev-parse --is-shallow-repository) == true ]]; then
	fetch_args+=(--unshallow)
fi
git fetch "${fetch_args[@]}" origin +refs/heads/master:refs/remotes/origin/master || die "fresh origin/master fetch failed"
git merge-base --is-ancestor "$commit" refs/remotes/origin/master || die "HEAD must be on freshly fetched origin/master"
[[ $(go env GOVERSION) == "go$(sed -n 's/^toolchain go//p' go.mod)" ]] || die "Go must match the go.mod toolchain"
commit_time=$(git log -1 --format=%ct "$commit")
commit_time=$(
	python3 -I - "$commit_time" <<'PY'
import datetime, sys
print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)
release_tag="RELEASE.${commit_time//:/-}.$version"
# Do not use Make's $(shell ...) LDFLAGS: its failure can silently become empty
# (#38). Check the generator's exit AND its essential output before any build.
ldflags=$(MINIO_RELEASE=RELEASE MINIO_HOTFIX="$version" go run buildscripts/gen-ldflags.go "$commit_time" "$commit") || die "version stamping failed"
python3 -I - "$ldflags" "$commit_time" "$commit" "$release_tag" <<'PY' || die "version stamping produced incomplete flags"
import shlex, sys
flags = shlex.split(sys.argv[1])
for name, value in zip(('Version', 'CommitID', 'ReleaseTag'), sys.argv[2:]):
    assert 'github.com/relvo-labs/granary/cmd.' + name + '=' + value in flags
PY

image=docker.io/neil0628/granary
hub=https://hub.docker.com/v2/repositories/neil0628/granary/tags
scratch=$(mktemp -d "${TMPDIR:-/tmp}/granary-release.XXXXXXXX")
builder="granary-release-${scratch##*.}"
builder_created=0
images=()
cleanup() {
	local rc=$?
	trap - EXIT
	for local_image in "${images[@]}"; do
		docker image rm "$local_image" >/dev/null || rc=1
	done
	if ((builder_created)); then
		docker buildx rm "$builder" >/dev/null || rc=1
	fi
	rm -rf -- "$scratch"
	exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Anonymous Hub API, independent of Docker's host credentials. Disable curlrc,
# netrc and redirects; unknown HTTP statuses, malformed JSON and outages fail.
hub_get() {
	status=$(curl -q --silent --show-error --proto '=https' --connect-timeout 15 --max-time 60 \
		--output "$scratch/hub.json" --write-out '%{http_code}' "$1") || die "anonymous Docker Hub query failed"
	case $status in
	200) ;;
	404)
		python3 -I - "$scratch/hub.json" <<'PY' || die "unrecognized Docker Hub 404 response"
import json, sys
data = json.load(open(sys.argv[1]))
assert 'not found' in data['message'].lower()
PY
		;;
	*) die "anonymous Docker Hub query returned HTTP $status" ;;
	esac
}
check_tags() {
	local next pages=0
	hub_get "$hub/$version"
	[[ $status == 404 ]] || die "version tag $version already exists; never overwrite it"
	next="$hub?page_size=100"
	while [[ -n $next ]]; do
		((++pages <= 100)) || die "too many Docker Hub tag pages"
		hub_get "$next"
		if [[ $status == 404 ]]; then
			((pages == 1)) || die "Docker Hub tag pagination disappeared"
			break # Repository may not exist before release one.
		fi
		next=$(
			python3 -I - "$scratch/hub.json" "$version" "$hub" <<'PY'
import json, re, sys
data = json.load(open(sys.argv[1]))
assert isinstance(data['results'], list)
for tag in data['results']:
    name = tag['name']
    if re.fullmatch(r'v[0-9]{4}\.[0-9]{2}\.[0-9]{2}', name) and name >= sys.argv[2]:
        sys.exit('release-image: version must be newer than every published version tag: ' + name)
next_page = data['next']
assert next_page is None or (isinstance(next_page, str) and next_page.startswith(sys.argv[3] + '?'))
print(next_page or '')
PY
		) || die "cannot establish that VERSION is the newest unused release"
	done
}
check_tags
docker buildx version >/dev/null || die "Docker buildx required"
mkdir "$scratch/context"
git archive "$commit" | tar -x -C "$scratch/context"
docker buildx create --name "$builder" --driver docker-container >/dev/null
builder_created=1
docker buildx inspect --builder "$builder" --bootstrap
build_args=(--builder "$builder" --progress plain --provenance=false
	--build-arg "COMMIT_ID=$commit" --build-arg "COMMIT_TIME=$commit_time"
	--build-arg MINIO_RELEASE=RELEASE --build-arg "MINIO_HOTFIX=$version"
	--label org.opencontainers.image.source=https://github.com/relvo-labs/granary
	--label "org.opencontainers.image.revision=$commit"
	--label "org.opencontainers.image.version=$version"
	--label "org.opencontainers.image.created=$commit_time"
	--label org.opencontainers.image.licenses=AGPL-3.0-or-later
	--label 'org.opencontainers.image.title=Granary, unofficial fork derived from MinIO'
	--label 'org.opencontainers.image.description=Unofficial S3 object storage fork derived from MinIO; no upstream endorsement')
for arch in amd64 arm64; do
	local_image="granary-release-check:${scratch##*.}-$arch"
	images+=("$local_image")
	docker buildx build "${build_args[@]}" --platform "linux/$arch" --load --tag "$local_image" "$scratch/context"
	# Running the real target binary verifies native execution or host binfmt/QEMU.
	# No storage mount, network or login is needed for --version.
	docker run --rm --network none --platform "linux/$arch" --entrypoint /usr/bin/granary "$local_image" --version >"$scratch/version" || die "linux/$arch could not execute granary --version (check binfmt/QEMU)"
	cat "$scratch/version"
	python3 -I - "$scratch/version" "$commit" "$release_tag" "$arch" <<'PY' || die "linux/$arch version verification failed (check binfmt/QEMU)"
import pathlib, re, sys
output = pathlib.Path(sys.argv[1]).read_text()
banner = re.fullmatch(r'granary version ([^\s]+) \(commit-id=([0-9a-f]{40})\)', output.splitlines()[0])
assert banner and banner.groups() == (sys.argv[3], sys.argv[2])
assert 'DEVELOPMENT.GOGET' not in output
assert re.search(r'^Runtime: \S+ linux/' + sys.argv[4] + r'$', output, re.M)
PY
done
if ((! push)); then
	echo "Dry run complete: $image:$version, commit $commit; both platforms verified; nothing pushed."
	exit 0
fi
# Recheck immediately before publication. The maintainer must serialize releases;
# Hub does not provide an atomic create-if-absent manifest PUT (see runbook).
check_tags
docker buildx build "${build_args[@]}" --platform linux/amd64,linux/arm64 \
	--tag "$image:$version" --tag "$image:latest" --push "$scratch/context"
for tag in "$version" latest; do
	docker buildx imagetools inspect "$image:$tag"
	docker buildx imagetools inspect --raw "$image:$tag" >"$scratch/$tag.manifest"
	docker buildx imagetools inspect --format '{{json .Manifest}}' "$image:$tag" >"$scratch/$tag.descriptor"
done
python3 -I - "$scratch" "$version" <<'PY'
import json, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
digests = []
for tag in (sys.argv[2], 'latest'):
    manifest = json.loads((root / (tag + '.manifest')).read_text())
    platforms = {(m['platform']['os'], m['platform']['architecture']) for m in manifest['manifests']}
    assert {('linux', 'amd64'), ('linux', 'arm64')} <= platforms, platforms
    digest = json.loads((root / (tag + '.descriptor')).read_text())['digest']
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
    digests.append(digest)
assert digests[0] == digests[1], 'latest and VERSION digests differ'
print('Published VERSION and latest digest: ' + digests[0])
PY
