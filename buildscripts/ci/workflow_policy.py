#!/usr/bin/env python3
# CI maintenance helper for the unofficial relvo-labs/granary fork; not part of
# or endorsed by upstream MinIO.  Distributed under the repository's license.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fail-closed GitHub Actions trigger policy validator (relvo-labs/granary #3).

Two independent layers must both accept every workflow:

1. A stdlib-only, intentionally narrow canonical grammar.  Top-level keys
   must be plain, unique and drawn from a fixed allow-list.  The ``on`` block
   accepts exactly one block-style shape::

       on:
         workflow_dispatch:
         pull_request:
           types:
             - ready_for_review
           branches:
             - master

   Anything else (flow/scalar forms, quoted or duplicate keys, inline
   comments, anchors, tags, odd indentation, empty lists) is rejected rather
   than ignored.  Blank and full-line comment lines never terminate a block.

2. A strict PyYAML load (exact pinned version, duplicate keys rejected,
   YAML 1.1 on/off/yes/no booleans disabled, anchors/aliases/tags/merge keys
   and multi-document streams rejected).  Its parsed top-level keys and
   ``on`` mapping must equal the canonical parse.

Run/no-run decisions are simulated from the parsed event mapping using
GitHub's documented filter semantics and compared with the per-class policy.

Job-level invariants are errors, not warnings: exact read-only top-level
permissions, per-PR/per-run concurrency, GitHub-hosted runners, third-party
actions pinned to the verified release commit with a matching tag comment,
non-persisted checkout credentials, setup-go reading the go.mod toolchain,
no secrets and no floating installs in run steps.  Repository checks bind the
go.mod toolchain, the tool pins and the Make targets the workflows call.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys

POLICY_VERSION = "2"
PYYAML_REQUIRED_VERSION = "6.0.3"
# Selected tested Go toolchain; go.mod's `toolchain` directive must equal it.
EXPECTED_GO_TOOLCHAIN = "go1.27.4"

PORTABLE = "portable"
PR_METADATA = "pr-metadata"
HEAVY = "heavy"

# Exact inventory of retained workflow files.  Every top-level *.yml/*.yaml in
# .github/workflows must appear here, and every entry must exist.
INVENTORY = {
    "depsreview.yaml": PR_METADATA,
    "go-cross.yml": PORTABLE,
    "go-lint.yml": PORTABLE,
    "helm-lint.yml": PORTABLE,
    "shfmt.yml": PORTABLE,
    "typos.yml": PORTABLE,
    "vulncheck.yml": PORTABLE,
    "go.yml": HEAVY,
    "go-healing.yml": HEAVY,
    "go-race.yml": HEAVY,
    "go-resiliency.yml": HEAVY,
    "iam-integrations.yaml": HEAVY,
    "mint.yml": HEAVY,
    "replication.yaml": HEAVY,
    "release-dryrun.yml": HEAVY,
    "root-disable.yml": HEAVY,
    "upgrade-ci-cd.yaml": HEAVY,
}

# Upstream administration bots removed by policy; they must stay absent.
REMOVED = {
    "issues.yaml": "upstream issue-project bot (miniohq project, upstream BOT_PAT)",
    "lock.yml": "upstream scheduled thread-lock bot (issues: write)",
}

# Non-workflow support material referenced by the heavy mint job.  GitHub only
# loads workflow files from the top level of .github/workflows.
SUPPORT_FILES = {"run-mint.sh", "root.cert", "root.key"}
SUPPORT_DIRS = {"mint", "multipart"}

ALLOWED_EVENTS = {
    PORTABLE: ("pull_request", "workflow_dispatch"),
    PR_METADATA: ("pull_request",),
    HEAVY: ("workflow_dispatch",),
}
READY = "ready_for_review"
ALLOWED_PR_FILTERS = ("types", "branches")
ALLOWED_BRANCH_FILTER = ["master"]
TOP_LEVEL_KEYS = (
    "name",
    "run-name",
    "on",
    "permissions",
    "concurrency",
    "env",
    "defaults",
    "jobs",
)
# GitHub's default activity types for pull_request when "types" is omitted.
DEFAULT_PR_TYPES = ("opened", "synchronize", "reopened")

