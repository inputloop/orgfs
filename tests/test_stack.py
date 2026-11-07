#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""`orgfs stack`: the tools the build stands on, by version, with a cache for cold starts.

The tools are stubs the test writes (so the versions, packages and licences are known and can be
changed between runs), and one test reads the real host and checks it against dpkg-query and the
copyright files read here, not by the tool.  Python 3 standard library only.
"""
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_orgfs as T  # noqa: E402

orgfs = T.orgfs


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def atoms_of(d):
    out = {}
    for n in sorted(os.listdir(d)):
        if n.startswith("tool-") and n.endswith(".org"):
            with open(os.path.join(d, n), encoding="utf-8") as f:
                out[n[5:-4]] = f.read()
    return out


def head(text):
    first = text.split("\n", 1)[0]
    m = re.match(r"^\* (\S+)((?: +:[A-Za-z0-9_@]+:)*)$", first)
    assert m, first
    return m.group(1), re.findall(r":([A-Za-z0-9_@]+):", m.group(2))


def props(text):
    out = {}
    lines = text.split("\n")
    assert lines[1] == ":PROPERTIES:"
    for l in lines[2:]:
        if l == ":END:":
            break
        m = re.match(r"^:([A-Z_0-9]+):(?: (.*))?$", l)
        out[m.group(1)] = m.group(2) or ""
    return out


def gap_ids(text):
    return set(re.findall(r"^\*\* gap (\S+)$", text, re.M))


class Stubs:
    """A directory of stub tools (git, python3, emacs, nginx, dpkg-query) and a doc root of copyright files."""

    def __init__(self, td, name, versions, licences, nginx=True):
        self.bin = os.path.join(td, name, "bin")
        self.doc = os.path.join(td, name, "doc")
        self.log = os.path.join(td, name, "calls.log")
        os.makedirs(self.bin)
        os.makedirs(self.doc)
        open(self.log, "w").close()
        self.versions = versions
        self.write("git", 'echo "git version %s"' % versions["git"])
        self.write("python3", 'echo "Python %s"' % versions["python3"])
        self.write("emacs", 'case "$*" in *--eval*) printf "%s\\n/stub/lisp/org.elc\\n";; *) echo "GNU Emacs %s"; echo "Copyright line";; esac' % (versions["org"], versions["emacs"]))
        if nginx:
            self.write("nginx", 'echo "nginx version: nginx/%s" >&2' % versions["nginx"])
        with open(os.path.join(os.path.dirname(self.bin), "table"), "w") as f:
            f.write("\n".join("%s|%s|%s|amd64|https://example.org/%s" % (p, v[0], v[1], p) for p, v in PACKAGES(versions).items()) + "\n")
        self.write("dpkg-query", r'''printf 'dpkg-query %%s %%s\\n' "$1" "$2" >> %s
case "$1" in
  -S) b=${2##*/}
      case $b in org.elc) b=emacs-common;; esac
      case $b in nothing*) echo "dpkg-query: no path found matching pattern $2" >&2; exit 1;; esac
      echo "$b: $2";;
  -W) grep "^$4|" %s || exit 1;;
esac''' % (self.log, os.path.join(os.path.dirname(self.bin), "table")))
        for pkg, lic in licences.items():
            os.makedirs(os.path.join(self.doc, pkg), exist_ok=True)
            with open(os.path.join(self.doc, pkg, "copyright"), "w") as f:
                f.write(lic)
        for n in ("curl", "wget", "ssh", "nc", "ncat", "ftp", "apt-get", "apt", "apt-cache", "pip", "pip3", "rsync"):
            self.write(n, 'echo "%s $*" >> %s; exit 1' % (n, self.log))

    def write(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, stat.S_IRWXU)

    def env(self):
        rest = os.environ["PATH"].split(os.pathsep)
        if not os.path.exists(os.path.join(self.bin, "nginx")):
            # "nginx is gone" must hold on a host that really has one (root's PATH has /usr/sbin)
            rest = [d for d in rest if not os.access(os.path.join(d, "nginx"), os.X_OK)]
        return {"PATH": os.pathsep.join([self.bin] + rest), "ORGFS_DOC": self.doc}


def PACKAGES(v):
    # package -> (version, source field)
    return {
        "git": (v.get("git_pkgver", "1:%s-1stub1" % v["git"]), ""),
        "python3": ("%s-1" % v["python3"], "python3-defaults (%s-1)" % v["python3"]),
        "emacs": ("1:%s+1-3" % v["emacs"], "emacs (1:%s+1-3)" % v["emacs"]),
        "emacs-common": ("1:%s+1-3" % v["emacs"], "emacs"),
        "nginx": ("%s-2" % v["nginx"], ""),
        "dash": ("0.5.11+git20210903+057cd650a4ed-3", ""),
        "gawk": ("1:5.1.0-1", ""),
        "mawk": ("1.3.4.20200120-3", ""),
        "busybox": ("1:1.30.1-7", ""),
    }


V1 = {"git": "9.9.9.stub", "python3": "3.99.1", "emacs": "99.1", "org": "9.99", "nginx": "1.99.0"}
LIC1 = {
    "git": "Format: x\n\nFiles: *\nLicense: GPL-2\n\nFiles: a\nLicense: GPL-2\n\nFiles: b\nLicense: LGPL-2.1+\n text of it\n",
    "python3": "License: PSF-2\n",
    "emacs": "License: GPL-3+\n",
    "emacs-common": "License: GPL-3+\n\nLicense: GFDL-1.3+\n",
    "nginx": "Copyright (c) someone; a free-form file with no License line at all\n",
    "dash": "License: BSD-3-clause\n",
    "gawk": "License: GPL-3+\n",
    "mawk": "License: GPL-2\n",
    "busybox": "License: GPL-2\n",
}


class StackBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name
        cls.s1 = Stubs(cls.td, "s1", V1, LIC1)
        cls.d1 = os.path.join(cls.td, "stack1")
        rc, cls.out1, err = orgfs("stack", cls.d1, env=cls.s1.env())
        assert rc == 0, err
        cls.a1 = atoms_of(cls.d1)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()


class StackTests(StackBase):
    def test_one_atom_per_tool_each_read_here(self):
        self.assertEqual(sorted(self.a1), ["awk", "emacs", "git", "nginx", "org", "python3", "sh"])
        for t, text in self.a1.items():
            name, tags = head(text)
            self.assertEqual((name, tags), (t, ["SEEN_HERE"]), t)
            p = props(text)
            self.assertEqual(p["ORGFS_ATOM"], "tool-%s.org" % t)
            self.assertEqual(p["TOOL"], t)
            self.assertRegex(p["READ_AT"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual(p["READ_ON"], subprocess.run(["uname", "-n"], capture_output=True, text=True).stdout.strip())
            self.assertNotIn("CARRIED_FROM", p)
        self.assertIn("There was no seed", read(os.path.join(self.d1, "story.org")))
        self.assertRegex(self.out1, r"stack: wrote .*: 7 atoms \(7 SEEN_HERE, 0 CARRIED\), \d+ gap records")

    def test_the_version_is_what_the_tool_printed_and_the_command_is_quoted(self):
        p = props(self.a1["git"])
        self.assertEqual(p["VERSION"], "git version 9.9.9.stub")
        self.assertIn("#+BEGIN_EXAMPLE\n$ git --version\ngit version 9.9.9.stub\n#+END_EXAMPLE", self.a1["git"])
        self.assertEqual(props(self.a1["python3"])["VERSION"], "Python 3.99.1")
        self.assertEqual(props(self.a1["emacs"])["VERSION"], "GNU Emacs 99.1")
        self.assertEqual(props(self.a1["org"])["VERSION"], "9.99")
        self.assertIn("(org-release)", self.a1["org"])
        self.assertEqual(props(self.a1["nginx"])["VERSION"], "nginx version: nginx/1.99.0")
        self.assertIn("$ nginx -v\nnginx version: nginx/1.99.0", self.a1["nginx"])
        # the hash of the quoted text is there so a later run can tell whether the version command still says the same
        quoted = re.search(r"#\+BEGIN_EXAMPLE\n(\$ git --version.*?)#\+END_EXAMPLE", self.a1["git"], re.S).group(1)
        self.assertEqual(props(self.a1["git"])["VERSION_TEXT_SHA256"], hashlib.sha256(quoted.encode()).hexdigest())

    def test_sh_and_awk_are_the_ones_found_and_resolved(self):
        sh = props(self.a1["sh"])
        self.assertEqual(sh["COMMAND_PATH"], shutil.which("sh", path=self.s1.env()["PATH"]))
        self.assertEqual(sh["RESOLVED_PATH"], os.path.realpath(sh["COMMAND_PATH"]))
        awk = props(self.a1["awk"])
        want = os.path.realpath(shutil.which(os.environ.get("ORGFS_AWK", "awk"), path=self.s1.env()["PATH"]))
        self.assertEqual(awk["RESOLVED_PATH"], want)

    def test_debian_package_source_package_and_upstream_version_are_derived_from_dpkg_query(self):
        g = props(self.a1["git"])
        self.assertEqual((g["PACKAGE"], g["PACKAGE_VERSION"], g["SOURCE_PACKAGE"], g["UPSTREAM_VERSION"]), ("git", "1:9.9.9.stub-1stub1", "git", "9.9.9.stub"))
        self.assertEqual(g["HOMEPAGE"], "https://example.org/git")
        py = props(self.a1["python3"])
        self.assertEqual((py["PACKAGE_VERSION"], py["SOURCE_PACKAGE"], py["UPSTREAM_VERSION"]), ("3.99.1-1", "python3-defaults", "3.99.1"))
        self.assertIn("$ dpkg-query -S ", self.a1["git"])
        self.assertIn("$ dpkg-query -W -f ", self.a1["git"])
        o = props(self.a1["org"])
        self.assertEqual(o["PACKAGE"], "emacs-common")

    def test_an_upstream_commit_named_by_the_version_is_recorded_not_looked_up(self):
        s = Stubs(self.td, "sgit", dict(V1, git_pkgver="1:2.0+git20210903+057cd650a4ed-3build1"), LIC1)
        d = os.path.join(self.td, "stack-gitcommit")
        rc, so, err = orgfs("stack", d, env=s.env())
        self.assertEqual(rc, 0, err)
        g = props(atoms_of(d)["git"])
        self.assertEqual(g["UPSTREAM_VERSION"], "2.0+git20210903+057cd650a4ed")
        self.assertEqual(g["UPSTREAM_COMMIT"], "057cd650a4ed")
        self.assertNotIn("UPSTREAM_COMMIT", props(self.a1["git"]))       # a version that names no commit gets none

    def test_licences_are_the_license_lines_of_the_copyright_file_distinct_in_file_order(self):
        g = props(self.a1["git"])
        self.assertEqual(g["LICENSES"], "GPL-2; LGPL-2.1+")
        self.assertEqual(g["COPYRIGHT_PATH"], os.path.join(self.s1.doc, "git", "copyright"))
        self.assertIn("#+BEGIN_EXAMPLE\nLicense: GPL-2\nLicense: LGPL-2.1+\n#+END_EXAMPLE", self.a1["git"])
        self.assertEqual(props(self.a1["org"])["LICENSES"], "GPL-3+; GFDL-1.3+")
        # and the tool does not judge: it never says a licence is right or compatible
        for text in self.a1.values():
            self.assertNotRegex(text, r"(?i)\b(compatible|incompatible|violat|allowed|forbidden|safe to)\b")

    def test_what_could_not_be_read_is_a_gap_record_in_the_form_of_the_views(self):
        # nginx's copyright file has no License: line
        self.assertIn("stack-nginx-licence", gap_ids(self.a1["nginx"]))
        self.assertNotIn("LICENSES", props(self.a1["nginx"]))
        for t, text in self.a1.items():
            self.assertIn("stack-%s-commits" % t, gap_ids(text), t)
        # the commits that make a version distinct: NOT CHECKED, with what would close it
        c = re.search(r"^\*\* gap stack-git-commits\n(.*?)^:END:", self.a1["git"], re.S | re.M).group(1)
        self.assertIn(":WHAT_SHAPE: the commits that make this version distinct", c)
        self.assertIn(":HOW: NOT CHECKED (needs the upstream tree; intake is a later slice)", c)
        self.assertIn(":ROUTE: ", c)
        v = re.search(r"^\*\* gap stack-git-vcs\n(.*?)^:END:", self.a1["git"], re.S | re.M).group(1)
        self.assertIn("Vcs-Git", v)
        # the checker the views use accepts them
        comp = os.path.join(self.td, "stack1.org")
        rc, so, err = orgfs("compose", self.d1, "--out", comp)
        self.assertEqual(rc, 0, err)
        rc, so, err = orgfs("routes", comp)
        self.assertEqual(rc, 0, err)
        self.assertRegex(so, r"routes: \d+ gaps, each with a rule, a declaring line, a reach and at least one route")
        self.assertRegex(so, r"gap stack-git-commits \(kind commits\)")

    def test_no_dpkg_means_a_gap_not_a_guess(self):
        bin2 = os.path.join(self.td, "nodpkg")
        os.makedirs(bin2)
        # a PATH that has what orgfs needs and no dpkg-query
        need = "sh awk gawk mawk sed sort cut tr head tail wc cat date uname mktemp rm mkdir cp mv cmp dd grep find basename dirname readlink sha256sum ls printf expr tee dash bash busybox".split()
        for n in need:
            w = shutil.which(n)
            if w and not os.path.exists(os.path.join(bin2, n)):
                os.symlink(w, os.path.join(bin2, n))
        d = os.path.join(self.td, "stack-nodpkg")
        rc, so, err = orgfs("stack", d, env={"PATH": bin2})
        self.assertEqual(rc, 0, err)
        a = atoms_of(d)
        sh = a["sh"]
        self.assertNotIn("PACKAGE", props(sh))
        self.assertIn("stack-sh-package", gap_ids(sh))
        self.assertIn("there is no dpkg-query on this host", sh)
        self.assertEqual(head(a["git"])[1], ["SEEN_HERE"])
        self.assertEqual(props(a["git"])["PRESENT"], "no")
        self.assertIn("stack-git-absent", gap_ids(a["git"]))
        self.assertNotIn("nginx", a)
        self.assertIn("Observed: not found on PATH, so without an atom: nginx.", read(os.path.join(d, "story.org")))

    def test_the_composed_stack_is_what_orgfs_composes_and_orgfs_decomposes(self):
        comp = os.path.join(self.td, "c.org")
        rc, so, err = orgfs("compose", self.d1, "--out", comp)
        self.assertEqual(rc, 0, err)
        back = os.path.join(self.td, "back")
        rc, so, err = orgfs("decompose", comp, back)
        self.assertEqual(rc, 0, err)
        for n, text in self.a1.items():
            with open(os.path.join(back, "tool-%s.org" % n), encoding="utf-8") as f:
                self.assertEqual(f.read(), text)

    def test_no_network_call(self):
        calls = read(self.s1.log).splitlines()
        self.assertTrue(calls)
        for c in calls:
            self.assertRegex(c, r"^dpkg-query (-S|-W) ", c)
        with open(T.BIN) as f:
            src = f.read()
        region = src[src.index("# ------------------------------------------------------------------ stack"):src.index("WHO_HTML_AWK=")]
        code = "\n".join(l for l in region.split("\n") if not l.lstrip().startswith("#"))
        bad = re.search(r"\b(curl|wget|ssh|rsync|ftp|nc|ncat)\b\s|https?://|\bgit (fetch|pull|clone)\b|apt-get (update|install|source)\b[^\"]*>", code)
        self.assertIsNone(bad, bad and bad.group(0))


class SeedTests(StackBase):
    """Cold start: a seed from an earlier run, and what is carried and what is seen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seed = os.path.join(cls.td, "seed.org")
        rc, so, err = orgfs("compose", cls.d1, "--out", cls.seed)
        assert rc == 0, err
        v2 = dict(V1, git="9.9.10.stub")                       # git changed; python3, emacs and org did not
        lic2 = dict(LIC1, python3="License: SOMETHING-ELSE\n", emacs="License: ALSO-CHANGED\n", git="License: GPL-3\n")
        cls.s2 = Stubs(cls.td, "s2", v2, lic2, nginx=False)    # and nginx is gone
        cls.d2 = os.path.join(cls.td, "stack2")
        rc, cls.out2, err = orgfs("stack", cls.d2, "--seed", cls.seed, env=cls.s2.env())
        assert rc == 0, err
        cls.a2 = atoms_of(cls.d2)

    def test_unchanged_tools_are_carried_and_say_what_was_run_again(self):
        for t in ("python3", "emacs", "org"):
            name, tags = head(self.a2[t])
            self.assertEqual(tags, ["CARRIED"], t)
            p = props(self.a2[t])
            self.assertEqual(p["CARRIED_FROM"], "seed.org")
            self.assertRegex(p["CARRIED_AT"], r"^20\d\d-")
            self.assertIn("version command", p["REREAD_HERE"])
            self.assertEqual(p["READ_AT"], props(self.a1[t])["READ_AT"])      # the date it was read stays the seed's
            self.assertEqual(p["READ_ON"], props(self.a1[t])["READ_ON"])

    def test_nothing_carried_is_shown_as_seen_and_no_carried_fact_is_refreshed(self):
        # the host now says python3 is PSF under another name, but the version command is unchanged: the atom is the seed's
        self.assertEqual(props(self.a2["python3"])["LICENSES"], "PSF-2")
        self.assertNotIn("SOMETHING-ELSE", self.a2["python3"])
        self.assertEqual(props(self.a2["emacs"])["LICENSES"], "GPL-3+")
        # a carried atom is the seed's atom: only its tag and the four lines about carrying differ
        for t in ("python3", "emacs", "org"):
            mine = self.a2[t].split("\n")
            theirs = self.a1[t].split("\n")
            strip = lambda ls: [l for l in ls if not re.match(r"^:(CARRIED_FROM|CARRIED_AT|REREAD_HERE|REREAD_AT):", l)]
            mine, theirs = strip(mine), strip(theirs)
            self.assertEqual(mine[0], theirs[0].replace(":SEEN_HERE:", ":CARRIED:"))
            self.assertEqual(mine[1:], theirs[1:], t)
        # and no atom tagged SEEN_HERE holds a fact that only the seed knew
        for t, text in self.a2.items():
            name, tags = head(text)
            if tags == ["SEEN_HERE"]:
                self.assertNotIn("CARRIED_FROM", props(text))
                self.assertNotIn("version 9.9.9.stub", text.replace(props(text).get("SEED_SAID_VERSION", "\0"), ""))
        # the tags are exactly one of the two on every atom
        for t, text in self.a2.items():
            self.assertIn(head(text)[1], (["SEEN_HERE"], ["CARRIED"]), t)

    def test_a_tool_that_changed_is_read_again_in_full_and_the_seeds_claim_is_named_as_the_seeds(self):
        name, tags = head(self.a2["git"])
        self.assertEqual(tags, ["SEEN_HERE"])
        p = props(self.a2["git"])
        self.assertEqual(p["VERSION"], "git version 9.9.10.stub")
        self.assertEqual(p["SEED_SAID_VERSION"], "git version 9.9.9.stub")
        self.assertEqual(p["LICENSES"], "GPL-3")            # read here, now
        self.assertNotIn("CARRIED_FROM", p)

    def test_a_carried_tool_that_is_absent_here_says_so_and_keeps_its_gap(self):
        name, tags = head(self.a2["nginx"])
        self.assertEqual(tags, ["CARRIED"])
        p = props(self.a2["nginx"])
        self.assertEqual(p["ABSENT_HERE"], "yes")
        self.assertNotIn("REREAD_HERE", p)
        self.assertIn("stack-nginx-reread", gap_ids(self.a2["nginx"]))
        self.assertEqual(p["VERSION"], "nginx version: nginx/1.99.0")      # the seed's, still labelled as carried

    def test_the_summary_and_the_story_say_what_came_from_where(self):
        self.assertRegex(self.out2, r"stack: wrote .*: 7 atoms \((\d) SEEN_HERE, (\d) CARRIED\)")
        m = re.search(r"\((\d) SEEN_HERE, (\d) CARRIED\)", self.out2)
        n_seen = sum(1 for t in self.a2.values() if head(t)[1] == ["SEEN_HERE"])
        n_car = sum(1 for t in self.a2.values() if head(t)[1] == ["CARRIED"])
        self.assertEqual((int(m.group(1)), int(m.group(2))), (n_seen, n_car))
        self.assertGreaterEqual(n_car, 4)
        story = read(os.path.join(self.d2, "story.org"))
        self.assertIn("It started from the seed seed.org", story)
        self.assertIn("An atom tagged CARRIED came from that seed", story)

    def test_a_seed_whose_atoms_were_edited_after_it_was_composed_is_refused(self):
        with open(self.seed, encoding="utf-8") as f:
            text = f.read()
        bad = os.path.join(self.td, "tampered.org")
        with open(bad, "w", encoding="utf-8") as f:
            f.write(text.replace(":LICENSES: PSF-2", ":LICENSES: Totally-Free"))
        d = os.path.join(self.td, "stack-t")
        rc, so, err = orgfs("stack", d, "--seed", bad, env=self.s2.env())
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE", err)
        self.assertFalse(os.path.exists(os.path.join(d, "tool-python3.org")))

    def test_the_seed_may_be_a_directory_and_may_be_the_output_directory(self):
        d = os.path.join(self.td, "stack-same")
        shutil.copytree(self.d1, d)
        rc, so, err = orgfs("stack", d, "--seed", d, env=self.s2.env())
        self.assertEqual(rc, 0, err)
        a = atoms_of(d)
        self.assertEqual(head(a["python3"])[1], ["CARRIED"])
        self.assertEqual(head(a["git"])[1], ["SEEN_HERE"])
        self.assertEqual(props(a["python3"])["CARRIED_FROM"], os.path.basename(d))

    def test_carrying_twice_keeps_the_original_reading_and_says_carried(self):
        seed2 = os.path.join(self.td, "seed2.org")
        rc, so, err = orgfs("compose", self.d2, "--out", seed2)
        self.assertEqual(rc, 0, err)
        d3 = os.path.join(self.td, "stack3")
        rc, so, err = orgfs("stack", d3, "--seed", seed2, env=self.s2.env())
        self.assertEqual(rc, 0, err)
        a3 = atoms_of(d3)
        p = props(a3["python3"])
        self.assertEqual(head(a3["python3"])[1], ["CARRIED"])
        self.assertEqual(p["CARRIED_FROM"], "seed2.org")
        self.assertEqual(p["READ_AT"], props(self.a1["python3"])["READ_AT"])
        self.assertEqual(a3["python3"].count(":CARRIED_FROM:"), 1)

    def test_an_atom_of_a_tool_this_version_does_not_know_is_carried_and_not_read(self):
        d = os.path.join(self.td, "stack-extra")
        shutil.copytree(self.d1, d)
        with open(os.path.join(d, "tool-zzz.org"), "w") as f:
            f.write("* zzz :SEEN_HERE:\n:PROPERTIES:\n:ORGFS_ATOM: tool-zzz.org\n:TOOL: zzz\n:VERSION: zzz 1.0\n:END:\nObserved elsewhere.\n")
        story = read(os.path.join(d, "story.org")).replace('#+INCLUDE: "tool-org.org"\n', '#+INCLUDE: "tool-org.org"\n\n#+INCLUDE: "tool-zzz.org"\n')
        with open(os.path.join(d, "story.org"), "w") as f:
            f.write(story)
        rc, so, err = orgfs("stack", os.path.join(self.td, "stack-extra2"), "--seed", d, env=self.s2.env())
        self.assertEqual(rc, 0, err)
        a = atoms_of(os.path.join(self.td, "stack-extra2"))
        self.assertEqual(head(a["zzz"])[1], ["CARRIED"])
        self.assertIn("NOT_REREAD_HERE", props(a["zzz"]))

    def test_a_missing_seed_is_an_error(self):
        rc, so, err = orgfs("stack", os.path.join(self.td, "x"), "--seed", os.path.join(self.td, "nope.org"), env=self.s2.env())
        self.assertNotEqual(rc, 0)
        self.assertIn("cannot read the seed", err)


