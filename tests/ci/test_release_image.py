# Granary is an unofficial fork derived from MinIO.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release preconditions and orchestration with fake external commands.

No Docker daemon, network, host credentials or real publication is used.
The separate full multi-arch dry run remains a required qualification gate.
"""
import datetime
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'buildscripts/release-image.sh'
COMMIT = '0123456789abcdef0123456789abcdef01234567'
VERSION = datetime.datetime.now(datetime.timezone.utc).strftime('v%Y.%m.%d')

STUB = r'''
import datetime, io, json, os, pathlib, signal, sys, tarfile
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
mode = os.environ.get('RELEASE_TEST_MODE', '')
commit = '0123456789abcdef0123456789abcdef01234567'
version = os.environ['RELEASE_TEST_VERSION']
pushed = pathlib.Path(os.environ['RELEASE_TEST_CALLS'] + '.pushed')
with open(os.environ['RELEASE_TEST_CALLS'], 'a') as log:
    log.write(json.dumps([tool, *args]) + '\n')
if tool == 'git':
    if args[0] == 'status':
        if mode == 'dirty': print('?? untracked-fixture')
    elif args[0] == 'rev-parse':
        print(os.environ['RELEASE_TEST_ROOT'] if args[1] == '--show-toplevel'
              else 'true' if args[1] == '--is-shallow-repository' and mode == 'shallow'
              else 'false' if args[1] == '--is-shallow-repository' else commit)
    elif args[:2] == ['remote', 'get-url']:
        print('https://example.invalid/other.git' if mode == 'wrong-origin'
              else 'https://github.com/relvo-labs/granary.git')
    elif args[0] == 'fetch':
        sys.exit(1 if mode == 'fetch-error' else 0)
    elif args[0] == 'merge-base':
        sys.exit(1 if mode == 'off-master' or
                 (mode == 'non-descendant' and args[-1] == commit) else 0)
    elif args[0] == 'log': print('1794650682')
    elif args[0] == 'archive':
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|'): pass
    else: sys.exit('unexpected git call')
elif tool == 'go':
    if args[0] == 'env': print('go1.27.4')
    elif mode == 'stamp-error': sys.exit(1)
    elif mode != 'empty-stamp':
        release = 'RELEASE.' + args[-2].replace(':', '-') + '.' + version
        for key, value in [('Version', args[-2]), ('CommitID', commit), ('ReleaseTag', release)]:
            print('-X github.com/relvo-labs/granary/cmd.' + key + '=' + value, end=' ')
elif tool == 'curl':
    if mode == 'network-error': sys.exit(7)
    url = args[-1]
    status = '200' if '?' in url or mode == 'existing-tag' or pushed.exists() else '404'
    if mode in ('private-tag', 'private-latest') or (
            mode == 'listing-not-found' and '?' in url) or (
            mode == 'anonymous-fails' and pushed.exists()): status = '404'
    data = {'message': 'httperror 404: object not found'}
    if '?' in url:
        data = {'results': [{'name': version}] if mode == 'older-version' else [], 'next': None}
    if status == '404': data = {'message': 'httperror 404: object not found'}
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
        print('granary version RELEASE.2026-11-14T10-04-42Z.' + version + ' (commit-id=' + stamp + ')')
        print('Runtime: go1.27.4 linux/' + runtime)
    elif args[:3] == ['buildx', 'imagetools', 'inspect']:
        if not pushed.exists():
            if mode == 'anonymous-denial':
                print('ERROR: pull access denied, repository does not exist or may require authorization: '
                      'server message: insufficient_scope: authorization failed', file=sys.stderr)
                sys.exit(1)
            latest = args[-1].endswith(':latest') or '@sha256:' in args[-1]
            if mode == 'tag-appears' and not latest and '--load' in pathlib.Path(os.environ['RELEASE_TEST_CALLS']).read_text():
                print(json.dumps({'digest': 'sha256:' + 'a' * 64}))
                sys.exit(0)
            if mode in ('inspect-denied', 'inspect-unknown', 'latest-denied', 'latest-unknown') and (
                    not mode.startswith('latest-') or latest):
                print('ERROR: unauthorized: access denied' if mode.endswith('denied') else
                      'ERROR: unexpected registry response', file=sys.stderr)
                sys.exit(1)
            if not ((mode in ('existing-tag', 'private-tag') and not latest) or
                    (mode in ('non-descendant', 'latest-present', 'private-latest', 'latest-newer',
                              'bad-labels', 'inconsistent-labels') and latest)):
                print('ERROR: manifest unknown: manifest unknown' if mode == 'manifest-unknown' else
                      'ERROR: ' + args[-1] + ': not found', file=sys.stderr)
                sys.exit(1)
        if os.environ['DOCKER_CONFIG'].endswith('anonymous-docker') and mode == 'anonymous-manifest-fails':
            sys.exit(1)
        if '--raw' in args:
            arches = ['amd64'] if mode == 'missing-platform' else ['amd64', 'arm64']
            print(json.dumps({'manifests': [{'platform': {'os': 'linux', 'architecture': a}} for a in arches]}))
        elif '--format' in args:
            if '.Image' in args[args.index('--format') + 1]:
                old = (datetime.date.fromisoformat(version[1:].replace('.', '-')) -
                       datetime.timedelta(days=1)).strftime('v%Y.%m.%d')
                labels = {'org.opencontainers.image.revision': 'b' * 40,
                          'org.opencontainers.image.version': version if mode == 'latest-newer' else old}
                if mode == 'bad-labels': labels = {}
                configs = {a: {'config': {'Labels': dict(labels)}} for a in ('linux/amd64', 'linux/arm64')}
                if mode == 'inconsistent-labels':
                    configs['linux/arm64']['config']['Labels']['org.opencontainers.image.revision'] = 'c' * 40
                print(json.dumps(configs))
            else:
                digest = 'b' if mode == 'different-digests' and args[-1].endswith(':latest') else 'a'
                print(json.dumps({'digest': 'sha256:' + digest * 64}))
    elif args[:2] == ['buildx', 'build']:
        if mode == 'build-error': sys.exit(1)
        if mode == 'build-exit42-cleanup-error': sys.exit(42)
        if mode == 'double-int':
            pathlib.Path(str(pushed) + '.shell').write_text(str(os.getppid()))
            os.kill(os.getppid(), signal.SIGINT)
        if '--push' in args:
            pushed.touch()
            if '--metadata-file' in args:
                metadata = {} if mode == 'missing-metadata' else {
                    'containerimage.digest': 'sha256:' + ('b' if mode == 'digest-mismatch' else 'a') * 64}
                pathlib.Path(args[args.index('--metadata-file') + 1]).write_text(json.dumps(metadata))
    elif args[:2] == ['image', 'rm']:
        if mode == 'double-int':
            os.kill(int(pathlib.Path(str(pushed) + '.shell').read_text()), signal.SIGINT)
        if mode in ('cleanup-error', 'build-exit42-cleanup-error'): sys.exit(1)
else: sys.exit('unexpected tool')
'''


class ReleaseImage(unittest.TestCase):
    def run_script(self, mode='', version=VERSION, extra=(), extra_env=None):
        with tempfile.TemporaryDirectory(prefix='release-image-test-') as directory:
            scratch = pathlib.Path(directory)
            calls = scratch / 'calls'
            for name in ('git', 'curl', 'docker', 'go'):
                stub = scratch / name
                stub.write_text('#!' + sys.executable + '\n' + STUB)
                stub.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                       RELEASE_TEST_ROOT=str(ROOT), RELEASE_TEST_MODE=mode,
                       RELEASE_TEST_VERSION=version,
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
        calls = self.rejected('existing-tag', 'already exists; never overwrite', extra=('--push',))
        self.assertFalse(any(c[1:3] == ['buildx', 'build'] for c in calls))

    def test_private_tag_cannot_be_overwritten(self):
        self.rejected('private-tag', 'already exists; never overwrite', extra=('--push',))

    def test_dry_run_skips_registry_checks_with_anonymous_denial(self):
        result, calls = self.run_script('anonymous-denial', extra=('--dry-run',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum('--load' in c for c in calls), 2)
        self.assertEqual(sum(c[:2] == ['docker', 'run'] for c in calls), 2)
        self.assertFalse(any(c[0] == 'curl' or c[1:4] == ['buildx', 'imagetools', 'inspect']
                             or '--push' in c for c in calls))

    def test_push_anonymous_denial_aborts_before_build(self):
        calls = self.rejected('anonymous-denial', 'authenticated lookup failed', extra=('--push',))
        self.assertFalse(any(c[1:3] in (['buildx', 'build'], ['buildx', 'create']) for c in calls))

    def test_push_requires_public_listing_even_without_candidate_or_latest(self):
        calls = self.rejected('listing-not-found',
                              'Docker Hub repository neil0628/granary must exist and be public before publishing',
                              extra=('--push',))
        inspected = [c[-1] for c in calls if c[1:4] == ['buildx', 'imagetools', 'inspect']]
        self.assertEqual(inspected, ['docker.io/neil0628/granary:' + VERSION,
                                     'docker.io/neil0628/granary:latest'])
        self.assertTrue(any(c[0] == 'curl' and '?page_size=100' in c[-1] for c in calls))
        self.assertFalse(any(c[1:3] in (['buildx', 'build'], ['buildx', 'create']) for c in calls))

    def test_tag_appearing_before_push_aborts(self):
        calls = self.rejected('tag-appears', 'already exists; never overwrite', extra=('--push',))
        self.assertEqual(sum('--load' in c for c in calls), 2)

    def test_explicit_manifest_unknown_allows_first_release(self):
        result, calls = self.run_script('manifest-unknown', extra=('--push',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum('--load' in c for c in calls), 2)

    def test_authenticated_inspect_failures_abort(self):
        for mode in ('inspect-denied', 'inspect-unknown', 'latest-denied', 'latest-unknown'):
            with self.subTest(mode=mode):
                self.rejected(mode, 'authenticated lookup failed', extra=('--push',))

    def test_latest_revision_and_version(self):
        self.rejected('non-descendant', 'descendant of current latest revision', extra=('--push',))
        self.rejected('latest-newer', 'newer than current latest', extra=('--push',))
        for mode in ('bad-labels', 'inconsistent-labels'):
            with self.subTest(mode=mode):
                self.rejected(mode, 'latest labels', extra=('--push',))
        self.rejected('private-latest', 'must exist and be public before publishing', extra=('--push',))
        result, _ = self.run_script('latest-present', extra=('--push',))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_origin(self):
        calls = self.rejected('wrong-origin', 'origin must be exactly')
        self.assertFalse(any(c[1] == 'fetch' for c in calls))

    def test_date_window(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        for offset in (-2, 2):
            self.rejected('', 'today UTC +/-1 day',
                          version=(today + datetime.timedelta(days=offset)).strftime('v%Y.%m.%d'))
        for offset in (-1, 0, 1):
            result, _ = self.run_script(version=(today + datetime.timedelta(days=offset)).strftime('v%Y.%m.%d'))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_second_interrupt_during_cleanup(self):
        result, calls = self.run_script('double-int')
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertTrue(any(c[1:3] == ['buildx', 'rm'] for c in calls))

    def test_cleanup_failures_are_independent_and_preserve_failure(self):
        for mode, code in (('cleanup-error', 1), ('build-exit42-cleanup-error', 42)):
            result, calls = self.run_script(mode)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertTrue(any(c[1:3] == ['buildx', 'rm'] for c in calls))

    def test_license_labels_and_buildkit_pin(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        create = next(c for c in calls if c[1:3] == ['buildx', 'create'])
        self.assertRegex(create[create.index('--driver-opt') + 1], r'^image=moby/buildkit@sha256:[0-9a-f]{64}$')
        for call in calls:
            if call[1:3] == ['buildx', 'build']:
                self.assertIn('org.opencontainers.image.licenses=AGPL-3.0-or-later', call)
        self.assertIn('org.opencontainers.image.licenses="AGPL-3.0-or-later"', (ROOT / 'Dockerfile').read_text())
        self.assertIn('--label org.opencontainers.image.licenses=AGPL-3.0-or-later', (ROOT / 'Makefile').read_text())

    def test_registry_errors_fail_closed(self):
        for mode in ('network-error', 'rate-limited', 'bad-json'):
            with self.subTest(mode=mode):
                self.rejected(mode, 'Docker Hub', extra=('--push',))

    def test_latest_cannot_move_backwards(self):
        self.rejected('older-version', 'newer than every published version', extra=('--push',))

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
        self.assertFalse(any(c[0] == 'curl' or c[1:4] == ['buildx', 'imagetools', 'inspect'] for c in calls))

    def test_mock_publication_readback(self):
        # All docker/curl/git/go calls go to the local stubs above, including
        # the simulated push. No real publication command can execute here.
        for mode in ('', 'missing-platform', 'different-digests', 'digest-mismatch',
                     'missing-metadata', 'anonymous-fails', 'anonymous-manifest-fails'):
            with self.subTest(mode=mode):
                result, calls = self.run_script(mode, extra=('--push',))
                self.assertEqual(result.returncode == 0, mode == '', result.stdout + result.stderr)
                push = next(i for i, call in enumerate(calls) if '--push' in call)
                runs = [i for i, call in enumerate(calls) if call[:2] == ['docker', 'run']]
                self.assertEqual(len(runs), 2)
                self.assertTrue(all(i < push for i in runs))
                self.assertTrue(any(call[1:3] == ['buildx', 'rm'] for call in calls))
                if mode in ('anonymous-fails', 'anonymous-manifest-fails'):
                    self.assertIn('set the Docker Hub repository public', result.stderr)
                if mode == 'digest-mismatch':
                    self.assertIn('pushed digest differ', result.stderr)


if __name__ == '__main__':
    unittest.main()