PR_ACTIONS = (
    "opened",
    "synchronize",
    "reopened",
    "ready_for_review",
    "converted_to_draft",
    "closed",
    "edited",
    "labeled",
    "unlabeled",
    "assigned",
    "unassigned",
    "review_requested",
    "review_request_removed",
    "auto_merge_enabled",
    "auto_merge_disabled",
    "locked",
    "unlocked",
    "enqueued",
    "dequeued",
    "milestoned",
    "demilestoned",
)
OTHER_EVENTS = (
    "push",
    "schedule",
    "workflow_run",
    "workflow_call",
    "pull_request_target",
    "pull_request_review",
    "pull_request_review_comment",
    "repository_dispatch",
    "merge_group",
    "issues",
    "issue_comment",
    "release",
    "create",
    "delete",
    "check_run",
    "check_suite",
    "deployment",
    "status",
)
SCENARIOS = (
    [{"id": "dispatch", "event": "workflow_dispatch"}]
    + [
        {"id": "pr-%s-master" % a, "event": "pull_request", "action": a, "base": "master"}
        for a in PR_ACTIONS
    ]
    + [
        {
            "id": "pr-ready_for_review-other-base",
            "event": "pull_request",
            "action": READY,
            "base": "ci/fork-validation",
        }
    ]
    + [{"id": e, "event": e} for e in OTHER_EVENTS]
)
REQUIRED_RUNS = {
    PORTABLE: {"dispatch", "pr-ready_for_review-master"},
    PR_METADATA: {"pr-ready_for_review-master"},
    HEAVY: {"dispatch"},
}
# A Ready transition against a non-master base may run when no branch filter
# exists (stacked candidates); it is neither required nor forbidden.
OPTIONAL_RUNS = {
    PORTABLE: {"pr-ready_for_review-other-base"},
    PR_METADATA: {"pr-ready_for_review-other-base"},
    HEAVY: set(),
}

TOP_KEY_RE = re.compile(r"([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?\Z")
EVENT_RE = re.compile(r"  ([a-z][a-z_]*):\Z")
FILTER_RE = re.compile(r"    ([a-z][a-z_-]*):\Z")
ITEM_RE = re.compile(r"      - ([A-Za-z0-9_./-]+)\Z")
BLOCK_SCALAR_RE = re.compile(r"[|>][-+]?\Z")
GLOB_CHARS = set("*?[]!+")

# --- job-level invariants ---------------------------------------------------
TOP_PERMISSIONS = {"contents": "read"}
CONCURRENCY = {
    "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}",
    "cancel-in-progress": True,
}
GITHUB_HOSTED_RUNNERS = ("ubuntu-latest", "ubuntu-24.04")
# Third-party actions: repository -> (commit of the release tag, tag).  Commits
# come from `git ls-remote --tags` of the canonical repository and the GitHub
# "latest release" metadata (see docs/maintenance/ci.md).
PINNED_ACTIONS = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "actions/setup-go": ("b7ad1dad31e06c5925ef5d2fc7ad053ef454303e", "v7.0.0"),
    "actions/dependency-review-action": ("a1d282b36b6f3519aa1f3fc636f609c47dddb294", "v5.0.0"),
}
CHECKOUT = "actions/checkout"
SETUP_GO = "actions/setup-go"
# Documented actions/setup-go v7.0.0 inputs this repository may set.
SETUP_GO_INPUTS = ("go-version-file", "cache", "cache-dependency-path")
CHECKOUT_INPUTS = ("persist-credentials", "repository", "path")
USES_KEY_RE = re.compile(r"^[ ]*(?:- )?uses[ ]*:")
USES_LINE_RE = re.compile(
    r"^[ ]*(?:- )?uses: ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40}) # (v[0-9]+\.[0-9]+\.[0-9]+)\Z"
)
# Floating installs/refs and pipe-to-shell are not allowed in run steps.
FLOATING_RUN_RE = re.compile(r"@(?:latest|master|main)\b|\|[ \t]*(?:sudo[ \t]+)?(?:bash|sh)\b")
MAKE_TARGET_RE = re.compile(r"(?<![\w./-])make[ \t]+([a-z][a-z0-9-]*)")
# Targets that verify the active Go equals the go.mod toolchain.
GO_CHECKED_TARGETS = (
    "ci-go-version",
    "ci-build",
    "ci-check-gen",
    "ci-lint",
    "ci-test",
    "ci-test-race",
    "ci-vulncheck",
    "ci-crosscompile",
)
# Exact Make targets of the restored portable workflows and the manual race
# workflow.  The canonical race suite must not run automatically.
EXPECTED_MAKE_TARGETS = {
    "go-lint.yml": {"ci-go-version", "ci-policy", "ci-build", "ci-check-gen", "ci-lint", "ci-test"},
    "go-cross.yml": {"ci-crosscompile"},
    "vulncheck.yml": {"ci-vulncheck"},
    "typos.yml": {"ci-go-version", "ci-typos"},
    "shfmt.yml": {"ci-go-version", "ci-shfmt"},
    "helm-lint.yml": {"ci-go-version", "ci-helm-lint"},
    "depsreview.yaml": set(),
    "go-race.yml": {"ci-test-race"},
    "release-dryrun.yml": {"ci-go-version", "release-image"},
}
MANUAL_ONLY_TARGETS = ("ci-test-race", "test-race")
# buildscripts/ci/tools.env keys that need a checksum per architecture in
# install-tools.sh (Go tools are verified through sum.golang.org instead).
CHECKSUMMED_TOOLS = {
    "GOLANGCI_LINT_VERSION": "golangci-lint",
    "TYPOS_VERSION": "typos",
    "HELM_VERSION": "helm",
}
TOOL_ARCHES = ("amd64", "arm64")


