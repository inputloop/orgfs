#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Bryant Frankford (INPUTLOOP)
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
    # ---- 0.3.0: atoms, composition, the manifest
    ("compose drops an atom (the first one is left out of the default story, and the missing-include check is off)",
     [('END { for (p in a) if (!(p in seen))', 'END { for (p in a) if (0)'),
      ("$AWK '{ if (NR > 1) print \"\"; printf \"#+INCLUDE: \\\"%s\\\"\\n\", $0 }' \"$TMP/atoms\" > \"$2\"",
       "$AWK 'NR == 1 { next } { print \"\"; printf \"#+INCLUDE: \\\"%s\\\"\\n\", $0 }' \"$TMP/atoms\" > \"$2\"")]),
    ("decompose loses the last line of every atom",
     'for (i = astart[k]; i <= aend[k]; i++) printf "%s\\n", L[i] > out', 'for (i = astart[k]; i < aend[k]; i++) printf "%s\\n", L[i] > out'),
    ("the identity differs between the atom directory and the composed file",
     '  parse_decl "$DECLFILE" "$1"\n',
     '  parse_decl "$DECLFILE" "$1"\n  if [ -d "$1" ]; then FILEID=$(sha_str "$FILEID"); fi\n'),
    ("RULE_AT points into the composed file instead of the atom",
     'function at(n) { return (atom != "") ? atom " line " arel[n] : "line " n }',
     'function at(n) { return "line " n }'),
    ("a composed file is read after an atom beside it changed (STALE printed, then used)",
     'stale_diff "$TMP/mf" "$TMP/fa" "in the atoms beside it, $cf_dir" || cf_rc=1',
     'stale_diff "$TMP/mf" "$TMP/fa" "in the atoms beside it, $cf_dir" || true'),
    ("an added atom that is not in the manifest is accepted",
     'for (p in n) if (!(p in m)) printf "orgfs: STALE: %s added since compose (%s)\\n", p, where',
     'for (p in n) if (0) printf "orgfs: STALE: %s added since compose (%s)\\n", p, where'),
    ("a composed file edited in place is not noticed",
     'stale_diff "$TMP/mf" "$TMP/fe" "in the composed file itself" || cf_rc=1', 'true'),
    ("a composed file read without its atoms says nothing",
     'warn "$2: composed file read without its atoms; freshness not checked"', 'true'),
    ("the manifest's identity is never compared",
     '[ "$MF_IDENT" != "$FILEID" ]', 'false'),
    ("decompose silently drops the story's own text (everything outside the atoms)",
     'if (nmf == 1 && i == mhead - 1 && blank(L[i])) continue', 'if (own[i] == 0) continue'),
    ("decompose overwrites an atom that differs",
     'if [ -e "$_dir/$_p" ] && ! cmp -s', 'if false && ! cmp -s'),
    ("compose accepts an atom that holds an #+INCLUDE of its own",
     'if (toupper(s) ~ /^[ \\t]*#\\+INCLUDE:/) f("line " i ": an atom holds no #+INCLUDE', 'if (0) f("line " i ": an atom holds no #+INCLUDE'),
    ("a displayed member does not say which atom declared it",
     [(":HOLDS: %s\\n:DECLARED_AT: %s\\n:END:\\n' \"$(holds_of \"$vs_h\")\" \"$(member_line \"$vs_h\" heading)\"", ":HOLDS: %s\\n:END:\\n' \"$(holds_of \"$vs_h\")\"")]),
    ("a member atom may sit in another spoke's directory",
     'else if (atom != "" && !member_ok(atom, sname, w[2]))', 'else if (0)'),
    # ---- 0.4.0: Org's own #+INCLUDE, the story, the manifest table
    ("compose lets Org change an atom's stars (the strict level check is off)",
     'if (strict && d == 1 && off != 0)', 'if (0 && strict && d == 1 && off != 0)'),
    ("an included file keeps its blank lines at head and tail (Org trims them)",
     'if (trim) { while (a <= b && isblank(T[d, a])) a++; while (b >= a && isblank(T[d, b])) b-- }', ''),
    ("an included file is not moved to the level the story sets",
     'if (off != 0) for (i = a; i <= b; i++) if (lvl(T[d, i]) > 0) {', 'if (0) for (i = a; i <= b; i++) if (lvl(T[d, i]) > 0) {'),
    (":lines on an include is quietly ignored",
     'if (w ~ /:lines/) die(":lines on #+INCLUDE is outside the supported subset")', ''),
    ("an include that names an absolute path is followed",
     'if (v ~ /^[\\/~]/) die("an absolute path is outside the supported subset: " v)', ''),
    ("a recursive include is followed",
     'for (k = 0; k <= d; k++) if (stack[k] == full) die("recursive inclusion of " p)', ''),
    ("an atom included twice by the story is accepted",
     'else if (seen[$0] > 1) {', 'else if (0) {'),
    ("compose writes a source form whose includes point nowhere (the relative prefix is dropped)",
     "$AWK -v pfx=\"${CD_PREFIX:-}\"", "$AWK -v pfx=\"\""),
    ("the manifest table loses its story.org row",
     "  printf '| story.org | %s |\\n' \"$(sha_file \"$TMP/story\")\"", "  true"),
    ("the manifest table is not given its #+NAME",
     "printf '#+NAME: orgfs-manifest\\n| atom | sha256 |", "printf '| atom | sha256 |"),
    ("the declaration's identity is not a property of the manifest heading",
     ":PROPERTIES:\\n:ORGFS_IDENTITY: %s\\n:END:\\n%s", ":PROPERTIES:\\n:END:\\n%s"),
    # ---- 0.4.0: Emacs reads what orgfs wrote (these run tests/org_check.sh on the faulted copy; no emacs, no verdict)
    ("compose writes a line Org would not produce (a comment marker above the composed text)",
     """      cat "$TMP/ebody"
    fi""", """      printf '# orgfs-compose: 1\\n'; cat "$TMP/ebody"
    fi""", "emacs"),
    ("an atom heading loses its :ORGFS_ATOM: property in the composed text",
     """      cat "$TMP/ebody"
    fi""", """      grep -v '^:ORGFS_ATOM: ' "$TMP/ebody"
    fi""", "emacs"),
    # ---- the home page tells the story; the working records are on made.html
    ("the home page loses its link to the declaration",
     '<p class="go"><a href="declaration.html">Read the declaration as a story</a></p>', '<p class="go">Read the declaration as a story</p>'),
    ("the home page links a declaration that is not published (a non-public spoke)",
     'if [ "$_allpublic" = 1 ]; then\n    cat <<EOF\n<p class="go">', 'if true; then\n    cat <<EOF\n<p class="go">'),
    ("the home page opens with the working records, not the example",
     '<h2>An example</h2>', '<h2>What was asked</h2>'),
    ("the home page hard-codes the number of pieces and copies",
     '"$(plur "$STRIPES" piece pieces)" "$REPLICAS" "$(n_members)"', '"5 pieces" "2" "$(n_members)"'),
    ("the home page ignores the copy count when it says how many machines can be lost",
     '    case $REPLICAS in\n      1) _lose=', '    case 2 in\n      1) _lose='),
    ("the scoring rule leaks onto the home page",
     '<p>Read the table by rows:', '<p>Score: SHA-256 of spoke|stripe|member. Read the table by rows:'),
    ("the home page's story is not the declaration's own words",
     '_why=$(decl_prose "$_out/declaration.org" 1 "" 2 2)', '_why="orgfs is a good idea."'),
    ("the home page carries a private declaration's prose (it is read from the composed file whatever its spokes)",
     '[ "$_allpublic" != 1 ] || _why=$(', 'true; _why=$('),
    ("the home page shows a sealed spoke's machines",
     'if [ "$VIS" = sealed ]; then _w="Spoke', 'if false; then _w="Spoke'),
    ("the home page shows how a sealed spoke spreads files",
     'if [ "$VIS" = sealed ]; then\n      printf \'<p>This spoke is sealed: how', 'if false; then\n      printf \'<p>This spoke is sealed: how'),
    ("the home page's Try it names a command that does not exist",
     'sh bin/orgfs plan examples/licensing.d</pre>', 'sh bin/orgfs planit examples/licensing.d</pre>'),
    ("made.html is left out of the navigation",
     '<li><a href="made.html"$(cur made "$2")>How this was made</a></li>\n', ''),
    ("made.html is not marked as the current page",
     '<a href="made.html"$(cur made "$2")>', '<a href="made.html">'),
    ("made.html loses the scoring rule",
     '<code>spoke|stripe|member</code>', '<code>spoke</code>'),
    ("made.html loses the date on his quotes",
     'Corwin Brust, 2026-11-06, about the first cut', 'Corwin Brust, about the first cut'),
    ("made.html drops its link to the CI log",
     '<a href="/ci/">/ci/</a>', '/ci/'),
    ("made.html drops its link to tests/org_check.el",
     '<a href="https://github.com/inputloop/orgfs/blob/main/tests/org_check.el">tests/org_check.el</a>', 'tests/org_check.el'),
    ("made.html loses the framing section (it opens with the records)",
     '<h2>The origination and curation story</h2>', '<h2>What you get</h2>'),
    ("made.html invents the Org and Emacs versions",
     'with the versions the builder really has. This page does not state those versions: read them in the log.', 'with the versions the builder really has: Org 9.5.5, Emacs 28.2.'),
    ("the home page's link to made.html says nothing of what is there",
     'How this was made, and how Emacs checks it: what we were asked', 'More: what we were asked'),
    ("made.html loses the members and plan tables (the spoke detail stays on the home page only)",
     'for _s in $SPOKES; do load_spoke "$_s"; site_spoke; done', 'for _s in $SPOKES; do load_spoke "$_s"; done'),
    # ---- 0.5.0: orgfs who
    ("who merges two spellings (the identity key is the email alone)",
     'i = n - s + 1; key = pn[i] SUBSEP pe[i]', 'i = n - s + 1; key = pe[i]'),
    ("a commit entry does not link its paths (no link from the commit to the path)",
     'if (s in cstart) for (i = cstart[s]; i <= cend[s]; i++) printf "- Path: %s\\n", xl(fp[i])',
     'if (s in cstart) for (i = cstart[s]; i <= cend[s]; i++) printf "- Path: %s\\n", fp[i]'),
    ("a path heading does not link its commits (no back-link from the path to the commit)",
     'body = body sprintf("- %s %s by %s\\n", d, cl(fs[i]), pl(fo[i]))', 'body = body sprintf("- %s %s by %s\\n", d, cshort[fs[i]], pl(fo[i]))'),
    ("a contributor heading does not link its commits",
     'printf "  - %s %s\\n", cdate[fs[i]], cl(fs[i])', 'printf "  - %s %s\\n", cdate[fs[i]], cshort[fs[i]]'),
    ("a commit entry does not link back to its contributor",
     'printf "- Author: %s\\n- Date: %s\\n", pl(cord[s]), cdate[s]', 'printf "- Author: %s\\n- Date: %s\\n", plabel(cord[s]), cdate[s]'),
    ("a path heading does not link its contributors",
     'cl(fs[i]), pl(fo[i]))', 'cl(fs[i]), plabel(fo[i]))'),
    ("who makes a network call (a fetch before it reads)",
     '  wd_shallow=$(git -C "$wd_repo" rev-parse --is-shallow-repository 2>/dev/null)',
     '  git -C "$wd_repo" fetch --quiet 2>/dev/null || true\n  wd_shallow=$(git -C "$wd_repo" rev-parse --is-shallow-repository 2>/dev/null)'),
    ("an identifier is not made from the whole path (the escape is dropped, so two paths can collide)",
     'else o = o sprintf("_%02x", ORD[c])', 'else o = o "_"'),
    ("who ignores --since",
     '${WHO_SINCE:+"--since=$WHO_SINCE"} ', ''),
    ("a bare date means that day at the current time of day (git's rule), not the whole day",
     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) WHO_SINCE="$WHO_SINCE 00:00:00";;', '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) :;;'),
    ("a subject can open an Org link (the zero-width escape is off)",
     'if ((c == "[" && d == "[") || c == "]" || (c == "<" && d == "<") || (c == "{" && d == "{")) o = o ZW', 'if (0) o = o ZW'),
    ("who.html is left out of the navigation",
     '<li><a href="who.html"$(cur who "$2")>Who worked on this</a></li>\n', ''),
    ("who.html is not marked as the current page",
     '<a href="who.html"$(cur who "$2")>', '<a href="who.html">'),
    ("who.html publishes the path of the build host",
     'who_doc "$_wrepo" "the repository this site was built from" "$1/who.org"', 'who_doc "$_wrepo" "$_wrepo" "$1/who.org"'),
    ("who.html loses its Not checked section",
     '<h2>Not checked</h2>\n<ul>\n<li>That the log is the whole history', '<h2>Notes</h2>\n<ul>\n<li>That the log is the whole history'),
    ("the site reads its own checkout even where --repo names another",
     '  _wrepo=${O_REPO:-}\n  if [ -z "$_wrepo" ]; then', '  _wrepo=\n  if [ -z "$_wrepo" ]; then'),
    # ---- 0.5.0: orgfs stack
    ("a carried atom is tagged SEEN_HERE (a carried fact shown as seen)",
     'NR == 1 { print strip($0) " :CARRIED:"; next }', 'NR == 1 { print strip($0) " :SEEN_HERE:"; next }'),
    ("an atom is carried although the tool's version command now prints something else",
     'if [ -n "$_want" ] && [ "$_want" = "$_got" ]; then', 'if true; then'),
    ("stack makes a network call (a source download before it reads the package)",
     '    st_package "$_t" || true\n    {', '    apt-get source --download-only "$_t" >/dev/null 2>&1 || true\n    st_package "$_t" || true\n    {'),
    ("the gap for the commits that make a version distinct is left out",
     '    st_gap "$_t" commits commits "the commits that make this version distinct"', '    : st_gap "$_t" commits commits "the commits that make this version distinct"'),
    ("licences repeat every License: line (not distinct)",
     'if ($0 != "" && !seen[$0]++) print }\' "$_cf"', 'if ($0 != "") print }\' "$_cf"'),
    ("a seed edited after it was composed is trusted",
     'check_fresh "$_sf" "$O_SEED" 1 || die "stack: the seed', 'check_fresh "$_sf" "$O_SEED" 1 || echo "stack: the seed'),
    ("a carried atom does not say where it was carried from",
     'printf ":CARRIED_FROM: %s\\n:CARRIED_AT: %s\\n", from, now', 'printf ":CARRIED_AT: %s\\n", now'),
    ("the hash of the quoted version text is not kept, so a later run cannot tell the version changed",
     '    [ ! -s "$TMP/st.block" ] || printf \':VERSION_TEXT_SHA256: %s\\n\' "$(sha_file "$TMP/st.block")"\n', '    true\n'),
    ("a carried atom whose tool is gone here does not say it is absent",
     'else printf ":ABSENT_HERE: yes\\n"', 'else printf ""'),
    # ---- 0.5.0: Emacs reads what who and stack wrote
    ("a commit's link to its contributor points at a heading that is not there (Org cannot resolve it)",
     'function pl(o) { return "[[#" pid(o) "][" plabel(o) "]]" }', 'function pl(o) { return "[[#" pid(o) "x][" plabel(o) "]]" }', "emacs"),
    ("a path heading drops its link to the contributor (Org's check of the interlocked triangles)",
     'cl(fs[i]), pl(fo[i]))', 'cl(fs[i]), plabel(fo[i]))', "emacs"),
    ("a carried atom is tagged SEEN_HERE (Org reads the tag)",
     'NR == 1 { print strip($0) " :CARRIED:"; next }', 'NR == 1 { print strip($0) " :SEEN_HERE:"; next }', "emacs"),
    ("story.org is copied onto a work file with cp, which fails on a read-only source",
     '    cat "$1/story.org" > "$2"\n', '    cp "$1/story.org" "$2"\n'),
    # ---- 0.5.x: licence confidence in orgfs stack
    ("a changed input overwrites the old guess instead of appending a new one",
     'cp "$TMP/sc/newguess" "$_new" || die "stack: cannot write $_new"', 'cp "$TMP/sc/newguess" "${_old:-$_new}" || die "stack: cannot write $_new"'),
    ("the old guess is not given its NEXT (a missing back-link)",
     "'BEGIN { done = 0 } !done && $0 == \":END:\" { print \":NEXT: \" nx; done = 1 } { print }'", "'{ print }'"),
    ("the new guess does not name its PREVIOUS (a missing back-link)",
     "if [ -n \"$_old\" ]; then printf ':PREVIOUS: [[file:%s]]\\n' \"${_old##*/}\"; else printf ':PREVIOUS: none\\n'; fi", "printf ':PREVIOUS: none\\n'"),
    ("a re-run with the same inputs writes a new guess anyway",
     '[ "$(prop_of INPUTS_KEY "$_old")" = "$SC_KEY" ]', 'false'),
    ("the inputs key leaves out the file, so a changed copyright file is not a changed input",
     '$SC_B3|$(sha_file "$_cp")|$SC_REFSET")', '$SC_B3|$SC_REFSET")'),
    ("a reference atom is written again over the old one",
     '    [ ! -e "$_ra" ] || continue\n', '    true\n'),
    ("a correction is approved by the person who proposed it",
     'if [ "$(st_name_key "$_by")" = "$(st_name_key "$O_BY")" ]; then', 'if false; then'),
    ("a proposer may approve their own correction by changing the case of their name",
     "st_name_key() { printf '%s' \"$1\" | tr 'A-Z' 'a-z' | tr -s ' '", "st_name_key() { printf '%s' \"$1\" | tr -s ' '"),
    ("a proposed correction outranks the machine guess",
     'if (ZP[b, "STATE"] == "approved") {\n      na++', 'if (ZP[b, "STATE"] != "") {\n      na++'),
    ("an approved correction hides the machine guess in the tool atom",
     '  if (head == "") {\n    printf "Observed: no machine guess: %s\\n", why', '  if (head == "" || best != "") {\n    printf "Observed: no machine guess: %s\\n", why'),
    ("an approved correction hides the machine guess on the site",
     '    if (gh == "") { printf "</dl>', '    if (gh == "" || best != "") { printf "</dl>'),
    ("a stanza holding two licences is reduced to its best match (collapsed to one)",
     '  while (ncomp < 4) {', '  while (ncomp < 1) {'),
    ("a file naming several licences is read as one stanza (collapsed to one)",
     'while (i <= n && !blankish(L[i])) { raw = raw L[i] "\\n"; i++ }', 'while (i <= n) { raw = raw L[i] "\\n"; i++ }'),
    ("the high band stops one word early (a gap of exactly 4 per cent falls to moderate)",
     'if (unc * 100 <= b1 * n) return "high"', 'if (unc * 100 < b1 * n) return "high"'),
    ("the moderate band reaches one word too far (a gap of 10 per cent stays moderate)",
     'if (unc * 100 <= b2 * n) return "moderate"', 'if (unc * 100 <= b2 * n + 100) return "moderate"'),
    ("the low band stops one word early (a gap of exactly 17 per cent becomes a gap)",
     'if (unc * 100 <= b3 * n) return "low"', 'if (unc * 100 < b3 * n) return "low"'),
    ("scoring makes a network call",
     '  sc_run "$_pkg" "$_cp" || { warn', '  curl -s https://example.invalid/licences </dev/null >/dev/null 2>&1 || true\n  sc_run "$_pkg" "$_cp" || { warn'),
    ("scoring asks a local model",
     '  sc_run "$_pkg" "$_cp" || { warn', '  ollama run licence-reader </dev/null >/dev/null 2>&1 || true\n  sc_run "$_pkg" "$_cp" || { warn'),
    ("the guesses sit inside the set of atoms that compose reads",
     "find . \\( -path ./guesses -o -path ./references -o -path ./corrections \\) -prune -o -type f", "find . -type f"),
    ("the copyright-holder lines are scored with the licence words",
     'if (t ~ /^(copyright|\\(c\\))/) { holders++; continue }', 'if (0) { holders++; continue }'),
    # ---- the stack page as the proof of concept (tests/test_live.py)
    ("the site silently falls back to the example stack while it claims to be live",
     'if [ "$_stackpage" = 1 ]; then site_stack "$_out" "$O_STACK"; printf', 'if [ "$_stackpage" = 1 ]; then site_stack "$_out" "$SHARE/../examples/stack.d"; printf'),
    ("an atom read on another machine is called live",
     '[ "$_on" = "$SK_HOST" ] || { SK_WHY=', 'true || { SK_WHY='),
    ("a carried atom is called live",
     '[ "$_tag" = ":SEEN_HERE:" ] || { SK_WHY=', 'true || { SK_WHY='),
    ("an atom read before the build began is called this build's reading",
     '[ "$_an" -ge "$_sn" ] || { SK_WHY=', 'true || { SK_WHY='),
    ("a build run that does not name the commit is linked anyway",
     '[ "${O_BRUN#*Z-}" = "$_h12" ] || { SK_WHY=', 'true || { SK_WHY='),
    ("a build run that is not STAMP-HASH12 is linked anyway",
     "grep -Eq '^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$' ||", "cat > /dev/null ||"),
    ("a live page with no build run named still claims a run",
     'if [ -z "$O_BRUN" ]; then SK_WHY="no build run was named', 'if false; then SK_WHY="no build run was named'),
    ("the live page links a run that is not this build's",
     'The build is <a href=\\"/ci/runs/%s.log\\">run %s</a>', 'The build is <a href=\\"/ci/runs/latest.log\\">run %s</a>'),
    ("the live page links the wrong commit",
     'esc(bcommit), esc(substr(bcommit, 1, 12))', 'esc(brun), esc(substr(brun, 17, 12))'),
    ("made.html states Emacs and Org versions the build did not read",
     '"$(printf \'%s\' "$SK_EMACS" | esc)" "$(printf \'%s\' "$SK_ORG" | esc)"', '"GNU Emacs 28.2" "9.5.5"'),
    ("made.html takes the org_check line from any phase, not the check phase of this run",
     'ph == "check" && /^org_check: (OK|NOT CHECKED|FAILED)/', 'ph != "" && /^org_check: (OK|NOT CHECKED|FAILED)/'),
    ("the home page's link to the machine that built the page says less",
     'See the machine that built this page, and how sure we are about each licence it runs under.</a>', 'See the machine.</a>'),
    ("the commits that make a version distinct are not named as a gap on the stack page",
     'if (k == "commits") line = "Which commits make this version distinct: not read yet (needs the upstream tree)."', 'if (k == "commits") line = "Which commits make this version distinct: read."'),
    ("a tool that cannot be read stops the stack (a gap becomes a failure)",
     '    st_gap "$_t" absent tool "$_t, its version', '    die "stack: $_t is not on PATH"\n    st_gap "$_t" absent tool "$_t, its version'),
    ("a build that reads the stack again wipes the corrections",
     'rm -f "$_dir"/tool-*.org "$_dir/story.org"', 'rm -f "$_dir"/tool-*.org "$_dir/story.org" "$_dir"/corrections/*/*.org'),
    ("make and rsync are left out of the tools it reads",
     'ST_TOOLS="sh awk make git python3 emacs org rsync nginx"', 'ST_TOOLS="sh awk git python3 emacs org nginx"'),
    ("a licence guess section is written one level too deep (Org reads it as a child of nothing)",
     'printf "** licence guess\\n:PROPERTIES:\\n:ROLE: licence-guess', 'printf "*** licence guess\\n:PROPERTIES:\\n:ROLE: licence-guess', "emacs"),
    ("the stack ignores ORGFS_OS_RELEASE and describes the machine running it",
     '_osr=${ORGFS_OS_RELEASE:-/etc/os-release}', '_osr=/etc/os-release'),
]


