# Granary is an unofficial fork derived from MinIO.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Prevent integration fixtures from falling back to unavailable server images."""

import pathlib
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


class IntegrationImages(unittest.TestCase):
    def test_server_fixtures_are_local_and_cannot_pull(self):
        files = sorted((ROOT / ".github/workflows/mint").glob("*.yaml"))
        files += sorted((ROOT / ".github/workflows/multipart").glob("*.yaml"))
        files += [ROOT / "buildscripts/upgrade-tests/compose.yml"]
        self.assertEqual(len(files), 7)
        for path in files:
            with self.subTest(path=path.relative_to(ROOT)):
                doc = yaml.safe_load(path.read_text())
                servers = [s for name, s in doc["services"].items() if "minio" in name]
                self.assertTrue(servers)
                for server in servers:
                    self.assertTrue(server["image"].startswith("granary-ci:"))
                    self.assertIn(":?", server["image"], "tag must be explicitly supplied")
                    self.assertEqual(server["pull_policy"], "never")
                    if "healthcheck" in server:
                        self.assertNotIn("mc", server["healthcheck"]["test"])

    def test_historical_entrypoints_fail_before_external_operations(self):
        scripts = [
            ".github/workflows/multipart/migrate.sh",
            "buildscripts/minio-upgrade.sh",
            "buildscripts/minio-iam-ldap-upgrade-import-test.sh",
        ]
        with tempfile.TemporaryDirectory() as scratch:
            for script in scripts:
                with self.subTest(script=script):
                    # Empty PATH and cwd: no external tools or checkout-relative
                    # side effects may be needed to explain a disabled scenario.
                    result = subprocess.run(
                        ["/bin/bash", str(ROOT / script)], cwd=scratch,
                        env={"PATH": scratch}, capture_output=True, text=True,
                        timeout=5,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("DISABLED (#30)", result.stderr)
                    self.assertIn("unavailable upstream", result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(list(pathlib.Path(scratch).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
