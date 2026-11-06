#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plant faults in a COPY of bin/orgfs and show each one turning the tests red.

The control run (no fault) must pass; every fault must make the suite fail.
A fault whose text is not found in the tool is an error, not a pass.
Python 3 standard library only.
"""
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAULTS = [
    ("plan differs between runs (pid in the hash)",
     'sha_str "$SPOKE|$_i|$_h"', 'sha_str "$SPOKE|$_i|$_h|$$"'),
    ("plan differs between hosts (cwd in the hash)",
     'sha_str "$SPOKE|$_i|$_h"', 'sha_str "$SPOKE|$_i|$_h|$PWD"'),
    ("plan picks the lowest scores, not the highest",
     '| sort -r | head -n "$REPLICAS"', '| sort | head -n "$REPLICAS"'),
    ("verify accepts a dropped slice",
     'if [ "$v_good" -ge "$REPLICAS" ]; then :; else', 'if [ "$v_good" -ge 0 ]; then :; else'),
    ("verify counts a corrupt copy as good",
     'elif slice_ok "$v_f" "$v_size" "$v_sha"; then v_good', 'elif [ -f "$v_f" ]; then v_good'),
    ("get skips the per-slice hash",
     '  [ "$(sha_file "$1")" = "$3" ]', '  true'),
    ("get skips the whole-file hash",
     'whole_ok() { # FILE ID\n  [ "$(sha_file "$1")" = "$2" ]', 'whole_ok() { # FILE ID\n  true'),
    ("get accepts a corrupt slice (both hashes skipped)",
     '  [ "$(sha_file "$1")" = "$3" ]\n}\nwhole_ok() { # FILE ID\n  [ "$(sha_file "$1")" = "$2" ]',
     '  true\n}\nwhole_ok() { # FILE ID\n  true'),
    ("agree accepts different declarations",
     'elif [ "$_id" != "$_first" ]; then _differ=1; fi', 'elif false; then _differ=1; fi'),
    ("identity hashes the raw file, prose and all",
     'ID=$(ident_of "$TMP/norm")', 'ID=$(sha_file "$1")'),
    ("identity depends on member order",
     'sort "$TMP/raw" > "$2"', 'cat "$TMP/raw" > "$2"'),
    ("check accepts an unmounted sshfs root",
     "$2==p{f=1}", "{f=1}"),
    ("put writes into a sealed spoke",
     '[ "$VIS" != sealed ] || die "put:', '[ "$VIS" != sealed ] || echo "put:'),
    ("the site carries an inline script",
     '<a class="skip" href="#main">Skip to main content</a>',
     '<a class="skip" href="#main">Skip to main content</a>\n<script>document.title="x"</script>'),
    ("the site carries a style attribute",
     '<p class="brand">orgfs</p>', '<p class="brand" style="color:red">orgfs</p>'),
    ("the site loads a style sheet from another host",
     '<link rel="stylesheet" href="orgfs.css">',
     '<link rel="stylesheet" href="orgfs.css">\n<link rel="stylesheet" href="https://fonts.example/x.css">'),
]


def run_suite(binpath, failfast=False):
    env = dict(os.environ, ORGFS_BIN=binpath, ORGFS_SHARE=os.path.join(ROOT, "share"))
    p = subprocess.run([sys.executable, os.path.join(HERE, "test_orgfs.py")] + (["-f"] if failfast else []),
                       capture_output=True, env=env, timeout=900)
    err = p.stderr.decode()
    failing = re.findall(r"^(?:FAIL|ERROR): (\w+)", err, re.M)
    return p.returncode, failing, err


def try_fault(td, src, idx, fault):
    name, old, new = fault
    d = os.path.join(td, "f%d" % idx)
    os.makedirs(os.path.join(d, "bin"))
    shutil.copytree(os.path.join(ROOT, "share"), os.path.join(d, "share"))
    copy = os.path.join(d, "bin", "orgfs")
    with open(copy, "w") as f:
        f.write(src.replace(old, new))
    os.chmod(copy, 0o755)
    rc, failing, err = run_suite(copy, failfast=True)
    return name, rc, failing


def main():
    with open(os.path.join(ROOT, "bin", "orgfs")) as f:
        src = f.read()
    for name, old, new in FAULTS:
        if src.count(old) != 1:
            print("PLANT ERROR (text found %d times, want 1): %s" % (src.count(old), name))
            return 1
    # PROVE_RED_RANGE=a:b runs only FAULTS[a:b] (for hosts with short time limits);
    # a partial run never prints the OK line.
    rng = os.environ.get("PROVE_RED_RANGE")
    todo = FAULTS
    if rng:
        a, b = rng.split(":")
        todo = FAULTS[int(a or 0):int(b or len(FAULTS))]
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "bin"))
        shutil.copytree(os.path.join(ROOT, "share"), os.path.join(td, "share"))
        copy = os.path.join(td, "bin", "orgfs")
        with open(copy, "w") as f:
            f.write(src)
        os.chmod(copy, 0o755)
        jobs = int(os.environ.get("PROVE_RED_JOBS", "8"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(try_fault, td, src, i, ft) for i, ft in enumerate(todo)]
            ctl = ex.submit(run_suite, copy)
            results = [f.result() for f in futs]
            rc, failing, err = ctl.result()
        if rc != 0:
            print("control FAILED on the untouched copy:", failing)
            print(err[-2000:])
            return 1
        print("control: passed on the untouched copy")
        caught = 0
        for name, rc, failing in results:
            if rc != 0 and failing:
                caught += 1
                u = sorted(set(failing))
                print("caught: %s  [%s%s]" % (name, ", ".join(u[:3]), " ..." if len(u) > 3 else ""))
            else:
                print("MISSED: %s (rc=%d)" % (name, rc))
        if rng:
            print("PARTIAL (%d of %d in range %s caught; control passed)" % (caught, len(todo), rng))
            return 0 if caught == len(todo) else 1
        if caught != len(FAULTS):
            print("PROVE-RED FAILED (%d of %d caught)" % (caught, len(FAULTS)))
            return 1
        print("PROVE-RED OK (%d of %d caught; control passed)" % (caught, len(FAULTS)))
        return 0


if __name__ == "__main__":
    sys.exit(main())