class Result(object):
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


def _check_top_value(lineno, key, value, res):
    if value == "":
        return
    first = value[0]
    if first in "&*!%@`":
        res.err("line %d: unsupported anchor/alias/tag/reserved value for %r" % (lineno, key))
    elif first in "\"'":
        if len(value) < 2 or not value.endswith(first):
            res.err("line %d: multi-line or unterminated quoted value for %r" % (lineno, key))
    elif first == "[":
        if not value.endswith("]"):
            res.err("line %d: multi-line flow sequence for %r" % (lineno, key))
    elif first == "{":
        if not value.endswith("}"):
            res.err("line %d: multi-line flow mapping for %r" % (lineno, key))
    elif first in "|>":
        if not BLOCK_SCALAR_RE.match(value):
            res.err("line %d: unsupported block scalar header for %r" % (lineno, key))


def parse_canonical(text, res):
    """Parse top-level keys and the ``on`` block with the narrow grammar.

    Returns (top_keys, events) where events maps event name to None or to a
    dict of filter name to list of items.  Errors are appended to res.
    """
    if text.startswith("﻿"):
        res.err("byte-order mark is not supported")
    if "\r" in text:
        res.err("carriage returns are not supported")
    lines = text.split("\n")
    top_keys = []
    blocks = {}
    current = None
    discarding = False  # indented lines of a rejected top-level entry
    doc_marker_allowed = True
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip(" ")
        body = line.lstrip(" \t")
        lead = line[: len(line) - len(body)]
        if "\t" in lead:
            res.err("line %d: tab in indentation" % lineno)
            continue
        if body == "" or body.startswith("#"):
            continue
        if lead:
            if discarding:
                continue
            if current is None:
                res.err("line %d: indented content before first top-level key" % lineno)
                continue
            blocks[current].append((lineno, line))
            continue
        if line == "---":
            if not doc_marker_allowed:
                res.err("line %d: multiple YAML documents are not supported" % lineno)
            doc_marker_allowed = False
            continue
        doc_marker_allowed = False
        m = TOP_KEY_RE.match(line)
        if not m:
            res.err("line %d: unsupported top-level syntax %r" % (lineno, line))
            current, discarding = None, True
            continue
        key, value = m.group(1), (m.group(2) or "").strip()
        if key not in TOP_LEVEL_KEYS:
            res.err("line %d: top-level key %r is not allowed (YAML 1.1 boolean aliases of 'on' included)" % (lineno, key))
        if key in blocks:
            res.err("line %d: duplicate top-level key %r" % (lineno, key))
            current, discarding = None, True
            continue
        top_keys.append(key)
        blocks[key] = []
        current, discarding = key, False
        if key == "on":
            if value != "":
                res.err("line %d: 'on' must be a block mapping without inline value" % lineno)
        else:
            _check_top_value(lineno, key, value, res)
    if "on" not in blocks:
        res.err("missing top-level 'on' key")
        return top_keys, None
    if "jobs" not in blocks:
        res.err("missing top-level 'jobs' key")
    events = _parse_on_block(blocks["on"], res)
    return top_keys, events