class RealHostTests(unittest.TestCase):
    """The real host, checked against dpkg-query and the copyright files read by this test."""

    def test_the_package_and_the_licences_agree_with_what_the_host_says(self):
        if shutil.which("dpkg-query") is None:
            self.skipTest("NOT CHECKED: no dpkg-query here")
        with tempfile.TemporaryDirectory() as td:
            rc, so, err = orgfs("stack", td)
            self.assertEqual(rc, 0, err)
            a = atoms_of(td)
            sh = props(a["sh"])
            real = sh["RESOLVED_PATH"]
            self.assertEqual(real, os.path.realpath(shutil.which("sh")))
            owners = None
            for cand in (real, shutil.which("sh"), real[len("/usr"):] if real.startswith("/usr/") else "/usr" + real):
                p = subprocess.run(["dpkg-query", "-S", cand], capture_output=True, text=True)
                if p.returncode == 0:
                    owners = p.stdout.split(":")[0].split(",")[0].strip()
                    break
            if owners is None:
                self.assertIn("stack-sh-package", gap_ids(a["sh"]))
                return
            self.assertEqual(sh["PACKAGE"], owners)
            ver = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", owners], capture_output=True, text=True).stdout
            self.assertEqual(sh["PACKAGE_VERSION"], ver)
            cf = "/usr/share/doc/%s/copyright" % owners.split(":")[0]
            lic = []
            if os.path.exists(cf):
                with open(cf, errors="replace") as fh:
                    for l in fh:
                        m = re.match(r"^License:\s*(.*?)\s*$", l)
                        if m and m.group(1) and m.group(1) not in lic:
                            lic.append(m.group(1))
            if lic:
                self.assertEqual(sh["LICENSES"], "; ".join(lic))
            else:
                self.assertIn("stack-sh-licence", gap_ids(a["sh"]))


if __name__ == "__main__":
    unittest.main()
