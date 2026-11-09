# CI trigger policy and restoration status (issue #3)

Status: **safety checkpoint only.** The event matrix below is enforced and
tested locally. It does not restore CI. The inherited job bodies are unchanged and
unqualified (see "Pending stage 2").

## Event matrix

| Class | Events | Workflows |
| --- | --- | --- |
| portable | `workflow_dispatch`, `pull_request` types `[ready_for_review]` | `go-lint.yml`, `go-cross.yml`, `vulncheck.yml`, `typos.yml`, `shfmt.yml`, `helm-lint.yml` |
| pr-metadata | `pull_request` types `[ready_for_review]` only | `depsreview.yaml` (needs a PR payload) |
| heavy | `workflow_dispatch` only | `go.yml`, `go-healing.yml`, `go-resiliency.yml`, `iam-integrations.yaml`, `mint.yml`, `replication.yaml`, `root-disable.yml`, `upgrade-ci-cd.yaml` |
| removed | none | `issues.yaml`, `lock.yml` |

The portable workflows build, lint, run unit tests, scan for vulnerabilities, or check
formatting and spelling on a hosted runner with no external services. The heavy
workflows start local multi-node clusters, Docker Compose stacks, or service containers.
Some also download released binaries or check out `minio/minio-iam-testing`.
`mint.yml` uses the upstream self-hosted `mint` runner. The two upstream
administration bots were removed rather than retargeted. One added issues to
the upstream `miniohq` project using an upstream PAT. The other locked threads
on a daily schedule with `issues: write`.

Existing `branches: [master]` filters are kept because the integration PR targets
`master`. `typos.yml` and `depsreview.yaml` had no branch filter, and none was added.
Push, schedule, ordinary PR activity types, `workflow_run`, `workflow_call`,
`pull_request_target` and every other event are rejected.

`.github/stale.yml` configures the Probot Stale app. It is not an Actions workflow
and is outside this policy.

## Local check

```sh
python3 buildscripts/ci/workflow_policy.py [--json report.json]
python3 -m unittest discover -s tests/ci -p 'test_*.py' -v
```

The validator fails closed. Its narrow stdlib grammar accepts only the canonical
block form of `on`. A strict PyYAML 6.0.3 load must agree with that parse. This load
rejects duplicate keys, YAML 1.1 `on`/`yes` booleans, anchors, tags and multiple
documents. The inventory is exact: any unclassified, missing or removed-admin workflow
fails. Run/no-run results are simulated from the parsed events. The canonical CI
entrypoint must install the same pinned PyYAML. This is pending stage 2.

## Pending stage 2 (not done)

- Go 1.27.3 parity for all jobs (inherited `go-version: 1.24.x`).
- Pin all actions to verified full commit SHAs. Floating `@v4`, `@v5` and
  `@master` refs are reported as warnings.
- Read-only default permissions everywhere (`typos.yml` has none).
- Replace the upstream-only `mint` runner with a runnable job.
- Fix the concurrency group for manual runs.
- Add a pinned linter install. `make getdeps` currently pulls the installer from
  `master`.
- Run real build/test/lint/security commands with recorded exits and full local
  proof, then a fresh independent review.
- Heavy workflows stay manual and unqualified until they are actually run.