def _parse_on_block(entries, res):
    events = {}
    cur_event = None
    cur_filter = None
    skipping = False
    for lineno, line in entries:
        if "#" in line:
            res.err("line %d: inline comments are not supported inside 'on'" % lineno)
            skipping = True
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2:
            skipping = False
            cur_filter = None
            m = EVENT_RE.match(line)
            if not m:
                res.err("line %d: malformed event entry %r" % (lineno, line.strip()))
                cur_event = None
                skipping = True
                continue
            name = m.group(1)
            if name in events:
                res.err("line %d: duplicate event %r" % (lineno, name))
                cur_event = None
                skipping = True
                continue
            events[name] = None
            cur_event = name
        elif skipping:
            continue
        elif indent == 4:
            m = FILTER_RE.match(line)
            if cur_event is None or not m:
                res.err("line %d: malformed event filter %r" % (lineno, line.strip()))
                skipping = True
                continue
            key = m.group(1)
            if events[cur_event] is None:
                events[cur_event] = {}
            if key in events[cur_event]:
                res.err("line %d: duplicate filter %r under %r" % (lineno, key, cur_event))
                skipping = True
                continue
            events[cur_event][key] = []
            cur_filter = key
        elif indent == 6:
            m = ITEM_RE.match(line)
            if cur_filter is None or not m:
                res.err("line %d: malformed filter item %r" % (lineno, line.strip()))
                skipping = True
                continue
            item = m.group(1)
            items = events[cur_event][cur_filter]
            if item in items:
                res.err("line %d: duplicate item %r in %s.%s" % (lineno, item, cur_event, cur_filter))
                continue
            items.append(item)
        else:
            res.err("line %d: unsupported indentation inside 'on'" % lineno)
            skipping = True
    if not events:
        res.err("'on' declares no events")
    for name, filters in events.items():
        for key, items in (filters or {}).items():
            if not items:
                res.err("empty filter list %s.%s" % (name, key))
    return events


def check_event_policy(cls, events, res):
    allowed = ALLOWED_EVENTS[cls]
    for name in events:
        if name not in allowed:
            res.err("event %r is forbidden for class %s" % (name, cls))
    for name in allowed:
        if name not in events:
            res.err("class %s requires event %r" % (cls, name))
    if "workflow_dispatch" in events and events["workflow_dispatch"] is not None:
        res.err("workflow_dispatch must not declare inputs or other keys")
    if "pull_request" in events:
        filters = events["pull_request"] or {}
        for key in filters:
            if key not in ALLOWED_PR_FILTERS:
                res.err("pull_request filter %r is not allowed" % key)
        if filters.get("types") != [READY]:
            res.err("pull_request types must be exactly [%s], got %r" % (READY, filters.get("types")))
        if "branches" in filters and filters["branches"] != ALLOWED_BRANCH_FILTER:
            res.err("pull_request branches must be exactly %r, got %r" % (ALLOWED_BRANCH_FILTER, filters["branches"]))


def would_run(events, scenario):
    """Simulate GitHub trigger evaluation from the parsed event mapping."""
    if events is None or scenario["event"] not in events:
        return False
    if scenario["event"] != "pull_request":
        return True
    filters = events["pull_request"] or {}
    types = filters.get("types", list(DEFAULT_PR_TYPES))
    if scenario["action"] not in types:
        return False
    branches = filters.get("branches")
    if branches is not None:
        literal = [b for b in branches if not (set(b) & GLOB_CHARS)]
        if len(literal) == len(branches) and scenario["base"] not in branches:
            return False
    return True


def run_matrix(events):
    return {s["id"]: would_run(events, s) for s in SCENARIOS}


