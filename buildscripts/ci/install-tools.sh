#!/usr/bin/env bash
#
# Pinned, checksum-verified CI tool installer for the unofficial
# relvo-labs/granary fork (issue #3).  Used identically by local runs and the
# GitHub workflows.  Nothing is installed globally.
#
# Usage: install-tools.sh TOOLS_DIR TOOL...
#   TOOL: golangci-lint govulncheck shfmt typos helm pyyaml all
#
# Release binaries are verified against SHA-256 values taken from the
# upstream release metadata (see docs/maintenance/ci.md).  Go tools are
# installed with "go install module@version", which verifies the module
# against go.sum data from sum.golang.org.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=buildscripts/ci/tools.env
. "${here}/tools.env"

die() {
	echo "install-tools: $*" >&2
	exit 1
}

[ "$#" -ge 2 ] || die "usage: $0 TOOLS_DIR TOOL..."
tools_dir="$1"
shift
mkdir -p "${tools_dir}"
tools_dir="$(cd "${tools_dir}" && pwd)"

[ "$(uname -s)" = "Linux" ] || die "only Linux is supported"
case "$(uname -m)" in
x86_64 | amd64) arch=amd64 ;;
aarch64 | arm64) arch=arm64 ;;
*) die "unsupported architecture $(uname -m)" ;;
esac

# sha256 NAME-VERSION-ARCH
pinned_sha256() {
	case "$1" in
	golangci-lint-2.15.0-amd64) echo 9b7fe6a6bf6334b98decf8d86b11415ea7889afdabdd1959fdce23af2a9ebcea ;;
	golangci-lint-2.15.0-arm64) echo b4d8f17068b35ee6060cc2f69a16fb48740d706fbba81661c318649c114a6998 ;;
	typos-1.52.0-amd64) echo 6155d4114f9a0f5b8f6d4fda96266c9d5f6add71df80da7f684ee5e72eb14588 ;;
	typos-1.52.0-arm64) echo 63b7d4478d467a61fe47cdb3162f720c6797856e61a04f7e125d1cdc282488b8 ;;
	helm-3.22.1-amd64) echo 896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea ;;
	helm-3.22.1-arm64) echo 4c29e286b279d170fa048ddfc3e4e83dcd069ca9c52102766ba672d7149e694d ;;
	*) die "no pinned checksum for $1" ;;
	esac
}

# fetch URL SHA256 DEST
fetch() {
	local tmp="$3.partial.$$"
	curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "${tmp}" "$1" || {
		rm -f "${tmp}"
		die "download failed: $1"
	}
	if ! echo "$2  ${tmp}" | sha256sum -c --status -; then
		rm -f "${tmp}"
		die "checksum mismatch: $1"
	fi
	mv "${tmp}" "$3"
}

# extract_one ARCHIVE MEMBER DEST
extract_one() {
	local dir
	dir="$(mktemp -d)"
	tar -xzf "$1" -C "${dir}" "$2"
	install -m 0755 "${dir}/$2" "$3"
	rm -rf "${dir}"
}

install_golangci_lint() {
	local v="${GOLANGCI_LINT_VERSION}" dest="${tools_dir}/golangci-lint/${GOLANGCI_LINT_VERSION}"
	if [ ! -x "${dest}/golangci-lint" ]; then
		mkdir -p "${dest}"
		fetch "https://github.com/golangci/golangci-lint/releases/download/v${v}/golangci-lint-${v}-linux-${arch}.tar.gz" \
			"$(pinned_sha256 "golangci-lint-${v}-${arch}")" "${dest}/archive.tar.gz"
		extract_one "${dest}/archive.tar.gz" "golangci-lint-${v}-linux-${arch}/golangci-lint" "${dest}/golangci-lint"
		rm -f "${dest}/archive.tar.gz"
	fi
	"${dest}/golangci-lint" version | grep -q "version ${v} " || die "golangci-lint version mismatch"
	echo "golangci-lint ${v}: ${dest}/golangci-lint"
}

