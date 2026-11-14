# CI maintenance tests for the unofficial relvo-labs/granary fork; not part of
# or endorsed by upstream MinIO.  Distributed under the repository's license.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adversarial tests for buildscripts/ci/workflow_policy.py.

Run from the repository root:
    python3 -m unittest discover -s tests/ci -p 'test_*.py' -v
PyYAML is mandatory; a missing or different version fails, never skips.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "buildscripts", "ci", "workflow_policy.py")
WF_DIR = os.path.join(ROOT, ".github", "workflows")

_spec = importlib.util.spec_from_file_location("workflow_policy", SCRIPT)
wp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp)

PORTABLE_ON = """on:
  workflow_dispatch:
  pull_request:
    types:
      - ready_for_review
    branches:
      - master
"""
METADATA_ON = """on:
  pull_request:
    types:
      - ready_for_review
"""
HEAVY_ON = """on:
  workflow_dispatch:
"""
JOBS = """
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
CONCURRENCY = (
    "\nconcurrency:\n"
    "  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}\n"
    "  cancel-in-progress: true\n"
)
TAIL = CONCURRENCY + "\npermissions:\n  contents: read\n" + JOBS

CHECKOUT_PIN = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
SETUP_GO_PIN = "actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e # v7.0.0"
GO_JOB = """
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: %s
        with:
          persist-credentials: false
      - uses: %s
        with:
          go-version-file: go.mod
      - run: make ci-go-version