def check_matrix(cls, matrix, res):
    runs = {k for k, v in matrix.items() if v}
    missing = REQUIRED_RUNS[cls] - runs
    extra = runs - REQUIRED_RUNS[cls] - OPTIONAL_RUNS[cls]
    if missing:
        res.err("simulated triggers missing for %s: %s" % (cls, sorted(missing)))
    if extra:
        res.err("simulated forbidden triggers for %s: %s" % (cls, sorted(extra)))


# ---------------------------------------------------------------------------
# Strict PyYAML cross-check.


def _strict_loader():
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    StrictLoader.yaml_implicit_resolvers = {
        k: [(tag, rx) for (tag, rx) in v if tag != "tag:yaml.org,2002:bool"]
        for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    StrictLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise yaml.constructor.ConstructorError(
                    None, None, "merge keys are not supported", key_node.start_mark
                )
            key = self.construct_object(key_node, deep=True)
            try:
                hash(key)
            except TypeError:
                raise yaml.constructor.ConstructorError(
                    None, None, "unhashable mapping key", key_node.start_mark
                )
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, "duplicate mapping key %r" % (key,), key_node.start_mark
                )
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(self, node, deep=deep)

    StrictLoader.construct_mapping = construct_mapping
    return yaml, StrictLoader


def yaml_load_strict(text, res):
    """Return the strictly loaded document or None after recording errors."""
    try:
        yaml, loader = _strict_loader()
    except ImportError:
        res.err("PyYAML %s is required for the strict cross-check" % PYYAML_REQUIRED_VERSION)
        return None
    if yaml.__version__ != PYYAML_REQUIRED_VERSION:
        res.err("PyYAML version %s != required %s" % (yaml.__version__, PYYAML_REQUIRED_VERSION))
        return None
    expect = os.environ.get("CI_EXPECT_PYYAML_DIR")
    if expect:
        got = os.path.dirname(os.path.dirname(os.path.realpath(yaml.__file__)))
        if got != os.path.realpath(expect):
            res.err("PyYAML loaded from %s, expected the pinned install in %s" % (got, expect))
            return None
    try:
        docs = 0
        for ev in yaml.parse(text, Loader=loader):
            if isinstance(ev, yaml.DocumentStartEvent):
                docs += 1
            if isinstance(ev, yaml.AliasEvent) or getattr(ev, "anchor", None):
                res.err("YAML anchors/aliases are not supported")
                return None
            if getattr(ev, "tag", None) is not None:
                res.err("explicit YAML tags are not supported")
                return None
        if docs != 1:
            res.err("expected exactly one YAML document, found %d" % docs)
            return None
        return yaml.load(text, Loader=loader)
    except yaml.YAMLError as exc:
        res.err("strict YAML parse failed: %s" % str(exc).replace("\n", " "))
        return None


def _is_write_permission(value):
    if isinstance(value, str):
        return value in ("write", "write-all")
    if isinstance(value, dict):
        return any(v == "write" for v in value.values())
    return False


def raw_uses_lines(text, res):
    """Return [(action, sha, tag)] for every ``uses:`` line of the raw text.

    Every line whose key is ``uses`` must be the plain pinned form
    ``uses: owner/repo@<40-hex> # vX.Y.Z``; anything else is an error.
    """
    found = []
    for lineno, line in enumerate(text.split("\n"), 1):
        if not USES_KEY_RE.match(line):
            continue
        m = USES_LINE_RE.match(line.rstrip(" "))
        if not m:
            res.err("line %d: action reference must be 'owner/repo@<full sha> # vX.Y.Z': %r" % (lineno, line.strip()))
            continue
        found.append(m.groups())
    return found


def _check_permissions_value(where, value, res):
    if not isinstance(value, dict) or not value:
        res.err("%s permissions must be a non-empty mapping of read/none scopes, got %r" % (where, value))
        return
    for scope, level in value.items():
        if level not in ("read", "none"):
            res.err("%s permission %s: %r is not read-only" % (where, scope, level))


