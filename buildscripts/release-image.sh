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
python3 -I - "$version" <<'PY' || die "VERSION must be a valid calendar date within today UTC +/-1 day"
import datetime, sys
date = datetime.date.fromisoformat(sys.argv[1][1:].replace('.', '-'))
today = datetime.datetime.now(datetime.timezone.utc).date()
assert abs((date - today).days) <= 1
PY
for tool in git go docker curl tar timeout; do
	command -v "$tool" >/dev/null || die "required tool missing: $tool"
done
cd "$(git rev-parse --show-toplevel)"
state=$(git status --porcelain=v1 --untracked-files=all --ignore-submodules=none) || die "cannot inspect worktree"
[[ -z $state ]] || die "clean worktree required (including untracked files)"
head=$(git rev-parse HEAD)
commit=${commit:-$head}
[[ $commit =~ ^[0-9a-f]{40}$ && $head == "$commit" ]] || die "HEAD must equal --commit (full SHA)"
[[ $(git remote get-url origin) == https://github.com/relvo-labs/granary.git ]] || die "origin must be exactly https://github.com/relvo-labs/granary.git"
# Fetch the entire master history on shallow Actions checkouts too. An ancestor
# is allowed so the owner can qualify a fixed commit while master moves ahead.
fetch_args=(--no-tags)
if [[ $(git rev-parse --is-shallow-repository) == true ]]; then
	fetch_args+=(--unshallow)
fi
git fetch "${fetch_args[@]}" https://github.com/relvo-labs/granary.git +refs/heads/master:refs/remotes/origin/master || die "fresh origin/master fetch failed"
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
# buildx-stable-1 multi-arch index, resolved anonymously on 2026-11-14.
buildkit_image=moby/buildkit@sha256:c5f8862f52499773af85bc07695b2379ef08e8002352b0df2c4864c91d78dabe
hub=https://hub.docker.com/v2/repositories/neil0628/granary/tags
scratch=$(mktemp -d "${TMPDIR:-/tmp}/granary-release.XXXXXXXX")
builder="granary-release-${scratch##*.}"
builder_created=0
images=()
cleanup() {
	trap '' INT TERM
	local rc=$1
	trap - EXIT
	for local_image in "${images[@]}"; do
		timeout --kill-after=5s 30s docker image rm "$local_image" >/dev/null || { ((rc != 0)) || rc=1; }
	done
	if ((builder_created)); then
		timeout --kill-after=5s 30s docker buildx rm "$builder" >/dev/null || { ((rc != 0)) || rc=1; }
	fi
	rm -rf -- "$scratch"
	exit "$rc"
}
trap 'cleanup "$?"' EXIT
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
	local next latest_digest latest_revision pages=0
	# Always use the publisher's Docker context. Anonymous 404s also hide
	# private repositories and cannot establish that a tag is absent.
	if authenticated_inspect "$image:$version"; then
		die "version tag $version already exists; never overwrite it"
	fi
	if authenticated_inspect "$image:latest"; then
		latest_digest=$(
			python3 -I - "$scratch/auth.descriptor" <<'PY'
import json, re, sys
digest = json.load(open(sys.argv[1]))['digest']
assert re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
print(digest)
PY
		) || die "cannot read current latest digest"
		# Pin this label read to the descriptor we just resolved.
		docker buildx imagetools inspect --format '{{json .Image}}' "$image@$latest_digest" >"$scratch/latest.config" || die "authenticated lookup failed for latest labels"
		latest_revision=$(
			python3 -I - "$scratch/latest.config" "$version" <<'PY'
import json, re, sys
configs = json.load(open(sys.argv[1]))
labels = [configs[p]['config']['Labels'] for p in ('linux/amd64', 'linux/arm64')]
revisions = {c['org.opencontainers.image.revision'] for c in labels}
versions = {c['org.opencontainers.image.version'] for c in labels}
assert len(revisions) == len(versions) == 1, 'inconsistent latest labels'
revision, version = revisions.pop(), versions.pop()
assert re.fullmatch(r'[0-9a-f]{40}', revision)
assert re.fullmatch(r'v[0-9]{4}\.[0-9]{2}\.[0-9]{2}', version)
assert sys.argv[2] > version, 'VERSION must be newer than current latest'
print(revision)
PY
		) || die "cannot validate latest labels; VERSION must be newer than current latest"
		git merge-base --is-ancestor "$latest_revision" "$commit" || die "HEAD must be a descendant of current latest revision (or equal)"
	fi
	hub_get "$hub/$version"
	[[ $status == 404 ]] || die "version tag $version already exists; never overwrite it"
	next="$hub?page_size=100"
	while [[ -n $next ]]; do
		((++pages <= 100)) || die "too many Docker Hub tag pages"
		hub_get "$next"
		if [[ $status == 404 ]]; then
			die "Docker Hub repository neil0628/granary must exist and be public before publishing (including the first release); cannot enumerate existing tags"
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
# Return 1 only for an explicit, reference-specific registry not-found or
# manifest-unknown response. Auth, transport and unknown errors are fatal.
authenticated_inspect() {
	local ref=$1
	if docker buildx imagetools inspect --format '{{json .Manifest}}' "$ref" >"$scratch/auth.descriptor" 2>"$scratch/auth.error"; then
		return 0
	fi
	python3 -I - "$scratch/auth.error" "$ref" <<'PY' || die "authenticated lookup failed for $ref; cannot establish tag absence"
import pathlib, re, sys
error = pathlib.Path(sys.argv[1]).read_text().strip()
assert re.fullmatch(r'(?:ERROR: )?(?:' + re.escape(sys.argv[2]) +
                    r': (?:not found|manifest unknown)|manifest unknown(?:: manifest unknown)?)', error)
PY
	return 1
}
docker buildx version >/dev/null || die "Docker buildx required"
# Hosted dry runs need no publisher credentials or release-tag availability.
# Publication must pass every registry check before creating a builder.
if ((push)); then
	check_tags
fi
mkdir "$scratch/context"
git archive "$commit" | tar -x -C "$scratch/context"
docker buildx create --name "$builder" --driver docker-container --driver-opt "image=$buildkit_image" >/dev/null
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
	--tag "$image:$version" --tag "$image:latest" --metadata-file "$scratch/push.metadata" --push "$scratch/context"
# No caller credentials or credential helpers are available to this read.
mkdir "$scratch/anonymous-docker"
if ! (
	hub_get "$hub/$version"
	[[ $status == 200 ]]
) ||
	! DOCKER_CONFIG="$scratch/anonymous-docker" docker buildx imagetools inspect --format '{{json .Manifest}}' "$image:$version" >"$scratch/anonymous.descriptor"; then
	die "post-push anonymous read failed; set the Docker Hub repository public and verify $image:$version"
fi
for tag in "$version" latest; do
	docker buildx imagetools inspect "$image:$tag"
	docker buildx imagetools inspect --raw "$image:$tag" >"$scratch/$tag.manifest"
	docker buildx imagetools inspect --format '{{json .Manifest}}' "$image:$tag" >"$scratch/$tag.descriptor"
done
python3 -I - "$scratch" "$version" <<'PY'
import json, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
pushed = json.loads((root / 'push.metadata').read_text())['containerimage.digest']
assert re.fullmatch(r'sha256:[0-9a-f]{64}', pushed), 'invalid pushed digest'
anonymous = json.loads((root / 'anonymous.descriptor').read_text())['digest']
assert anonymous == pushed, 'anonymous read-back and pushed digest differ'
digests = []
for tag in (sys.argv[2], 'latest'):
    manifest = json.loads((root / (tag + '.manifest')).read_text())
    platforms = {(m['platform']['os'], m['platform']['architecture']) for m in manifest['manifests']}
    assert {('linux', 'amd64'), ('linux', 'arm64')} <= platforms, platforms
    digest = json.loads((root / (tag + '.descriptor')).read_text())['digest']
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
    digests.append(digest)
assert digests[0] == digests[1], 'latest and VERSION digests differ'
assert digests[0] == pushed, 'read-back and pushed digest differ'
print('Published VERSION and latest digest: ' + digests[0])
PY
