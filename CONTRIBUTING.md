# Granary contribution guide — unofficial MinIO fork

This guide is for Granary ([relvo-labs/granary](https://github.com/relvo-labs/granary)),
an independent, unofficial maintenance fork of MinIO, not affiliated with or
endorsed by MinIO, Inc. Route contributions and non-sensitive bug reports to this fork.
Report suspected vulnerabilities privately as described in
[SECURITY.md](SECURITY.md), not in public issues.

## Development Workflow

Read [AGENTS.md](AGENTS.md), the [maintenance plan](docs/maintenance/plan.md)
and the [fork-maintenance skill](.agent/skills/fork-maintenance/SKILL.md)
before changing files. Link work to a
[fork issue](https://github.com/relvo-labs/granary/issues), use a non-default
branch and open a Draft checkpoint. Preserve upstream notices and compatibility
boundaries. Keep credentials and operational data out of reports and tests.

### Set up your Granary GitHub repository

Fork [relvo-labs/granary](https://github.com/relvo-labs/granary/fork) to your own
repository and clone that fork. For a local source build and first readiness
check, follow the [README](README.md#build-and-try-locally). The module path
remains `github.com/minio/minio`; installing that path at `@latest` fetches
upstream rather than this fork.

### Create your feature branch

Before making code changes, make sure you create a separate branch for these changes

```
git checkout -b docs/issue-4-description
```

### Test server changes

Add relevant tests for code changes. Use the canonical
[CI runbook](docs/maintenance/ci.md#local-reproduction-linux-amd64arm64)
for actual Make targets, pinned-tool setup, Go 1.27.4 selection and resource
preconditions. It also documents incomplete gates, including stringer coverage
(#13) and manual heavy integrations. Run generation checks in a scratch clone:
they rewrite files. Heavy targets can install into `GOPATH/bin`, so follow the
runbook's disposable-runner or scratch-`GOPATH` requirement.

Record the exact source commit, command exits and limitations. A successful
source build does not establish production or release acceptance.

### Commit changes

After verification, human contributors commit using their own existing Git
identity. For repository automation and agent-generated commits, follow the
maintainer identity instruction in [AGENTS.md](AGENTS.md) using per-process
environment variables.

```
git commit -m 'docs: clarify fork guidance (issue #4)'
```

### Push to the branch

Push your locally committed changes to the remote origin (your fork)

```
git push origin docs/issue-4-description
```

### Create a Pull Request

Open a Draft pull request against the agreed base branch; stacked maintenance
work may target a predecessor branch. Draft status alone is not a CI safeguard.
Validation runs only by manual dispatch or conversion to Ready, subject to the
[event policy](docs/maintenance/ci.md#event-matrix). Keep the checkpoint Draft
until qualification and independent review of its exact HEAD are complete.
Merge, release/image publication, deployment and repository settings changes
require explicit owner authority.

## FAQs

### How does Granary manage dependencies?

Granary uses `go mod` to manage its dependencies.

- Run `go get foo/bar` in the source folder to add the dependency to `go.mod` file.

To remove a dependency

- Edit your code and remove the import reference.
- Run `go mod tidy` in the source folder to remove dependency from `go.mod` file.

### What are the coding guidelines for Granary?

Follow Go style and the repository's existing conventions. Refer to the Go
project's [code review comments](https://go.dev/wiki/CodeReviewComments).
Discuss actionable changes in this fork's issues or pull requests.
