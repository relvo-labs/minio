# Working on this unofficial fork

Before changing files, read `docs/maintenance/plan.md` and
`.agent/skills/fork-maintenance/SKILL.md`, then the relevant source and tests.
This repository is `relvo-labs/granary` (Granary, formerly `relvo-labs/minio`),
not the upstream vendor repository. The Go module path is
`github.com/relvo-labs/granary`.

Use the Granary name, clearly identify the project as an unofficial fork derived
from MinIO, and preserve upstream copyright, license and notice files. Never
imply endorsement. Follow the phased rename in issue #21; keep `MINIO_*`
variables, `.minio.sys`, `x-minio-*` headers and `mc` compatibility.

Use issue-linked, non-default branches and Draft checkpoints. Do not merge,
publish releases/images, deploy or change repository settings without explicit
owner authority. CI execution is manual or on conversion to Ready only; Draft
status is not itself a CI safeguard. Track verified problems in issues.

Use `Lei-k <qwe17235@gmail.com>` as both commit author and committer via
per-process environment variables, not repository/global Git configuration.
Never put credentials or operational data in tests, logs or public issues.
