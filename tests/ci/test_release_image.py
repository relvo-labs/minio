# Granary is an unofficial fork derived from MinIO.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release preconditions and orchestration with fake external commands.

No Docker daemon, network, host credentials or real publication is used.
The separate full multi-arch dry run remains a required qualification gate.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'buildscripts/release-image.sh'
COMMIT = '0123456789abcdef0123456789abcdef01234567'

STUB = r'''
import io, json, os, pathlib, sys, tarfile
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
mode = os.environ.get('RELEASE_TEST_MODE', '')
commit = '0123456789abcdef0123456789abcdef01234567'
with open(os.environ['RELEASE_TEST_CALLS'], 'a') as log:
    log.write(json.dumps([tool, *args]) + '\n')
if tool == 'git':
    if args[0] == 'status':
        if mode == 'dirty': print('?? untracked-fixture')
    elif args[0] == 'rev-parse':
        print(os.environ['RELEASE_TEST_ROOT'] if args[1] == '--show-toplevel'
              else 'true' if args[1] == '--is-shallow-repository' and mode == 'shallow'
              else 'false' if args[1] == '--is-shallow-repository' else commit)
    elif args[0] == 'fetch':
        sys.exit(1 if mode == 'fetch-error' else 0)
    elif args[0] == 'merge-base':
        sys.exit(1 if mode == 'off-master' else 0)
    elif args[0] == 'log': print('1794650682')
    elif args[0] == 'archive':
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|'): pass
    else: sys.exit('unexpected git call')
elif tool == 'go':
    if args[0] == 'env': print('go1.27.4')
    elif mode == 'stamp-error': sys.exit(1)
    elif mode != 'empty-stamp':
        release = 'RELEASE.' + args[-2].replace(':', '-') + '.v2026.11.15'
        for key, value in [('Version', args[-2]), ('CommitID', commit), ('ReleaseTag', release)]:
            print('-X github.com/relvo-labs/granary/cmd.' + key + '=' + value, end=' ')
elif tool == 'curl':
    if mode == 'network-error': sys.exit(7)
    url = args[-1]
    status = '200' if '?' in url or mode == 'existing-tag' else '404'
    data = {'message': 'httperror 404: object not found'}
    if '?' in url:
        data = {'results': [{'name': 'v2026.11.16'}] if mode == 'older-version' else [], 'next': None}
    if mode == 'bad-json': body = '<html>not found</html>'
    else: body = json.dumps(data)
    pathlib.Path(args[args.index('--output') + 1]).write_text(body)
    print('429' if mode == 'rate-limited' else status, end='')
elif tool == 'docker':
    if args[0] == 'run':
        arch = args[args.index('--platform') + 1].split('/')[1]
        if mode == 'no-qemu' and arch == 'arm64': sys.exit(126)
        stamp = 'f' * 40 if mode == 'wrong-arm-commit' and arch == 'arm64' else commit
        runtime = 'amd64' if mode == 'wrong-arm-runtime' else arch
        print('granary version RELEASE.2026-11-14T10-04-42Z.v2026.11.15 (commit-id=' + stamp + ')')
        print('Runtime: go1.27.4 linux/' + runtime)
    elif args[:3] == ['buildx', 'imagetools', 'inspect']:
        if '--raw' in args:
            arches = ['amd64'] if mode == 'missing-platform' else ['amd64', 'arm64']
            print(json.dumps({'manifests': [{'platform': {'os': 'linux', 'architecture': a}} for a in arches]}))
        elif '--format' in args:
            digest = 'b' if mode == 'different-digests' and args[-1].endswith(':latest') else 'a'
            print(json.dumps({'digest': 'sha256:' + digest * 64}))
    elif args[:2] == ['buildx', 'build'] and mode == 'build-error': sys.exit(1)
else: sys.exit('unexpected tool')
'''