def _check_step(job_id, idx, step, res):
    """Validate one step; return the action repository it uses, if any."""
    if not isinstance(step, dict):
        res.err("job %r step %d is not a mapping" % (job_id, idx))
        return None
    run = step.get("run")
    if isinstance(run, str) and FLOATING_RUN_RE.search(run):
        res.err("job %r step %d installs or fetches a floating/unpinned reference: %r" % (job_id, idx, run.strip()))
    uses = step.get("uses")
    if uses is None:
        return None
    if not isinstance(uses, str) or "@" not in uses or uses.startswith("./") or uses.startswith("docker://"):
        res.err("job %r step %d uses %r: only pinned third-party actions are allowed" % (job_id, idx, uses))
        return None
    repo, ref = uses.rsplit("@", 1)
    pin = PINNED_ACTIONS.get(repo)
    if pin is None:
        res.err("job %r step %d action %r is not in the verified pin list" % (job_id, idx, repo))
        return repo
    if ref != pin[0]:
        res.err("job %r step %d action %s@%s is not the verified %s commit %s" % (job_id, idx, repo, ref, pin[1], pin[0]))
    with_ = step.get("with") or {}
    if not isinstance(with_, dict):
        res.err("job %r step %d 'with' is not a mapping" % (job_id, idx))
        with_ = {}
    if repo == CHECKOUT:
        for key in with_:
            if key not in CHECKOUT_INPUTS:
                res.err("job %r step %d checkout input %r is not allowed" % (job_id, idx, key))
        if with_.get("persist-credentials") is not False:
            res.err("job %r step %d checkout must set persist-credentials: false" % (job_id, idx))
    elif repo == SETUP_GO:
        for key in with_:
            if key not in SETUP_GO_INPUTS:
                res.err("job %r step %d setup-go input %r is not allowed (use go-version-file: go.mod)" % (job_id, idx, key))
        if with_.get("go-version-file") != "go.mod":
            res.err("job %r step %d setup-go must read go-version-file: go.mod" % (job_id, idx))
        if "cache" in with_ and not isinstance(with_["cache"], bool):
            res.err("job %r step %d setup-go cache must be a boolean" % (job_id, idx))
    elif with_:
        res.err("job %r step %d action %s takes no inputs here" % (job_id, idx, repo))
    return repo


def crosscheck(data, top_keys, events, res, text=None):
    if not isinstance(data, dict):
        res.err("workflow document is not a mapping")
        return
    if not all(isinstance(k, str) for k in data):
        res.err("non-string top-level keys in strict parse: %r" % [k for k in data if not isinstance(k, str)])
        return
    if sorted(data) != sorted(top_keys):
        res.err("strict parse top-level keys %r differ from canonical %r" % (sorted(data), sorted(top_keys)))
    if events is not None and data.get("on") != events:
        res.err("strict parse 'on' %r differs from canonical %r" % (data.get("on"), events))
    if _is_write_permission(data.get("permissions")):
        res.err("top-level write permissions are not allowed")
    if data.get("permissions") != TOP_PERMISSIONS:
        res.err("top-level permissions must be exactly %r, got %r" % (TOP_PERMISSIONS, data.get("permissions")))
    if data.get("concurrency") != CONCURRENCY:
        res.err("concurrency must be exactly %r, got %r" % (CONCURRENCY, data.get("concurrency")))
    if text is not None and "secrets." in text.replace(" ", ""):
        res.err("workflows must not reference secrets")
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        res.err("jobs must be a non-empty mapping")
        return
    step_uses = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            res.err("job %r is not a mapping" % (job_id,))
            continue
        if "uses" in job:
            res.err("job %r calls a reusable workflow (%r)" % (job_id, job["uses"]))
        if _is_write_permission(job.get("permissions")):
            res.err("job %r requests write permissions" % (job_id,))
        if "permissions" in job:
            _check_permissions_value("job %r" % (job_id,), job["permissions"], res)
        if "container" in job:
            res.err("job %r container jobs are not allowed" % (job_id,))
        runs_on = job.get("runs-on")
        if runs_on not in GITHUB_HOSTED_RUNNERS:
            res.err("job %r runs-on %r is not one of the GitHub-hosted labels %r" % (job_id, runs_on, GITHUB_HOSTED_RUNNERS))
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            res.err("job %r must have a non-empty steps list" % (job_id,))
            continue
        repos = []
        targets = []
        for idx, step in enumerate(steps):
            repo = _check_step(job_id, idx, step, res)
            if repo is not None:
                repos.append(repo)
                step_uses.append(step.get("uses"))
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                targets.extend(MAKE_TARGET_RE.findall(step["run"]))
        if SETUP_GO in repos and not set(targets) & set(GO_CHECKED_TARGETS):
            res.err("job %r sets up Go but runs no target that verifies the go.mod toolchain" % (job_id,))
        if (SETUP_GO in repos or targets) and (not repos or repos[0] != CHECKOUT):
            res.err("job %r must check out the repository (pinned checkout) first" % (job_id,))
    if text is not None:
        raw = raw_uses_lines(text, res)
        if [("%s@%s" % (a, s)) for a, s, _ in raw] != step_uses:
            res.err("raw 'uses:' lines %r differ from parsed steps %r" % (raw, step_uses))
        for action, sha, tag in raw:
            pin = PINNED_ACTIONS.get(action)
            if pin is not None and (sha, tag) != pin:
                res.err("action %s@%s comment %r does not match verified pin %s %s" % (action, sha, tag, pin[1], pin[0]))