def pairs(old, new):
    return old if isinstance(old, list) else [(old, new)]


def plant(src, old, new):
    for o, n in pairs(old, new):
        src = src.replace(o, n)
    return src


def unpack(fault):
    return fault[:3] if len(fault) >= 3 else (fault[0], fault[1], None)


def is_emacs(fault):
    return len(fault) == 4 and fault[3] == "emacs"


def have_emacs():
    return shutil.which(os.environ.get("EMACS", "emacs")) is not None


def run_org_check(binpath):
    env = dict(os.environ, ORGFS_BIN=binpath, ORGFS_REQUIRE_EMACS="1")
    p = subprocess.run(["sh", os.path.join(HERE, "org_check.sh")], capture_output=True, env=env, timeout=600)
    return p.returncode, p.stdout.decode() + p.stderr.decode()


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
    if is_emacs(fault):
        if not have_emacs():
            return name, None, ["no emacs"]
        rc, out = run_org_check(copy)
        return name, rc, ["org_check"] if "FAILED" in out else []
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
            ectl = ex.submit(run_org_check, copy) if have_emacs() and any(is_emacs(f) for f in todo) else None
            results = [f.result() for f in futs]
            rc, failing, err = ctl.result()
        if rc != 0:
            print("control FAILED on the untouched copy:", failing)
            print(err[-2000:])
            return 1
        print("control: passed on the untouched copy")
        if ectl is not None:
            erc, eout = ectl.result()
            if erc != 0:
                print("control FAILED on the untouched copy (org_check):", eout[-2000:])
                return 1
            print("control: org_check passed on the untouched copy")
        caught = 0
        notchecked = []
        for name, rc, failing in results:
            if rc is None:
                notchecked.append(name)
                print("NOT CHECKED (no emacs): %s" % name)
            elif rc != 0 and failing:
                caught += 1
                u = sorted(set(failing))
                print("caught: %s  [%s%s]" % (name, ", ".join(u[:3]), " ..." if len(u) > 3 else ""))
            else:
                print("MISSED: %s (rc=%d)" % (name, rc))
        if rng:
            print("PARTIAL (%d of %d in range %s caught; %d not checked; control passed)" % (caught, len(todo), rng, len(notchecked)))
            return 0 if caught + len(notchecked) == len(todo) else 1
        if notchecked:
            print("PROVE-RED NOT COMPLETE (%d caught, %d NOT CHECKED for want of emacs; no verdict)" % (caught, len(notchecked)))
            return 1 if (os.environ.get("CI") or os.environ.get("ORGFS_REQUIRE_EMACS") == "1") else 0
        if caught != len(FAULTS):
            print("PROVE-RED FAILED (%d of %d caught)" % (caught, len(FAULTS)))
            return 1
        print("PROVE-RED OK (%d of %d caught; control passed)" % (caught, len(FAULTS)))
        return 0


if __name__ == "__main__":
    sys.exit(main())