""" % (CHECKOUT_PIN, SETUP_GO_PIN)
GO_TAIL = CONCURRENCY + "\npermissions:\n  contents: read\n" + GO_JOB

# The inherited (pre-restoration) go-lint.yml job body, with the safe triggers.
INHERITED_GO_LINT = PORTABLE_ON + """
concurrency:
  group: ${{ github.workflow }}-${{ github.head_ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  build:
    name: Go ${{ matrix.go-version }} on ${{ matrix.os }}
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        go-version: [1.24.x]
        os: [ubuntu-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: ${{ matrix.go-version }}
          check-latest: true
      - name: Build on ${{ matrix.os }}
        run: |
          make
          make test
          make test-race
"""


def doc(on_block, head="name: Fixture\n", tail=TAIL):
    return head + on_block + tail


def check(cls, text):
    return wp.validate_text(cls, text)


class PositiveCases(unittest.TestCase):
    def assertClean(self, cls, text):
        r = check(cls, text)
        self.assertEqual(r["errors"], [], r)
        return r

    def test_pyyaml_pinned(self):
        import yaml

        self.assertEqual(yaml.__version__, wp.PYYAML_REQUIRED_VERSION)
        expect = os.environ.get("CI_EXPECT_PYYAML_DIR")
        if expect:
            got = os.path.dirname(os.path.dirname(os.path.realpath(yaml.__file__)))
            self.assertEqual(got, os.path.realpath(expect))

    def test_compliant_go_job(self):
        r = self.assertClean(wp.PORTABLE, doc(PORTABLE_ON, tail=GO_TAIL))
        self.assertEqual(r["make_targets"], ["ci-go-version"])

    def test_setup_go_cache_and_secondary_checkout(self):
        tail = GO_TAIL.replace("go-version-file: go.mod", "go-version-file: go.mod\n          cache: false")
        tail += (
            "      - uses: %s\n        with:\n          repository: minio/minio-iam-testing\n"
            "          path: minio-iam-testing\n          persist-credentials: false\n" % CHECKOUT_PIN
        )
        self.assertClean(wp.HEAVY, doc(HEAVY_ON, tail=tail))

    def test_manual_race_target_in_heavy(self):
        tail = GO_TAIL.replace("make ci-go-version", "make ci-test-race")
        self.assertClean(wp.HEAVY, doc(HEAVY_ON, tail=tail))

    def test_portable_ready_and_manual(self):
        r = self.assertClean(wp.PORTABLE, doc(PORTABLE_ON))
        self.assertEqual(r["runs"], ["dispatch", "pr-ready_for_review-master"])

    def test_portable_without_branch_filter(self):
        on = "on:\n  workflow_dispatch:\n  pull_request:\n    types:\n      - ready_for_review\n"
        r = self.assertClean(wp.PORTABLE, doc(on))
        self.assertIn("pr-ready_for_review-other-base", r["runs"])

    def test_metadata_ready_only(self):
        r = self.assertClean(wp.PR_METADATA, doc(METADATA_ON))
        self.assertNotIn("dispatch", r["runs"])
        self.assertIn("pr-ready_for_review-master", r["runs"])

    def test_heavy_manual_only(self):
        r = self.assertClean(wp.HEAVY, doc(HEAVY_ON))
        self.assertEqual(r["runs"], ["dispatch"])

    def test_blank_and_comment_lines_inside_on(self):
        on = (
            "on:\n  # manual entry\n  workflow_dispatch:\n\n"
            "  pull_request:\n\n    types:\n# column-zero comment\n      - ready_for_review\n"
        )
        self.assertClean(wp.PORTABLE, doc(on))

    def test_leading_document_marker(self):
        self.assertClean(wp.HEAVY, doc(HEAVY_ON, head="---\nname: Fixture\n"))


class NegativeCases(unittest.TestCase):
    def assertRejected(self, cls, text, needle):
        r = check(cls, text)
        self.assertTrue(r["errors"], "accepted: %r" % text)
        joined = " | ".join(r["errors"])
        self.assertIn(needle, joined, joined)
        return r

    # --- forbidden events, including after blank lines/comments ---------
    def _forbidden_event(self, event, body=""):
        on = PORTABLE_ON + "\n\n  %s:\n%s" % (event, body)
        r = self.assertRejected(wp.PORTABLE, doc(on), "forbidden")
        return r

    def test_push_after_blank_lines(self):
        r = self._forbidden_event("push", "    branches:\n      - master\n")
        self.assertIn("push", r["runs"])

    def test_push_after_comment(self):
        on = PORTABLE_ON + "# trailing comment\n  push:\n"
        r = self.assertRejected(wp.PORTABLE, doc(on), "'push' is forbidden")
        self.assertIn("push", r["runs"])

    def test_schedule(self):
        self.assertRejected(wp.PORTABLE, doc(PORTABLE_ON + "\n  schedule:\n    - cron: '0 0 * * *'\n"), "'schedule' is forbidden")

    def test_workflow_run(self):
        self._forbidden_event("workflow_run", "    workflows:\n      - Linters\n")

    def test_pull_request_target(self):
        self._forbidden_event("pull_request_target")

    def test_workflow_call(self):
        self._forbidden_event("workflow_call")

    def test_repository_dispatch(self):
        self._forbidden_event("repository_dispatch")

    def test_issues_and_merge_group(self):
        self._forbidden_event("issues")
        self._forbidden_event("merge_group")

    def test_heavy_with_ready(self):
        r = self.assertRejected(wp.HEAVY, doc(PORTABLE_ON), "'pull_request' is forbidden for class heavy")
        self.assertIn("pr-ready_for_review-master", r["runs"])

    def test_metadata_with_dispatch(self):
        self.assertRejected(wp.PR_METADATA, doc(PORTABLE_ON), "'workflow_dispatch' is forbidden")

    def test_portable_missing_dispatch(self):
        self.assertRejected(wp.PORTABLE, doc(METADATA_ON), "requires event 'workflow_dispatch'")

    # --- ordinary PR activity types -------------------------------------
    def _types(self, types):
        items = "".join("      - %s\n" % t for t in types)
        on = "on:\n  workflow_dispatch:\n  pull_request:\n    types:\n" + items
        return self.assertRejected(wp.PORTABLE, doc(on), "types must be exactly")

    def test_ordinary_types(self):
        for t in ("opened", "synchronize", "reopened", "converted_to_draft", "closed", "edited", "labeled"):
            with self.subTest(t=t):
                r = self._types([t])
                self.assertIn("pr-%s-master" % t, r["runs"])
                self.assertNotIn("pr-ready_for_review-master", r["runs"])

    def test_ready_plus_synchronize(self):
        r = self._types(["ready_for_review", "synchronize"])
        self.assertIn("pr-synchronize-master", r["runs"])

    def test_missing_types_defaults(self):
        on = "on:\n  workflow_dispatch:\n  pull_request:\n    branches:\n      - master\n"
        r = self.assertRejected(wp.PORTABLE, doc(on), "types must be exactly")
        for a in wp.DEFAULT_PR_TYPES:
            self.assertIn("pr-%s-master" % a, r["runs"])

    def test_bare_pull_request(self):
        on = "on:\n  workflow_dispatch:\n  pull_request:\n"
        r = self.assertRejected(wp.PORTABLE, doc(on), "simulated forbidden triggers")
        self.assertIn("pr-opened-master", r["runs"])

    # --- filters ---------------------------------------------------------
    def test_other_filters(self):
        for key in ("branches-ignore", "paths", "paths-ignore", "tags"):
            with self.subTest(key=key):
                on = PORTABLE_ON + "    %s:\n      - docs\n" % key
                self.assertRejected(wp.PORTABLE, doc(on), "filter %r is not allowed" % key)

    def test_other_branch(self):
        on = PORTABLE_ON.replace("      - master", "      - main")
        self.assertRejected(wp.PORTABLE, doc(on), "branches must be exactly")

    def test_glob_branch_rejected(self):
        on = PORTABLE_ON.replace("      - master", "      - '**'")
        self.assertRejected(wp.PORTABLE, doc(on), "malformed filter item")

    def test_dispatch_inputs(self):
        on = "on:\n  workflow_dispatch:\n    inputs:\n      - x\n" + METADATA_ON[4:]
        self.assertRejected(wp.PORTABLE, doc(on), "workflow_dispatch must not declare")

    def test_empty_filter_list(self):
        on = "on:\n  workflow_dispatch:\n  pull_request:\n    types:\n"
        self.assertRejected(wp.PORTABLE, doc(on), "empty filter list")

    def test_duplicate_filter_and_item(self):
        dup_filter = PORTABLE_ON + "    types:\n      - ready_for_review\n"
        self.assertRejected(wp.PORTABLE, doc(dup_filter), "duplicate filter")
        dup_item = PORTABLE_ON.replace("      - ready_for_review\n", "      - ready_for_review\n      - ready_for_review\n")
        self.assertRejected(wp.PORTABLE, doc(dup_item), "duplicate item")

    # --- malformed on key / event syntax ---------------------------------
    def test_scalar_and_list_on(self):
        for on in ("on: pull_request\n", "on: [pull_request]\n", "on: [workflow_dispatch]\n",
                   "on: {workflow_dispatch: null}\n", "on: workflow_dispatch\n"):
            with self.subTest(on=on):
                self.assertRejected(wp.HEAVY, doc(on), "'on' must be a block mapping")

    def test_flow_list_types(self):
        on = "on:\n  workflow_dispatch:\n  pull_request:\n    types: [ready_for_review]\n"
        self.assertRejected(wp.PORTABLE, doc(on), "malformed event filter")

    def test_flow_list_under_on(self):
        self.assertRejected(wp.HEAVY, doc("on:\n  [workflow_dispatch]\n"), "malformed event entry")

    def test_event_value_forms(self):
        for line in ("  workflow_dispatch: {}\n", "  workflow_dispatch: null\n", "  workflow_dispatch: ~\n",
                     '  "workflow_dispatch":\n', "  'push':\n", "  ? push\n", "  push :\n", "  Push:\n"):
            with self.subTest(line=line):
                self.assertRejected(wp.HEAVY, doc("on:\n" + line), "malformed event entry")

    def test_scalar_types(self):
        on = "on:\n  workflow_dispatch:\n  pull_request:\n    types: ready_for_review\n"
        self.assertRejected(wp.PORTABLE, doc(on), "malformed event filter")

    def test_list_at_filter_indent(self):
        on = "on:\n  workflow_dispatch:\n  pull_request:\n    types:\n    - ready_for_review\n"
        self.assertRejected(wp.PORTABLE, doc(on), "malformed event filter")

    def test_odd_indentation(self):
        self.assertRejected(wp.HEAVY, doc("on:\n   workflow_dispatch:\n"), "unsupported indentation")
        self.assertRejected(wp.HEAVY, doc("on:\n    workflow_dispatch:\n"), "malformed event filter")

    def test_inline_comment(self):
        self.assertRejected(wp.HEAVY, doc("on:\n  workflow_dispatch: # manual\n"), "inline comments")

    def test_tab_indentation(self):
        self.assertRejected(wp.HEAVY, doc("on:\n\tworkflow_dispatch:\n"), "tab in indentation")

    def test_duplicate_event_after_blank(self):
        on = HEAVY_ON + "\n\n  workflow_dispatch:\n"
        self.assertRejected(wp.HEAVY, doc(on), "duplicate event")

    def test_duplicate_on_key(self):
        text = doc(HEAVY_ON) + "\non:\n  push:\n"
        self.assertRejected(wp.HEAVY, text, "duplicate top-level key 'on'")

    def test_quoted_on_key(self):
        for key in ('"on":', "'on':"):
            with self.subTest(key=key):
                text = doc(HEAVY_ON) + "\n%s\n  push:\n" % key
                self.assertRejected(wp.HEAVY, text, "unsupported top-level syntax")

    def test_yaml11_boolean_aliases(self):
        for key in ("true", "True", "yes", "On", "ON", "y"):
            with self.subTest(key=key):
                text = doc(HEAVY_ON) + "\n%s:\n  push:\n" % key
                self.assertRejected(wp.HEAVY, text, "is not allowed")

    def test_complex_key_and_directives(self):
        for extra in ("? on\n: push\n", "%YAML 1.1\n", "<<: {on: push}\n", "!!str on:\n  push:\n", "--- !!map\n"):
            with self.subTest(extra=extra):
                self.assertRejected(wp.HEAVY, doc(HEAVY_ON) + extra, "unsupported top-level syntax")

    def test_missing_on_and_jobs(self):
        self.assertRejected(wp.HEAVY, "name: x\njobs:\n  a:\n    runs-on: ubuntu-latest\n", "missing top-level 'on'")
        self.assertRejected(wp.HEAVY, "name: x\n" + HEAVY_ON, "missing top-level 'jobs'")

    def test_indented_before_first_key(self):
        self.assertRejected(wp.HEAVY, "  name: x\n" + doc(HEAVY_ON, head=""), "indented content before")

    def test_multiple_documents(self):
        text = doc(HEAVY_ON) + "---\n" + doc("on:\n  push:\n")
        self.assertRejected(wp.HEAVY, text, "multiple YAML documents")

    def test_document_end_marker(self):
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON) + "...\n", "unsupported top-level syntax")

    def test_crlf_and_bom(self):
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON).replace("\n", "\r\n"), "carriage returns")
        self.assertRejected(wp.HEAVY, "\ufeff" + doc(HEAVY_ON), "byte-order mark")

    def test_unterminated_top_values(self):
        for head in ('name: "Fixture\n', "name: [a,\n", "name: {a: 1,\n", "name: &a x\n", "name: *a\n", "name: |2\n  x\n"):
            with self.subTest(head=head):
                self.assertRejected(wp.HEAVY, doc(HEAVY_ON, head=head), "for 'name'")

    # --- strict YAML layer -----------------------------------------------
    def test_anchor_in_job_body(self):
        tail = TAIL.replace("runs-on: ubuntu-latest", "runs-on: &r ubuntu-latest\n    env:\n      X: *r")
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "anchors/aliases")

    def test_duplicate_job_key(self):
        tail = TAIL + "  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo dup\n"
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "duplicate mapping key")

    def test_explicit_tag_in_body(self):
        tail = TAIL.replace("echo ok", "!!str echo ok")
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "explicit YAML tags")

    def test_reusable_workflow_job(self):
        tail = "\njobs:\n  call:\n    uses: ./.github/workflows/go-lint.yml\n"
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "reusable workflow")
        tail = "\njobs:\n  call:\n    uses: other-org/repo/.github/workflows/x.yml@v1\n"
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "reusable workflow")

    def test_write_permissions(self):
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=TAIL.replace("contents: read", "contents: write")),
                            "top-level write permissions")
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail="\npermissions: write-all\n" + JOBS),
                            "top-level write permissions")
        tail = TAIL.replace("    runs-on:", "    permissions:\n      issues: write\n    runs-on:")
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "requests write permissions")

    def test_invalid_yaml_body(self):
        tail = TAIL.replace("      - run: echo ok", "      - run: [unterminated")
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "strict YAML parse failed")

    def test_crosscheck_detects_divergence(self):
        res = wp.Result()
        data = {"name": "x", "on": {"push": None}, "jobs": {"a": {"runs-on": "ubuntu-latest"}}, "permissions": {}}
        wp.crosscheck(data, ["name", "on", "jobs", "permissions"], {"workflow_dispatch": None}, res)
        self.assertTrue(any("differs from canonical" in e for e in res.errors), res.errors)
        res = wp.Result()
        wp.crosscheck({True: {"push": None}, "jobs": {}}, ["on", "jobs"], {}, res)
        self.assertTrue(any("non-string top-level keys" in e for e in res.errors), res.errors)

    def test_strict_loader_keeps_on_as_string(self):
        res = wp.Result()
        data = wp.yaml_load_strict(doc(HEAVY_ON), res)
        self.assertEqual(res.errors, [])
        self.assertIn("on", data)
        self.assertNotIn(True, data)

    def test_pyyaml_from_unexpected_location(self):
        with mock.patch.dict(os.environ, {"CI_EXPECT_PYYAML_DIR": tempfile.gettempdir()}):
            res = wp.Result()
            self.assertIsNone(wp.yaml_load_strict(doc(HEAVY_ON), res))
        self.assertTrue(any("expected the pinned install" in e for e in res.errors), res.errors)

    # --- job-level invariants (formerly stage-2 warnings) -----------------
    def _go(self, old, new, needle, cls=None, count=1):
        tail = GO_TAIL.replace(old, new, count)
        self.assertNotEqual(tail, GO_TAIL, "fixture replacement did not apply: %r" % old)
        return self.assertRejected(cls or wp.HEAVY, doc(HEAVY_ON if cls in (None, wp.HEAVY) else PORTABLE_ON, tail=tail), needle)

    def test_inherited_go_lint_rejected(self):
        r = check(wp.PORTABLE, "name: Linters and Tests\n" + INHERITED_GO_LINT)
        joined = " | ".join(r["errors"])
        for needle in ("concurrency must be exactly", "runs-on '${{ matrix.os }}'",
                       "must be 'owner/repo@<full sha> # vX.Y.Z'", "not the verified v7.0.1",
                       "setup-go input 'go-version' is not allowed", "setup-go input 'check-latest'",
                       "must read go-version-file: go.mod", "persist-credentials: false",
                       "'test-race' is manual-only"):
            with self.subTest(needle=needle):
                self.assertIn(needle, joined)

    def test_floating_and_short_refs(self):
        for ref in ("v4", "master", "v7.0.1", "3d3c42e"):
            with self.subTest(ref=ref):
                self._go(CHECKOUT_PIN, "actions/checkout@%s" % ref, "action reference must be")

    def test_unverified_sha(self):
        self._go(CHECKOUT_PIN, CHECKOUT_PIN.replace("3d3c42e5", "0d3c42e5"), "is not the verified v7.0.1 commit")

    def test_tag_comment_must_match(self):
        self._go(CHECKOUT_PIN, CHECKOUT_PIN.replace("# v7.0.1", "# v4.2.2"), "does not match verified pin")
        self._go(CHECKOUT_PIN, CHECKOUT_PIN.replace(" # v7.0.1", ""), "action reference must be")
        self._go(CHECKOUT_PIN, CHECKOUT_PIN + " extra", "action reference must be")

    def test_quoted_or_spaced_uses(self):
        self._go("- uses: " + CHECKOUT_PIN, '- uses: "%s"' % CHECKOUT_PIN.split(" #")[0], "action reference must be")
        self._go("- uses: " + CHECKOUT_PIN, "- uses : " + CHECKOUT_PIN, "action reference must be")

    def test_unknown_pinned_action(self):
        self._go(CHECKOUT_PIN, "azure/setup-helm@" + "a" * 40 + " # v4.0.0", "not in the verified pin list")

    def test_local_docker_and_reusable_actions(self):
        self._go("- uses: " + SETUP_GO_PIN, "- uses: ./.github/actions/x", "action reference must be")
        self._go("- uses: " + SETUP_GO_PIN, "- uses: docker://alpine:3", "action reference must be")

    def test_checkout_credentials(self):
        self._go("persist-credentials: false", "persist-credentials: true", "persist-credentials: false")
        self._go("persist-credentials: false", "fetch-depth: 0", "checkout input 'fetch-depth' is not allowed")
        self._go("persist-credentials: false", "token: x\n          persist-credentials: false",
                 "checkout input 'token' is not allowed")

    def test_setup_go_inputs(self):
        self._go("go-version-file: go.mod", "go-version: 1.27.3", "must read go-version-file: go.mod")
        self._go("go-version-file: go.mod", "go-version-file: go.work", "must read go-version-file: go.mod")
        self._go("go-version-file: go.mod", "go-version-file: go.mod\n          cached: false",
                 "setup-go input 'cached' is not allowed")
        self._go("go-version-file: go.mod", "go-version-file: go.mod\n          cache: 'false'",
                 "cache must be a boolean")

    def test_runners(self):
        for runs_on in ("mint", "self-hosted", "[self-hosted, linux]", "${{ matrix.os }}", "windows-latest"):
            with self.subTest(runs_on=runs_on):
                self._go("runs-on: ubuntu-latest", "runs-on: %s" % runs_on, "is not one of the GitHub-hosted labels")

    def test_permissions_exact(self):
        self._go("permissions:\n  contents: read\n", "", "top-level permissions must be exactly")
        self._go("  contents: read\n", "  contents: read\n  issues: read\n", "top-level permissions must be exactly")
        self._go("permissions:\n  contents: read\n", "permissions: read-all\n", "top-level permissions must be exactly")
        self._go("    runs-on:", "    permissions:\n      contents: write\n    runs-on:", "requests write permissions")
        self._go("    runs-on:", "    permissions: write-all\n    runs-on:", "requests write permissions")

    def test_concurrency_exact(self):
        self._go("github.event.pull_request.number || github.run_id", "github.head_ref", "concurrency must be exactly")
        self._go(CONCURRENCY, "", "concurrency must be exactly")
        self._go("cancel-in-progress: true", "cancel-in-progress: false", "concurrency must be exactly")

    def test_secrets_forbidden(self):
        self._go("- run: make ci-go-version",
                 "- run: make ci-go-version\n        env:\n          T: ${{ secrets.GITHUB_TOKEN }}",
                 "must not reference secrets")
        self._go("- run: make ci-go-version",
                 "- run: make ci-go-version\n        env:\n          T: ${{ secrets . BOT_PAT }}",
                 "must not reference secrets")

    def test_floating_installs_in_run(self):
        for run in ("go install golang.org/x/vuln/cmd/govulncheck@latest",
                    "curl -sSfL https://example.invalid/install.sh | sh -s v2",
                    "curl -sSfL https://example.invalid/i.sh | sudo bash",
                    "pip install git+https://example.invalid/x@main"):
            with self.subTest(run=run):
                self._go("- run: make ci-go-version", "- run: make ci-go-version && " + run, "floating/unpinned")

    def test_go_job_must_verify_toolchain(self):
        self._go("make ci-go-version", "make verify", "runs no target that verifies the go.mod toolchain")

    def test_checkout_must_come_first(self):
        tail = GO_TAIL.replace("      - uses: %s\n        with:\n          persist-credentials: false\n" % CHECKOUT_PIN, "")
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "must check out the repository")

    def test_container_job(self):
        self._go("    runs-on: ubuntu-latest", "    runs-on: ubuntu-latest\n    container: alpine:3",
                 "container jobs are not allowed")

    def test_race_target_not_portable(self):
        for target in ("ci-test-race", "test-race"):
            with self.subTest(target=target):
                self._go("make ci-go-version", "make ci-go-version\n          make %s" % target,
                         "is manual-only", cls=wp.PORTABLE)

    def test_empty_steps(self):
        tail = CONCURRENCY + "\npermissions:\n  contents: read\n\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        self.assertRejected(wp.HEAVY, doc(HEAVY_ON, tail=tail), "non-empty steps list")


class DirectoryInventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wfpolicy-")
        self.wf = os.path.join(self.tmp, "workflows")
        shutil.copytree(WF_DIR, self.wf, symlinks=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def errors(self):
        return " | ".join(wp.validate_dir(self.wf)["errors"])

    def test_copy_is_clean(self):
        self.assertEqual(self.errors(), "")

    def test_unclassified_workflow(self):
        for name in ("extra.yml", "Extra.YAML", ".hidden.yml"):
            with self.subTest(name=name):
                path = os.path.join(self.wf, name)
                with open(path, "w") as fh:
                    fh.write(doc(HEAVY_ON))
                self.assertIn("unclassified workflow", self.errors())
                os.remove(path)

    def test_removed_admin_workflow_present(self):
        for name in wp.REMOVED:
            with self.subTest(name=name):
                path = os.path.join(self.wf, name)
                with open(path, "w") as fh:
                    fh.write(doc(HEAVY_ON))
                self.assertIn("removed upstream admin workflow", self.errors())
                os.remove(path)

    def test_missing_classified_workflow(self):
        for name in ("go-lint.yml", "go-race.yml"):
            with self.subTest(name=name):
                os.rename(os.path.join(self.wf, name), os.path.join(self.tmp, name))
                self.assertIn("%s: classified workflow is missing" % name, self.errors())
                os.rename(os.path.join(self.tmp, name), os.path.join(self.wf, name))

    def test_unknown_file_dir_and_symlink(self):
        with open(os.path.join(self.wf, "notes.txt"), "w") as fh:
            fh.write("x\n")
        os.mkdir(os.path.join(self.wf, "nested"))
        os.symlink("go-lint.yml", os.path.join(self.wf, "link.yml"))
        errs = self.errors()
        self.assertIn("notes.txt: unclassified file", errs)
        self.assertIn("nested: unclassified directory", errs)
        self.assertIn("link.yml: symlinks are not allowed", errs)

    def test_regressed_trigger_in_real_file(self):
        path = os.path.join(self.wf, "go-lint.yml")
        with open(path) as fh:
            text = fh.read()
        with open(path, "w") as fh:
            fh.write(text.replace("      - ready_for_review\n", "      - ready_for_review\n      - synchronize\n", 1))
        self.assertIn("go-lint.yml: pull_request types must be exactly", self.errors())

    def _edit(self, name, old, new):
        path = os.path.join(self.wf, name)
        with open(path) as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(path, "w") as fh:
            fh.write(text.replace(old, new, 1))

    def test_portable_targets_must_match(self):
        self._edit("go-lint.yml", "        run: make ci-lint\n", "        run: make ci-go-version\n")
        self.assertIn("go-lint.yml: make targets", self.errors())

    def test_race_moved_back_to_portable(self):
        self._edit("go-lint.yml", "          make ci-test\n", "          make ci-test\n          make ci-test-race\n")
        errs = self.errors()
        self.assertIn("'ci-test-race' is manual-only", errs)
        self.assertIn("go-lint.yml: make targets", errs)

    def test_race_workflow_must_run_race(self):
        self._edit("go-race.yml", "make ci-test-race", "make ci-test")
        self.assertIn("go-race.yml: make targets", self.errors())

    def test_undefined_make_target(self):
        mk = os.path.join(self.tmp, "Makefile")
        with open(ROOT + "/Makefile") as fh:
            text = fh.read()
        with open(mk, "w") as fh:
            fh.write(text.replace("\nci-helm-lint:", "\nci-helm-lint-renamed:"))
        errs = " | ".join(wp.validate_dir(self.wf, makefile=mk)["errors"])
        self.assertIn("helm-lint.yml: make target 'ci-helm-lint' is not defined", errs)
        self.assertEqual(wp.validate_dir(self.wf, makefile=ROOT + "/Makefile")["errors"], [])


class RepositoryPins(unittest.TestCase):
    FILES = ("go.mod", "Makefile", "buildscripts/ci/tools.env",
             "buildscripts/ci/requirements-ci.txt", "buildscripts/ci/install-tools.sh")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wfrepo-")
        for rel in self.FILES:
            os.makedirs(os.path.dirname(os.path.join(self.tmp, rel)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(self.tmp, rel))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _edit(self, rel, old, new):
        path = os.path.join(self.tmp, rel)
        with open(path) as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(path, "w") as fh:
            fh.write(text.replace(old, new, 1))

    def errors(self):
        return " | ".join(wp.check_repo(self.tmp))

    def test_real_repository_clean(self):
        self.assertEqual(wp.check_repo(ROOT), [])
        self.assertEqual(self.errors(), "")

    def test_toolchain_drift(self):
        self._edit("go.mod", "toolchain go1.27.4", "toolchain go1.24.9")
        self.assertIn("toolchain directive", self.errors())

    def test_toolchain_missing(self):
        self._edit("go.mod", "toolchain go1.27.4\n", "")
        self.assertIn("toolchain directive []", self.errors())

    def test_pyyaml_pin_drift(self):
        self._edit("buildscripts/ci/tools.env", "PYYAML_VERSION=6.0.3", "PYYAML_VERSION=6.0.2")
        self.assertIn("PYYAML_VERSION", self.errors())

    def test_pyyaml_lock_drift(self):
        self._edit("buildscripts/ci/requirements-ci.txt", "PyYAML==6.0.3", "PyYAML==6.0.2")
        self.assertIn("requirements-ci.txt PyYAML pins", self.errors())

    def test_tool_version_without_checksum(self):
        self._edit("buildscripts/ci/tools.env", "HELM_VERSION=3.22.1", "HELM_VERSION=3.22.2")
        errs = self.errors()
        self.assertIn("no pinned sha256 for helm 3.22.2 amd64", errs)
        self.assertIn("no pinned sha256 for helm 3.22.2 arm64", errs)

    def test_floating_go_tool(self):
        self._edit("buildscripts/ci/tools.env", "GOVULNCHECK_VERSION=v1.9.0", "GOVULNCHECK_VERSION=latest")
        self.assertIn("GOVULNCHECK_VERSION must be an exact", self.errors())

    def test_malformed_tools_env(self):
        self._edit("buildscripts/ci/tools.env", "SHFMT_VERSION=v3.14.1", "export SHFMT_VERSION=v3.14.1")
        self.assertIn("not a plain NAME=value line", self.errors())


class RepositoryMatrix(unittest.TestCase):
    """Derived run/no-run for the real tree, computed from parsed events."""

    @classmethod
    def setUpClass(cls):
        cls.report = wp.validate_dir(WF_DIR)

    def test_no_errors_and_exact_inventory(self):
        self.assertEqual(self.report["errors"], [])
        self.assertEqual(sorted(self.report["workflows"]), sorted(wp.INVENTORY))
        self.assertEqual(wp.validate_dir(WF_DIR, makefile=os.path.join(ROOT, "Makefile"))["errors"], [])

    def test_no_warnings_left(self):
        for name, entry in self.report["workflows"].items():
            with self.subTest(workflow=name):
                self.assertEqual(entry["warnings"], [])

    def test_race_is_manual_only(self):
        runs_race = {n for n, e in self.report["workflows"].items()
                     if set(e["make_targets"]) & set(wp.MANUAL_ONLY_TARGETS)}
        self.assertEqual(runs_race, {"go-race.yml"})
        self.assertEqual(self.report["workflows"]["go-race.yml"]["runs"], ["dispatch"])

    def test_no_forbidden_scenario_runs(self):
        forbidden = {s["id"] for s in wp.SCENARIOS if s["event"] not in ("workflow_dispatch", "pull_request")}
        forbidden |= {s["id"] for s in wp.SCENARIOS
                      if s["event"] == "pull_request" and s["action"] != wp.READY}
        for name, entry in self.report["workflows"].items():
            with self.subTest(workflow=name):
                runs = {k for k, v in wp.run_matrix(entry["events"]).items() if v}
                self.assertFalse(runs & forbidden, runs & forbidden)
                if entry["class"] == wp.HEAVY:
                    self.assertFalse(any(r.startswith("pr-") for r in runs), runs)

    def test_cli_exit_codes(self):
        ok = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        tmp = tempfile.mkdtemp(prefix="wfpolicy-cli-")
        try:
            bad = subprocess.run([sys.executable, SCRIPT, "--workflows-dir", tmp], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1, bad.stdout)
            self.assertIn("classified workflow is missing", bad.stdout)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