def make_targets(data):
    """All ``make <target>`` invocations in run steps, in order."""
    out = []
    jobs = data.get("jobs") if isinstance(data, dict) else None
    for job in (jobs or {}).values():
        for step in (job.get("steps") or []) if isinstance(job, dict) else []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                out.extend(MAKE_TARGET_RE.findall(step["run"]))
    return out


# ---------------------------------------------------------------------------


def validate_text(cls, text):
    """Validate one workflow body against class ``cls``."""
    res = Result()
    top_keys, events = parse_canonical(text, res)
    if events is not None:
        check_event_policy(cls, events, res)
    matrix = run_matrix(events)
    check_matrix(cls, matrix, res)
    data = yaml_load_strict(text, res)
    targets = []
    if data is not None:
        crosscheck(data, top_keys, events, res, text=text)
        targets = make_targets(data)
    if cls != HEAVY:
        for t in targets:
            if t in MANUAL_ONLY_TARGETS:
                res.err("target %r is manual-only and must not run in a %s workflow" % (t, cls))
    return {
        "class": cls,
        "events": events,
        "runs": sorted(k for k, v in matrix.items() if v),
        "make_targets": targets,
        "errors": res.errors,
        "warnings": res.warnings,
    }


def makefile_targets(path):
    with open(path) as fh:
        return set(re.findall(r"^([A-Za-z0-9_.-]+)[ \t]*:(?!=)", fh.read(), re.M))


def check_repo(root):
    """Repository-level pins that the workflows rely on; returns errors."""
    errors = []

    def read(rel):
        try:
            with open(os.path.join(root, rel)) as fh:
                return fh.read()
        except OSError as exc:
            errors.append("%s: cannot read: %s" % (rel, exc))
            return ""

    gomod = read("go.mod")
    toolchains = re.findall(r"^toolchain[ \t]+(\S+)[ \t]*$", gomod, re.M)
    if toolchains != [EXPECTED_GO_TOOLCHAIN]:
        errors.append("go.mod: toolchain directive %r != selected %s" % (toolchains, EXPECTED_GO_TOOLCHAIN))
    env = {}
    for line in read("buildscripts/ci/tools.env").split("\n"):
        if line.strip() and not line.startswith("#"):
            m = re.match(r"([A-Z][A-Z0-9_]*)=([A-Za-z0-9.]+)\Z", line)
            if not m:
                errors.append("buildscripts/ci/tools.env: not a plain NAME=value line: %r" % line)
                continue
            env[m.group(1)] = m.group(2)
    if env.get("PYYAML_VERSION") != PYYAML_REQUIRED_VERSION:
        errors.append("tools.env PYYAML_VERSION %r != %s" % (env.get("PYYAML_VERSION"), PYYAML_REQUIRED_VERSION))
    req = read("buildscripts/ci/requirements-ci.txt")
    pins = re.findall(r"^PyYAML==(\S+)", req, re.M)
    if pins != [PYYAML_REQUIRED_VERSION]:
        errors.append("requirements-ci.txt PyYAML pins %r != [%s]" % (pins, PYYAML_REQUIRED_VERSION))
    if not re.search(r"--hash=sha256:[0-9a-f]{64}", req):
        errors.append("requirements-ci.txt has no sha256 hashes")
    installer = read("buildscripts/ci/install-tools.sh")
    for key, name in sorted(CHECKSUMMED_TOOLS.items()):
        version = env.get(key)
        if not version:
            errors.append("tools.env: %s is missing" % key)
            continue
        for arch in TOOL_ARCHES:
            if not re.search(r"^\t%s-%s-%s\) echo [0-9a-f]{64} ;;$" % (re.escape(name), re.escape(version), arch), installer, re.M):
                errors.append("install-tools.sh: no pinned sha256 for %s %s %s" % (name, version, arch))
    for key in ("GOVULNCHECK_VERSION", "SHFMT_VERSION"):
        if not re.match(r"v[0-9]+\.[0-9]+\.[0-9]+\Z", env.get(key, "")):
            errors.append("tools.env: %s must be an exact vX.Y.Z module version" % key)
    return errors