class ReleaseImage(unittest.TestCase):
    def run_script(self, mode='', version='v2026.11.15', extra=(), extra_env=None):
        with tempfile.TemporaryDirectory(prefix='release-image-test-') as directory:
            scratch = pathlib.Path(directory)
            calls = scratch / 'calls'
            for name in ('git', 'curl', 'docker', 'go'):
                stub = scratch / name
                stub.write_text('#!' + sys.executable + '\n' + STUB)
                stub.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                       RELEASE_TEST_ROOT=str(ROOT), RELEASE_TEST_MODE=mode,
                       RELEASE_TEST_CALLS=str(calls), VERSION='', TMPDIR=directory,
                       DOCKER_CONFIG=str(scratch / 'must-not-read-credentials'))
            env.update(extra_env or {})
            result = subprocess.run(['bash', str(SCRIPT), *extra, version], env=env,
                                    capture_output=True, text=True, timeout=60)
            import json
            commands = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            return result, commands

    def rejected(self, mode, message, **kwargs):
        result, calls = self.run_script(mode, **kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(any('--push' in c or 'login' in c for c in calls))
        return calls

    def test_bad_version_and_invalid_calendar_date(self):
        for version in ('2026.11.15', 'v2026.2.01', 'v2026.02.30', 'v0000.01.01', 'v2026.11.15\n'):
            with self.subTest(version=version):
                self.rejected('', 'VERSION must', version=version)

    def test_dirty_tree(self):
        calls = self.rejected('dirty', 'clean worktree required')
        self.assertFalse(any(c[1] == 'fetch' for c in calls))

    def test_wrong_exact_commit(self):
        self.rejected('', 'HEAD must equal', extra=('--commit', 'f' * 40))

    def test_not_on_master(self):
        calls = self.rejected('off-master', 'HEAD must be on freshly fetched origin/master')
        self.assertTrue(any(c[1] == 'fetch' for c in calls))
        self.assertFalse(any(c[0] == 'docker' for c in calls))

    def test_failed_fetch(self):
        self.rejected('fetch-error', 'fresh origin/master fetch failed')

    def test_existing_version_tag(self):
        calls = self.rejected('existing-tag', 'already exists; never overwrite')
        self.assertFalse(any(c[0] == 'docker' for c in calls))

    def test_registry_errors_fail_closed(self):
        for mode in ('network-error', 'rate-limited', 'bad-json'):
            with self.subTest(mode=mode):
                self.rejected(mode, 'Docker Hub')

    def test_latest_cannot_move_backwards(self):
        self.rejected('older-version', 'newer than every published version')

    def test_failed_or_empty_stamping(self):
        for mode in ('stamp-error', 'empty-stamp'):
            with self.subTest(mode=mode):
                calls = self.rejected(mode, 'version stamping')
                self.assertFalse(any(c[0] in ('docker', 'curl') for c in calls))

    def test_python_environment_cannot_disable_checks(self):
        for mode in ('empty-stamp', 'wrong-arm-runtime'):
            with self.subTest(mode=mode):
                self.rejected(mode, 'version', extra_env={'PYTHONOPTIMIZE': '1'})

    def test_platform_verification_and_cleanup(self):
        for mode in ('wrong-arm-commit', 'wrong-arm-runtime', 'no-qemu', 'build-error'):
            with self.subTest(mode=mode):
                result, calls = self.run_script(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(any(c[1:3] == ['buildx', 'rm'] for c in calls))
                self.assertFalse(any('--push' in c or 'prune' in c for c in calls))

    def test_safe_default_verifies_both_platforms(self):
        result, calls = self.run_script('shallow')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('both platforms verified; nothing pushed', result.stdout)
        self.assertTrue(any('--unshallow' in c for c in calls))
        runs = [c for c in calls if c[:2] == ['docker', 'run']]
        self.assertEqual([c[c.index('--platform') + 1] for c in runs], ['linux/amd64', 'linux/arm64'])
        self.assertFalse(any('--push' in c or 'login' in c or 'prune' in c for c in calls))

    def test_mock_publication_readback(self):
        # All docker/curl/git/go calls go to the local stubs above, including
        # the simulated push. No real publication command can execute here.
        for mode in ('', 'missing-platform', 'different-digests'):
            with self.subTest(mode=mode):
                result, calls = self.run_script(mode, extra=('--push',))
                self.assertEqual(result.returncode == 0, mode == '', result.stdout + result.stderr)
                push = next(i for i, call in enumerate(calls) if '--push' in call)
                runs = [i for i, call in enumerate(calls) if call[:2] == ['docker', 'run']]
                self.assertEqual(len(runs), 2)
                self.assertTrue(all(i < push for i in runs))
                self.assertTrue(any(call[1:3] == ['buildx', 'rm'] for call in calls))


if __name__ == '__main__':
    unittest.main()
