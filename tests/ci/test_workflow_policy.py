# CI maintenance tests for the unofficial relvo-labs/minio fork; not part of
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
TAIL = "\npermissions:\n  contents: read\n" + JOBS


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
        os.remove(os.path.join(self.wf, "go-lint.yml"))
        self.assertIn("go-lint.yml: classified workflow is missing", self.errors())

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


class RepositoryMatrix(unittest.TestCase):
    """Derived run/no-run for the real tree, computed from parsed events."""

    @classmethod
    def setUpClass(cls):
        cls.report = wp.validate_dir(WF_DIR)

    def test_no_errors_and_exact_inventory(self):
        self.assertEqual(self.report["errors"], [])
        self.assertEqual(sorted(self.report["workflows"]), sorted(wp.INVENTORY))

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
