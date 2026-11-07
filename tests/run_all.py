#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run every test module at once (they are independent) and say what happened.

  run_all.py        run all modules, report each one's last lines, exit 1 if any failed
  run_all.py -f     fail fast: stop everything when one module fails (prove_red uses this)
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    fast = "-f" in sys.argv[1:]
    mods = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    procs = {m: subprocess.Popen([sys.executable, m] + (["-f"] if fast else []),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for m in mods}
    done, failed = {}, False
    while len(done) < len(procs):
        for m, p in procs.items():
            if m in done or p.poll() is None:
                continue
            out, err = p.communicate()
            done[m] = (p.returncode, out.decode(), err.decode())
            if p.returncode != 0:
                failed = True
        if failed and fast:
            for m, p in procs.items():
                if m not in done:
                    p.kill()
                    p.communicate()
                    done[m] = (-9, "", "")
            break
        time.sleep(0.2)
    total = 0
    for m in mods:
        rc, out, err = done[m]
        sys.stderr.write(err if rc != 0 else "")
        tail = [l for l in err.splitlines() if l.startswith("Ran ")]
        if tail:
            total += int(tail[0].split()[1])
        print("%-18s %s" % (os.path.basename(m), "ok" if rc == 0 else "FAILED") + ("  (%s)" % tail[0] if tail else ""))
    if failed:
        print("check: FAILED")
        return 1
    print("check: OK (%d tests in %d modules)" % (total, len(mods)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