def validate_dir(wf_dir, inventory=None, removed=None, makefile=None, expected_targets=None):
    inventory = INVENTORY if inventory is None else inventory
    removed = REMOVED if removed is None else removed
    expected_targets = EXPECTED_MAKE_TARGETS if expected_targets is None else expected_targets
    report = {"policy_version": POLICY_VERSION, "workflows": {}, "errors": []}
    try:
        entries = sorted(os.listdir(wf_dir))
    except OSError as exc:
        report["errors"].append("cannot list %s: %s" % (wf_dir, exc))
        return report
    seen = set()
    for name in entries:
        path = os.path.join(wf_dir, name)
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            report["errors"].append("%s: symlinks are not allowed" % name)
            continue
        if stat.S_ISDIR(st.st_mode):
            if name not in SUPPORT_DIRS:
                report["errors"].append("%s: unclassified directory" % name)
            continue
        lower = name.lower()
        if lower.endswith(".yml") or lower.endswith(".yaml"):
            if name in removed:
                report["errors"].append("%s: removed upstream admin workflow is present (%s)" % (name, removed[name]))
                continue
            if name not in inventory:
                report["errors"].append("%s: unclassified workflow" % name)
                continue
            seen.add(name)
            with open(path, "rb") as fh:
                raw = fh.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                report["errors"].append("%s: not UTF-8" % name)
                continue
            entry = validate_text(inventory[name], text)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            report["workflows"][name] = entry
            report["errors"].extend("%s: %s" % (name, e) for e in entry["errors"])
        elif name not in SUPPORT_FILES:
            report["errors"].append("%s: unclassified file in workflows directory" % name)
    for name in sorted(set(inventory) - seen):
        if name not in report["workflows"]:
            report["errors"].append("%s: classified workflow is missing" % name)
    for name, want in sorted(expected_targets.items()):
        entry = report["workflows"].get(name)
        if entry is not None and set(entry["make_targets"]) != want:
            report["errors"].append("%s: make targets %r != expected %r" % (name, sorted(set(entry["make_targets"])), sorted(want)))
    if makefile is not None:
        try:
            defined = makefile_targets(makefile)
        except OSError as exc:
            report["errors"].append("cannot read Makefile: %s" % exc)
            defined = None
        if defined is not None:
            for name, entry in sorted(report["workflows"].items()):
                for t in sorted(set(entry["make_targets"]) - defined):
                    report["errors"].append("%s: make target %r is not defined in the Makefile" % (name, t))
    return report


def main(argv=None):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workflows-dir", default=os.path.join(root, ".github", "workflows"))
    ap.add_argument("--repo-root", default=root, help="checkout whose go.mod/Makefile/tool pins are checked")
    ap.add_argument("--json", help="write the full report to this path")
    args = ap.parse_args(argv)
    report = validate_dir(args.workflows_dir, makefile=os.path.join(args.repo_root, "Makefile"))
    report["errors"].extend(check_repo(args.repo_root))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
            fh.write("\n")
    for name, entry in sorted(report["workflows"].items()):
        print("%-24s %-12s runs=%s" % (name, entry["class"], ",".join(entry["runs"]) or "-"))
        for w in entry["warnings"]:
            print("  warning: %s" % w)
    for e in report["errors"]:
        print("ERROR: %s" % e)
    ok = not report["errors"]
    print("workflow policy: %s (%d workflows)" % ("PASS" if ok else "FAIL", len(report["workflows"])))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
