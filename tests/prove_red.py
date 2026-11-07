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

SORT_RAW = ('sort "$TMP/raw" > "$TMP/all"', 'cat "$TMP/raw" > "$TMP/all"')
SELF_CHECK = ('$AWK -v mode=check "$GAPS_AWK" "$TMP/view.body" || die "$VERB: refusing to emit a view with an incomplete gap record"', 'true')

# Each fault is (name, old, new) or (name, [(old, new), ...]).  Every old text must occur exactly once.
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
     'ident_of "$TMP/norm.$_s" >> "$TMP/ids"', 'sha_file "$1" >> "$TMP/ids"'),
    ("identity depends on member order",
     [SORT_RAW, ('"$TMP/all" | sort\n}', '"$TMP/all"\n}')]),
    ("the file identity depends on the order of the spokes in the file",
     [SORT_RAW, ("{ printf 'orgfs-file-1\\n'; sort \"$TMP/ids\"; }", "{ printf 'orgfs-file-1\\n'; cat \"$TMP/ids\"; }")]),
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
    # ---- 0.2.0: views, gaps, back-links, routes
    ("materialize drops a gap record (a private spoke's member-root gap)",
     '[ "$vs_acc" != none ] || gap_member_root "$vs_h"', 'true'),
    ("materialize drops the slice gaps",
     '[ "$vo_bytes" = 1 ] || gap_slice_bytes', 'true || gap_slice_bytes'),
    ("a gap record lacks its WHY",
     ':HOW: %s\\n:WHY: %s\\n:REACH: %s\\n', ':HOW: %s\\n:REACH: %s\\n'),
    ("a gap record lacks its declaring line (and the self-check is off)",
     [SELF_CHECK, ('    "$(at_spoke VISIBILITY)" \\\n    "$VERB did not read the stores', '    "" \\\n    "$VERB did not read the stores')]),
    ("a gap record lacks its routes (and the self-check is off)",
     [SELF_CHECK, ('  routes_for assemble "--spoke $SPOKE --into DIR"\n  gap_close', '  gap_close')]),
    ("routes accepts an incomplete gap record",
     'if (!ok) { bad = 1; return }', 'if (!ok) { return }'),
    ("the back-link row carries the wrong view id",
     'vm_row="$VIEWID$TAB$VHOST', 'vm_row="x$VIEWID$TAB$VHOST'),
    ("the back-link row is never written",
     "printf '%s\\n' \"$vm_row\" >> \"$vm_d/MATERIALIZED\"", 'true'),
    ("trace accepts a source that does not list the view",
     "'$1==v&&$2==h{f=1} END{exit f?0:1}'", "'{f=1} END{exit f?0:1}'"),
    ("the view-id is not bound to the content",
     'VIEWID=$(sha_file "$TMP/view.body")', 'VIEWID=$(sha_str fixed)'),
    ("a view prints a private spoke's roots to a non-member",
     "[ \"$vs_acc\" = none ] || printf ':ROOT: %s\\n' \"$(root_of \"$vs_h\")\"", "printf ':ROOT: %s\\n' \"$(root_of \"$vs_h\")\""),
    ("a sealed spoke is shown to a non-member",
     'if [ "$VIS" = sealed ] && [ "$vs_acc" != member ]; then gap_sealed; return 0; fi', 'if false; then gap_sealed; return 0; fi'),
    ("assemble reads a private store for a non-member",
     'elif [ "$VIS" = public ]; then vs_acc=read; else vs_acc=none; fi', 'elif true; then vs_acc=read; else vs_acc=none; fi'),
    ("assemble ignores the object grain (every object gathered)",
     'if [ -n "$G_OBJECT" ] && [ "$SPOKE" = "$G_OBJSPOKE" ] && [ "$vs_o" != "$G_OBJECT" ]; then', 'if false; then'),
    ("assemble ignores --spoke (every spoke gathered)",
     'if [ -n "$G_SPOKE" ] && [ "$G_SPOKE" != "$SPOKE" ]; then gap_spoke_grain; return 0; fi', ':'),
    # ---- 0.2.0: layout
    ("layout passes a /c4 owned by the wrong user",
     'lay broken "$1" "$2 is owned by uid', 'lay held "$1" "$2 is owned by uid'),
    ("layout calls a missing /c4 held",
     'else lay broken c4-present', 'else lay held c4-present'),
    # ---- 0.2.0: the look and the demo page
    ("the link colour fails WCAG AA",
     '--link: #0b3d91;', '--link: #9aa0a6;'),
    ("the focus outline is removed",
     'a:focus-visible, .scroll:focus-visible, pre:focus-visible { outline: 3px solid var(--fg); outline-offset: 3px; }',
     'a:focus-visible, .scroll:focus-visible, pre:focus-visible { outline: none; }'),
    ("the print stylesheet leaves the navigation in",
     '  nav, .skip { display: none; }\n', '  .skip { display: none; }\n'),
    ("each element gets its own width again (a ch measure per child)",
     '  margin: 0 auto; max-width: var(--measure); padding: 0 1.25rem 4rem;\n}\n',
     '  margin: 0 auto; max-width: 60rem; padding: 0 1.25rem 4rem;\n}\nmain > * { max-width: 66ch; }\n'),
    ("the demo page lists fewer gaps than the view holds",
     '$AWK -v mode=tsv "$GAPS_AWK" "$_w/visitor.org" |', '$AWK -v mode=tsv "$GAPS_AWK" "$_w/visitor.org" | head -n 3 |'),
]


def pairs(old, new):
    return old if isinstance(old, list) else [(old, new)]


def plant(src, old, new):
    for o, n in pairs(old, new):
        src = src.replace(o, n)
    return src


def unpack(fault):
    return fault if len(fault) == 3 else (fault[0], fault[1], None)


def run_suite(binpath, failfast=False):
    env = dict(os.environ, ORGFS_BIN=binpath, ORGFS_SHARE=os.path.join(ROOT, "share"))
    p = subprocess.run([sys.executable, os.path.join(HERE, "run_all.py")] + (["-f"] if failfast else []),
                       capture_output=True, env=env, timeout=900)
    err = p.stderr.decode()
    failing = re.findall(r"^(?:FAIL|ERROR): (\w+)", err, re.M)
    return p.returncode, failing, err


def try_fault(td, src, idx, fault):
    name, old, new = unpack(fault)
    d = os.path.join(td, "f%d" % idx)
    os.makedirs(os.path.join(d, "bin"))
    shutil.copytree(os.path.join(ROOT, "share"), os.path.join(d, "share"))
    copy = os.path.join(d, "bin", "orgfs")
    with open(copy, "w") as f:
        f.write(plant(src, old, new))
    os.chmod(copy, 0o755)
    rc, failing, err = run_suite(copy, failfast=True)
    return name, rc, failing


def main():
    with open(os.path.join(ROOT, "bin", "orgfs")) as f:
        src = f.read()
    for ft in FAULTS:
        name, old, new = unpack(ft)
        for o, _ in pairs(old, new):
            if src.count(o) != 1:
                print("PLANT ERROR (text found %d times, want 1): %s" % (src.count(o), name))
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
