# Granary is an unofficial fork derived from MinIO.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adversarial tests for the build metadata of gen-ldflags.go and the
check-version-stamp.sh gate (#29, #34).  Requires `go` on PATH."""

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GEN_LDFLAGS = ROOT / "buildscripts/gen-ldflags.go"
CHECK = ROOT / "buildscripts/check-version-stamp.sh"

COMMIT = "1525c541e95ded8a2221eedaf716cdfb5e4612f2"
OTHER = "0123456789abcdef0123456789abcdef01234567"
TIME = "2026-11-14T10:04:42Z"
INJECT = "test -X github.com/relvo-labs/granary/cmd.Version=2000-01-01T00:00:00Z"


def gen_ldflags(*args, release="", hotfix=""):
    env = dict(os.environ, MINIO_RELEASE=release, MINIO_HOTFIX=hotfix)
    return subprocess.run(
        ["go", "run", str(GEN_LDFLAGS), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


class GenLDFlags(unittest.TestCase):
    def test_valid_metadata(self):
        for release, hotfix in [("", ""), ("RELEASE", ""), ("RELEASE", "hotfix.1a2b3c4d"), ("v1_2-3", "x"), ("R" * 64, "h" * 64)]:
            with self.subTest(release=release, hotfix=hotfix):
                result = gen_ldflags(TIME, COMMIT, release=release, hotfix=hotfix)
                self.assertEqual(result.returncode, 0, result.stderr)
                flags = result.stdout.split()
                self.assertEqual(flags.count("-X"), 7)
                self.assertIn(f"github.com/relvo-labs/granary/cmd.CommitID={COMMIT}", flags)
                self.assertIn(f"github.com/relvo-labs/granary/cmd.Version={TIME}", flags)

    def test_rejects_unsafe_release_metadata(self):
        bad = [
            "with space",
            INJECT,
            "-X",
            "a\nb",
            "a\tb",
            "'quoted'",
            '"quoted"',
            "a=b",
            "a/b",
            "x" * 65,
        ]
        for value in bad:
            for name in ("release", "hotfix"):
                with self.subTest(name=name, value=value):
                    result = gen_ldflags(TIME, COMMIT, **{name: value})
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("MINIO_" + ("RELEASE" if name == "release" else "HOTFIX"), result.stderr)

    def test_rejects_unsafe_commit(self):
        bad = [
            COMMIT[:12],
            COMMIT.upper(),
            COMMIT + "\n" + OTHER,
            COMMIT + " -X github.com/relvo-labs/granary/cmd.Version=2000-01-01T00:00:00Z",
            "'" + COMMIT + "'",
            "",
        ]
        for value in bad:
            with self.subTest(value=value):
                result = gen_ldflags(TIME, value)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn("commit", result.stderr)


class CheckVersionStamp(unittest.TestCase):
    def check(self, name, banner, commit=COMMIT):
        """Run the gate on a fake binary NAME that prints BANNER for --version."""
        with tempfile.TemporaryDirectory() as scratch:
            binary = pathlib.Path(scratch) / name
            fixture = pathlib.Path(scratch) / "banner.txt"
            fixture.write_text(banner)
            binary.write_text(f"#!/bin/sh\ncat '{fixture}'\n")
            binary.chmod(0o755)
            return subprocess.run(
                ["bash", str(CHECK), str(binary), commit],
                capture_output=True,
                text=True,
                timeout=60,
            )

    @staticmethod
    def banner(name, release, commit):
        return (
            f"{name} version {release} (commit-id={commit})\n"
            "Runtime: go1.27.4 linux/amd64\n"
            "License: GNU AGPLv3 - https://www.gnu.org/licenses/agpl-3.0.html\n"
            "Copyright: 2015-2026 MinIO, Inc.\n"
        )

    def test_correct_full_commit_passes(self):
        result = self.check("granary", self.banner("granary", "DEVELOPMENT.2026-11-14T10-04-42Z", COMMIT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version stamp OK", result.stdout)

    def test_expected_commit_in_binary_name_fails(self):
        name = "granary-" + COMMIT[:12]
        result = self.check(name, self.banner(name, "DEVELOPMENT.2026-11-14T10-04-42Z", OTHER))
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_expected_commit_in_release_suffix_fails(self):
        for suffix in (COMMIT, COMMIT[:12]):
            with self.subTest(suffix=suffix):
                release = "DEVELOPMENT.2026-11-14T10-04-42Z." + suffix
                result = self.check("granary", self.banner("granary", release, OTHER))
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_short_or_extended_commit_field_fails(self):
        for value in (COMMIT[:12], COMMIT + "0", COMMIT + " " + COMMIT, ""):
            with self.subTest(value=value):
                result = self.check("granary", self.banner("granary", "DEVELOPMENT.2026-11-14T10-04-42Z", value))
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_missing_commit_field_fails(self):
        result = self.check("granary", f"granary version DEVELOPMENT.2026-11-14T10-04-42Z {COMMIT}\n")
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_unstamped_binary_fails(self):
        result = self.check("granary", self.banner("granary", "DEVELOPMENT.GOGET", "DEVELOPMENT.GOGET"))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("DEVELOPMENT.GOGET", result.stderr)


if __name__ == "__main__":
    unittest.main()
