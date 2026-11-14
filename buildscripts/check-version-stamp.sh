#!/usr/bin/env bash
# Granary is an unofficial fork derived from MinIO.
#
# Fail unless BINARY --version reports COMMIT (full or 12-character short form)
# and no DEVELOPMENT.GOGET placeholder.  A mistyped -X package path in the
# ldflags is silently ignored by the Go linker, so check the result (#34).
#
# Usage: check-version-stamp.sh BINARY COMMIT

set -euo pipefail

if [ "$#" -ne 2 ]; then
	echo "usage: $0 BINARY COMMIT" >&2
	exit 2
fi
binary=$1
commit=$2

if [[ ! $commit =~ ^[0-9a-f]{40}$ ]]; then
	echo "version stamp: expected a full 40-character commit ID, got '$commit'" >&2
	exit 1
fi

if ! version=$("$binary" --version 2>&1); then
	echo "version stamp: '$binary --version' failed:" >&2
	echo "$version" >&2
	exit 1
fi

if [[ $version == *DEVELOPMENT.GOGET* ]]; then
	echo "version stamp: '$binary' is not stamped (DEVELOPMENT.GOGET):" >&2
	echo "$version" >&2
	exit 1
fi

if [[ $version != *"$commit"* && $version != *"${commit:0:12}"* ]]; then
	echo "version stamp: '$binary' does not report commit $commit:" >&2
	echo "$version" >&2
	exit 1
fi

echo "version stamp OK: $(head -n1 <<<"$version")"
