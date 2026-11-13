# Security policy for the unofficial MinIO fork

[relvo-labs/minio](https://github.com/relvo-labs/minio) is an independent,
**unofficial fork**, not affiliated with or endorsed by MinIO, Inc.
This policy concerns this fork's source and dependencies.

## Maintenance scope

The fork focuses on source maintenance, dependency security, CI and
compatibility. As of **2026-11-09**, it has no GitHub releases and no published
supported-version matrix. Artifact publishing and tag policy remain undecided.
Upstream releases and images do not establish a supported fork version.
There is no guaranteed response time, fix schedule or enterprise support SLA.
The [CI runbook](docs/maintenance/ci.md) records validation boundaries;
successful local checks do not certify production security.

## Reporting limitation: no approved private channel

As of **2026-11-09**, GitHub private vulnerability reporting is **disabled** for
this fork, and no dedicated fork security contact has been approved.
There is currently no approved private submission route documented here.
Channel activation is an owner action tracked in
[issue #4](https://github.com/relvo-labs/minio/issues/4)
(`OWNER_ACTION_REQUIRED:minio-private-reporting`).

You may ask a **non-sensitive contact-channel question** in
[this fork's Issues](https://github.com/relvo-labs/minio/issues).
Do **not** post undisclosed exploit details, credentials, logs containing
personal data, or real host/customer details in public issues, pull requests
or comments. Keep sensitive material private while an approved channel is
unavailable; a public channel question must not include a vulnerability payload.

Inherited upstream email addresses, Slack and vendor policies are not this
fork's security reporting channels. Do not send fork findings to
`security@min.io` as a substitute for a fork contact.

## Preparing information

The [vulnerability report guidance](VULNERABILITY_REPORT.md) describes what to
prepare privately for a future approved channel. It does not activate a
reporting service or promise a disclosure process. For ordinary non-sensitive
bugs, use the fork issue tracker with a synthetic reproduction and exact
source commit; follow [CONTRIBUTING.md](CONTRIBUTING.md).
