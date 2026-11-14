#!/usr/bin/env python3
# CI maintenance helper for the unofficial relvo-labs/granary fork; not part of
# or endorsed by upstream MinIO.  Distributed under the repository's license.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Install the hash-locked PyYAML wheel without pip or a venv (issue #3).

Usage: fetch_pyyaml.py DEST_DIR

The allowed hashes come from buildscripts/ci/requirements-ci.txt.  The wheel
for the running CPython (manylinux, x86_64 or aarch64) is located through the
PyPI JSON API, downloaded, and accepted only if its SHA-256 is in the
committed hash set and matches the published digest.  It is then unpacked
into DEST_DIR, which callers put first on PYTHONPATH.
"""

import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, "requirements-ci.txt")


def locked():
    text = open(LOCK).read()
    m = re.search(r"^PyYAML==(\S+)", text, re.M)
    if not m:
        sys.exit("fetch_pyyaml: no PyYAML pin in %s" % LOCK)
    hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", text))
    if not hashes:
        sys.exit("fetch_pyyaml: no hashes in %s" % LOCK)
    return m.group(1), hashes


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: fetch_pyyaml.py DEST_DIR")
    dest = os.path.abspath(argv[1])
    version, hashes = locked()
    machine = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(
        platform.machine().lower())
    if sys.platform != "linux" or machine is None or platform.python_implementation() != "CPython":
        sys.exit("fetch_pyyaml: unsupported interpreter/platform")
    tag = "cp%d%d" % sys.version_info[:2]
    url = "https://pypi.org/pypi/PyYAML/%s/json" % version
    with urllib.request.urlopen(url, timeout=60) as resp:
        meta = json.load(resp)
    if meta["info"]["version"] != version:
        sys.exit("fetch_pyyaml: PyPI returned version %s" % meta["info"]["version"])
    pattern = re.compile(r"^pyyaml-%s-%s-%s-manylinux.*_%s\.whl$" % (re.escape(version), tag, tag, machine))
    files = [f for f in meta["urls"] if pattern.match(f["filename"])]
    if len(files) != 1:
        sys.exit("fetch_pyyaml: expected one wheel for %s/%s, found %d" % (tag, machine, len(files)))
    entry = files[0]
    published = entry["digests"]["sha256"]
    if published not in hashes:
        sys.exit("fetch_pyyaml: published digest of %s is not in the hash lock" % entry["filename"])
    if not entry["url"].startswith("https://files.pythonhosted.org/"):
        sys.exit("fetch_pyyaml: unexpected download host")
    with urllib.request.urlopen(entry["url"], timeout=120) as resp:
        blob = resp.read()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != published:
        sys.exit("fetch_pyyaml: checksum mismatch for %s" % entry["filename"])
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".pyyaml-", dir=parent)
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                norm = os.path.normpath(name)
                if norm.startswith("..") or os.path.isabs(norm):
                    sys.exit("fetch_pyyaml: unsafe path in wheel: %s" % name)
            zf.extractall(tmp)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.rename(tmp, dest)
    finally:
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
    print("pyyaml %s: %s (%s sha256=%s)" % (version, dest, entry["filename"], actual))


if __name__ == "__main__":
    main(sys.argv)
