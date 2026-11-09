#!/usr/bin/env python3
# CI maintenance helper for the unofficial relvo-labs/minio fork; not part of
# or endorsed by upstream MinIO.  Distributed under the repository's license.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fail-closed GitHub Actions trigger policy validator (relvo-labs/minio #3).

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
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys

POLICY_VERSION = "1"
PYYAML_REQUIRED_VERSION = "6.0.3"

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
    "go-resiliency.yml": HEAVY,
    "iam-integrations.yaml": HEAVY,
    "mint.yml": HEAVY,
    "replication.yaml": HEAVY,
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


def crosscheck(data, top_keys, events, res):
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
    if "permissions" not in data:
        res.warn("pending stage2: no top-level read-only permissions block")
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        res.err("jobs must be a non-empty mapping")
        return
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            res.err("job %r is not a mapping" % (job_id,))
            continue
        if "uses" in job:
            res.err("job %r calls a reusable workflow (%r)" % (job_id, job["uses"]))
        if _is_write_permission(job.get("permissions")):
            res.err("job %r requests write permissions" % (job_id,))
        runs_on = job.get("runs-on")
        if isinstance(runs_on, str) and not (
            runs_on.startswith("ubuntu-") or runs_on.startswith("${{")
        ):
            res.warn("pending stage2: job %r runs-on %r is not a GitHub-hosted label" % (job_id, runs_on))
        for step in job.get("steps") or []:
            uses = step.get("uses") if isinstance(step, dict) else None
            if isinstance(uses, str) and not uses.startswith("./"):
                ref = uses.rsplit("@", 1)[-1] if "@" in uses else ""
                if not re.match(r"[0-9a-f]{40}\Z", ref):
                    res.warn("pending stage2: job %r action %r is not pinned to a full SHA" % (job_id, uses))


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
    if data is not None:
        crosscheck(data, top_keys, events, res)
    return {
        "class": cls,
        "events": events,
        "runs": sorted(k for k, v in matrix.items() if v),
        "errors": res.errors,
        "warnings": res.warnings,
    }


def validate_dir(wf_dir, inventory=None, removed=None):
    inventory = INVENTORY if inventory is None else inventory
    removed = REMOVED if removed is None else removed
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
    return report


def main(argv=None):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workflows-dir", default=os.path.join(root, ".github", "workflows"))
    ap.add_argument("--json", help="write the full report to this path")
    args = ap.parse_args(argv)
    report = validate_dir(args.workflows_dir)
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
