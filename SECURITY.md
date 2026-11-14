# Security policy for Granary (unofficial MinIO fork)

Granary ([relvo-labs/granary](https://github.com/relvo-labs/granary)) is an
independent, **unofficial fork** of MinIO, not affiliated with or endorsed by
MinIO, Inc. This policy concerns this fork's source and dependencies.

## Maintenance scope

The fork focuses on source maintenance, dependency security, CI and
compatibility. As of **2026-11-14**, it has no GitHub releases and no published
supported-version matrix. Planned releases will use `vYYYY.MM.DD` tags; until
one exists, fixes land on the default branch only.
Upstream MinIO releases and images do not establish a supported fork version.
There is no guaranteed response time, fix schedule or enterprise support SLA.
The [CI runbook](docs/maintenance/ci.md) records validation boundaries;
successful local checks do not certify production security.

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub private vulnerability
reporting: open
[Security → Report a vulnerability](https://github.com/relvo-labs/granary/security/advisories/new)
in this repository. Only you and the fork maintainers can see the report.

Do **not** post undisclosed exploit details, credentials, logs containing
personal data, or real host/customer details in public issues, pull requests
or comments.

Inherited upstream email addresses, Slack and vendor policies are not this
fork's security reporting channels. Do not send fork findings to
`security@min.io`.

## Preparing information

The [vulnerability report guidance](VULNERABILITY_REPORT.md) describes what a
useful report contains. For ordinary non-sensitive bugs, use the fork issue
tracker with a synthetic reproduction and exact source commit; follow
[CONTRIBUTING.md](CONTRIBUTING.md).