install_typos() {
	local v="${TYPOS_VERSION}" dest="${tools_dir}/typos/${TYPOS_VERSION}" triple
	case "${arch}" in
	amd64) triple=x86_64-unknown-linux-musl ;;
	arm64) triple=aarch64-unknown-linux-musl ;;
	esac
	if [ ! -x "${dest}/typos" ]; then
		mkdir -p "${dest}"
		fetch "https://github.com/crate-ci/typos/releases/download/v${v}/typos-v${v}-${triple}.tar.gz" \
			"$(pinned_sha256 "typos-${v}-${arch}")" "${dest}/archive.tar.gz"
		extract_one "${dest}/archive.tar.gz" "./typos" "${dest}/typos"
		rm -f "${dest}/archive.tar.gz"
	fi
	[ "$("${dest}/typos" --version)" = "typos-cli ${v}" ] || die "typos version mismatch"
	echo "typos ${v}: ${dest}/typos"
}

install_helm() {
	local v="${HELM_VERSION}" dest="${tools_dir}/helm/${HELM_VERSION}"
	if [ ! -x "${dest}/helm" ]; then
		mkdir -p "${dest}"
		fetch "https://get.helm.sh/helm-v${v}-linux-${arch}.tar.gz" \
			"$(pinned_sha256 "helm-${v}-${arch}")" "${dest}/archive.tar.gz"
		extract_one "${dest}/archive.tar.gz" "linux-${arch}/helm" "${dest}/helm"
		rm -f "${dest}/archive.tar.gz"
	fi
	"${dest}/helm" version --template '{{.Version}}' | grep -qx "v${v}" || die "helm version mismatch"
	echo "helm ${v}: ${dest}/helm"
}

# go_tool NAME MODULE PACKAGE VERSION
go_tool() {
	local dest="${tools_dir}/$1/$4"
	if [ ! -x "${dest}/$1" ]; then
		mkdir -p "${dest}"
		(cd "${dest}" && GOBIN="${dest}" GOFLAGS=-mod=mod GONOSUMDB= GOPRIVATE= GOINSECURE= \
			GOSUMDB=sum.golang.org go install "$3@$4")
	fi
	go version -m "${dest}/$1" | grep -Eq "^[[:space:]]+mod[[:space:]]+$2[[:space:]]+$4[[:space:]]" ||
		die "$1 module version mismatch"
	echo "$1 $4: ${dest}/$1"
}

install_pyyaml() {
	local v="${PYYAML_VERSION}" dest="${tools_dir}/pyyaml/${PYYAML_VERSION}" got
	grep -qx "PyYAML==${v} \\\\" "${here}/requirements-ci.txt" || die "requirements-ci.txt does not pin PyYAML ${v}"
	if [ ! -d "${dest}/yaml" ]; then
		python3 "${here}/fetch_pyyaml.py" "${dest}"
	fi
	got="$(PYTHONPATH="${dest}" python3 -s -c 'import yaml, os, sys; print(yaml.__version__, os.path.dirname(os.path.dirname(os.path.abspath(yaml.__file__))))')"
	[ "${got}" = "${v} ${dest}" ] || die "PyYAML check failed: ${got}"
	echo "pyyaml ${v}: ${dest}"
}

for tool in "$@"; do
	case "${tool}" in
	golangci-lint) install_golangci_lint ;;
	govulncheck) go_tool govulncheck golang.org/x/vuln golang.org/x/vuln/cmd/govulncheck "${GOVULNCHECK_VERSION}" ;;
	shfmt) go_tool shfmt mvdan.cc/sh/v3 mvdan.cc/sh/v3/cmd/shfmt "${SHFMT_VERSION}" ;;
	typos) install_typos ;;
	helm) install_helm ;;
	pyyaml) install_pyyaml ;;
	all)
		install_golangci_lint
		go_tool govulncheck golang.org/x/vuln golang.org/x/vuln/cmd/govulncheck "${GOVULNCHECK_VERSION}"
		go_tool shfmt mvdan.cc/sh/v3 mvdan.cc/sh/v3/cmd/shfmt "${SHFMT_VERSION}"
		install_typos
		install_helm
		install_pyyaml
		;;
	*) die "unknown tool ${tool}" ;;
	esac
done
